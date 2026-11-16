import json
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app import main
from app.auth import get_current_user_id
from app.config import Settings


client = TestClient(main.app)


@pytest.fixture(autouse=True)
def authenticated_user():
    main.app.dependency_overrides[get_current_user_id] = lambda: "resume-ai-setup-user"
    yield
    main.app.dependency_overrides.pop(get_current_user_id, None)


def pdf_with_text() -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
    })
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 72 720 Td (Jane Example) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class FakeResponse:
    def __init__(self, status_code: int, payload: object, text: str | None = None):
        self.status_code = status_code
        self.payload = payload
        self.text = text if text is not None else json.dumps(payload)
        self.headers = {}
        self.is_error = status_code >= 400

    def json(self):
        return self.payload


class FakeAsyncClient:
    responses = []
    calls = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return None

    async def post(self, url, headers, json):
        self.calls.append({"url": url, "request": json})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def configure_ai(monkeypatch, responses):
    FakeAsyncClient.responses = list(responses)
    FakeAsyncClient.calls = []
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(main.settings, "resume_parser_mode", "hybrid")
    monkeypatch.setattr(main.settings, "openrouter_api_key", "test-private-key")
    monkeypatch.setattr(main.settings, "openrouter_model", "mock/resume-model")
    monkeypatch.setattr(main.settings, "openrouter_fallback_models", "")
    monkeypatch.setattr(main.settings, "openrouter_max_retries", 0)
    main._OPENROUTER_JSON_MODE_UNSUPPORTED.clear()


def upload_resume():
    return client.post(
        "/api/resume/parse",
        files={"file": ("resume.pdf", pdf_with_text(), "application/pdf")},
    )


def test_openrouter_key_is_cleaned():
    settings = Settings(_env_file=None, openrouter_api_key='  "Bearer sk-test-value"  ')
    assert settings.openrouter_api_key == "sk-test-value"


def test_backend_env_file_is_first_absolute_candidate():
    env_files = Settings.model_config["env_file"]
    assert Path(env_files[0]).resolve() == Path(__file__).resolve().parents[1] / ".env"
    assert env_files[1] == ".env"


def test_openrouter_defaults_are_single_model_fast_and_without_retries():
    assert Settings.model_fields["openrouter_fallback_models"].default == ""
    assert Settings.model_fields["openrouter_max_retries"].default == 0
    assert Settings.model_fields["resume_ai_timeout_seconds"].default == 30.0


def test_missing_key_uses_local_parser_with_explanatory_note(monkeypatch):
    monkeypatch.setattr(main.settings, "resume_parser_mode", "hybrid")
    monkeypatch.setattr(main.settings, "openrouter_api_key", "")
    response = upload_resume()
    assert response.status_code == 200
    body = response.json()
    assert body["parser"] == "local"
    assert body["parser_note"] == (
        "AI extraction is off because OPENROUTER_API_KEY is not set in backend/.env. "
        "The built-in parser was used."
    )


def test_ai_failure_falls_back_with_real_reason(monkeypatch):
    configure_ai(monkeypatch, [FakeResponse(429, {"error": {"message": "rate limited"}})])
    response = upload_resume()
    assert response.status_code == 200
    body = response.json()
    assert body["parser"] == "local"
    assert "AI extraction failed (OpenRouter rate limit reached" in body["parser_note"]
    assert "built-in parser was used" in body["parser_note"]


def test_ai_success_has_ai_parser_and_empty_note(monkeypatch):
    configure_ai(monkeypatch, [FakeResponse(200, {
        "choices": [{"message": {"content": '{"full_name":"Jane Example"}'}}],
    })])
    response = upload_resume()
    assert response.status_code == 200
    assert response.json()["parser"] == "ai"
    assert response.json()["parser_note"] == ""


def test_parser_status_never_leaks_api_key(monkeypatch):
    configure_ai(monkeypatch, [FakeResponse(200, {"model": "mock/resume-model"})])
    response = client.get("/api/resume/parser-status?test=true")
    assert response.status_code == 200
    assert "test-private-key" not in response.text
    assert response.json()["key_configured"] is True
    assert response.json()["test"]["ok"] is True


def test_parser_status_reports_rejected_key(monkeypatch):
    configure_ai(monkeypatch, [FakeResponse(401, {"error": {"message": "invalid key"}})])
    response = client.get("/api/resume/parser-status?test=true")
    assert response.status_code == 200
    assert response.json()["test"] == {
        "ok": False,
        "detail": "OpenRouter rejected the API key. Check that the key is valid.",
    }


@pytest.mark.parametrize(
    ("status_code", "detail"),
    [
        (402, "OpenRouter reports that credits are required"),
        (403, "OpenRouter denied access."),
        (404, "model name or endpoint was not found"),
        (429, "rate-limited the test request"),
    ],
)
def test_parser_status_reports_provider_rejections(monkeypatch, status_code, detail):
    configure_ai(monkeypatch, [FakeResponse(status_code, {"error": {"message": "provider error"}})])
    response = client.get("/api/resume/parser-status?test=true")
    assert response.status_code == 200
    assert response.json()["test"]["ok"] is False
    assert detail in response.json()["test"]["detail"]


def test_model_rejecting_json_mode_is_retried_without_it(monkeypatch):
    configure_ai(monkeypatch, [
        FakeResponse(
            400,
            {"error": {"message": "response_format json_object is unsupported"}},
            text="response_format json_object is unsupported",
        ),
        FakeResponse(200, {
            "choices": [{"message": {"content": '{"full_name":"Jane Example"}'}}],
        }),
    ])
    response = upload_resume()
    assert response.status_code == 200
    assert response.json()["parser"] == "ai"
    assert len(FakeAsyncClient.calls) == 2
    assert "response_format" in FakeAsyncClient.calls[0]["request"]
    assert "response_format" not in FakeAsyncClient.calls[1]["request"]
