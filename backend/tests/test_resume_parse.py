import json
import logging
from io import BytesIO

import httpx
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)

from app import main
from app.auth import get_current_user_id


client = TestClient(main.app)


@pytest.fixture(autouse=True)
def mock_authenticated_user():
    main.app.dependency_overrides[get_current_user_id] = lambda: "resume-parse-test-user"
    yield
    main.app.dependency_overrides.pop(get_current_user_id, None)


def pdf_with_text() -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)

    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): font_reference}
            )
        }
    )

    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 72 720 Td (Jane Example) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)

    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class FakeResponse:
    def __init__(self, status_code: int, payload: object, headers=None):
        self.status_code = status_code
        self.payload = payload
        self.headers = headers or {}
        self.is_error = status_code >= 400

    def json(self):
        return self.payload


def mock_openrouter(monkeypatch, response_or_exception):
    calls = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_value, traceback):
            return None

        async def post(self, url, headers, json):
            calls.append({"url": url, "model": json["model"]})
            if isinstance(response_or_exception, Exception):
                raise response_or_exception
            return response_or_exception

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(main.settings, "openrouter_api_key", "mock-test-key")
    monkeypatch.setattr(main.settings, "openrouter_model", "mock/test-model")
    monkeypatch.setattr(main.settings, "openrouter_fallback_models", "")
    monkeypatch.setattr(main.settings, "openrouter_max_retries", 0)
    return calls


def upload_resume(content: bytes | None = None):
    return client.post(
        "/api/resume/parse",
        files={
            "file": (
                "resume.pdf",
                pdf_with_text() if content is None else content,
                "application/pdf",
            )
        },
    )


def test_parse_resume_success_uses_configured_model_and_chat_endpoint(
    monkeypatch,
    caplog,
):
    calls = mock_openrouter(
        monkeypatch,
        FakeResponse(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "full_name": "Jane Example",
                                    "skills": ["Python"],
                                }
                            )
                        }
                    }
                ]
            },
        ),
    )

    with caplog.at_level(logging.INFO, logger=main.__name__):
        response = upload_resume()

    assert response.status_code == 200
    assert response.json()["extracted"]["full_name"] == "Jane Example"
    assert calls == [
        {
            "url": (
                f"{main.settings.openrouter_base_url.rstrip('/')}"
                "/chat/completions"
            ),
            "model": "mock/test-model",
        }
    ]
    assert "Resume upload received" in caplog.text
    assert "PDF text extraction succeeded" in caplog.text
    assert "OpenRouter request started" in caplog.text
    assert "OpenRouter response received status=200 duration_ms=" in caplog.text
    assert "OpenRouter request succeeded status=200" in caplog.text
    assert "Jane Example" not in caplog.text
    assert "mock-test-key" not in caplog.text


def test_parse_resume_without_api_key_does_not_call_openrouter(monkeypatch):
    monkeypatch.setattr(main.settings, "openrouter_api_key", "")

    class NoRequestClient:
        def __init__(self, *args, **kwargs):
            pytest.fail("OpenRouter must not be called without configuration")

    monkeypatch.setattr(main.httpx, "AsyncClient", NoRequestClient)

    response = upload_resume()

    assert response.status_code == 503
    assert response.json()["detail"] == "OpenRouter API key is not configured."


def test_parse_resume_rejects_invalid_pdf_without_calling_openrouter(
    monkeypatch,
    caplog,
):
    monkeypatch.setattr(main.settings, "openrouter_api_key", "mock-test-key")

    class NoRequestClient:
        def __init__(self, *args, **kwargs):
            pytest.fail("Invalid PDFs must not be sent to OpenRouter")

    monkeypatch.setattr(main.httpx, "AsyncClient", NoRequestClient)

    with caplog.at_level(logging.WARNING, logger=main.__name__):
        response = upload_resume(b"not a PDF")

    assert response.status_code == 422
    assert "Could not read this PDF" in response.json()["detail"]
    assert "category=invalid_pdf" in caplog.text


@pytest.mark.parametrize(
    "status_code",
    [
        401,
        402,
        403,
        429,
        500,
    ],
)
def test_parse_resume_logs_provider_error_details(
    monkeypatch,
    caplog,
    status_code,
):
    private_error = "PRIVATE_ERROR_PAYLOAD_DO_NOT_LOG"
    mock_openrouter(
        monkeypatch,
        FakeResponse(
            status_code,
            {"error": {"message": private_error}},
        ),
    )

    with caplog.at_level(logging.WARNING, logger=main.__name__):
        response = upload_resume()

    assert response.status_code == status_code
    assert f"HTTP {status_code}" in response.json()["detail"]
    assert private_error not in response.json()["detail"]
    assert f"status={status_code}" in caplog.text
    assert f"error_message={private_error}" in caplog.text


@pytest.mark.parametrize(
    (
        "limit_source",
        "provider_name",
        "provider_code",
        "error_type",
        "expected_source",
    ),
    [
        ("account", None, None, "rate_limit", "account"),
        (
            "provider",
            "Mock Provider",
            "provider_limit",
            "rate_limit",
            "provider",
        ),
    ],
)
def test_parse_resume_logs_sanitized_429_source_and_headers(
    monkeypatch,
    caplog,
    limit_source,
    provider_name,
    provider_code,
    error_type,
    expected_source,
):
    mock_openrouter(
        monkeypatch,
        FakeResponse(
            429,
            {
                "error": {
                    "message": (
                        "Rate limit for Jane Example "
                        "jane@example.com +1 212 555 0199"
                    ),
                    "metadata": {
                        "error_type": error_type,
                        "limit_source": limit_source,
                        "provider_name": provider_name,
                        "provider_code": provider_code,
                    },
                }
            },
            {
                "Retry-After": "30",
                "X-RateLimit-Limit": "100",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "1800000000",
            },
        ),
    )

    with caplog.at_level(logging.WARNING, logger=main.__name__):
        response = upload_resume()

    assert response.status_code == 429
    assert "OpenRouter rate limit reached" in response.json()["detail"]
    assert (
        "error_message=Rate limit for [redacted] [redacted] "
        "[redacted] [redacted]"
    ) in caplog.text
    assert "error_type=rate_limit" in caplog.text
    assert f"limit_source={expected_source}" in caplog.text
    assert f"provider_name={provider_name}" in caplog.text
    assert f"provider_code={provider_code}" in caplog.text
    assert "retry_after=30" in caplog.text
    assert "rate_limit_limit=100" in caplog.text
    assert "rate_limit_remaining=0" in caplog.text
    assert "rate_limit_reset=1800000000" in caplog.text
    assert "jane@example.com" not in caplog.text
    assert "212 555 0199" not in caplog.text
    assert "Jane" not in caplog.text
    assert "Example" not in caplog.text
    assert "mock-test-key" not in caplog.text


def test_parse_resume_timeout_is_reported_without_exception_details(
    monkeypatch,
    caplog,
):
    private_error = "PRIVATE_TIMEOUT_DETAILS_DO_NOT_LOG"
    mock_openrouter(
        monkeypatch,
        httpx.TimeoutException(private_error),
    )

    with caplog.at_level(logging.WARNING, logger=main.__name__):
        response = upload_resume()

    assert response.status_code == 504
    assert "timed out" in response.json()["detail"].lower()
    assert private_error not in response.json()["detail"]
    assert private_error not in caplog.text
    assert "category=timeout" in caplog.text


def test_parse_resume_rejects_malformed_model_output_without_logging_content(
    monkeypatch,
    caplog,
):
    private_output = "PRIVATE_MODEL_OUTPUT_DO_NOT_LOG"
    mock_openrouter(
        monkeypatch,
        FakeResponse(
            200,
            {
                "choices": [
                    {"message": {"content": private_output}}
                ]
            },
        ),
    )

    with caplog.at_level(logging.WARNING, logger=main.__name__):
        response = upload_resume()

    assert response.status_code == 502
    assert "invalid response" in response.json()["detail"].lower()
    assert private_output not in response.json()["detail"]
    assert private_output not in caplog.text
    assert "category=malformed_model_output" in caplog.text


def test_parse_resume_retries_429_then_falls_back_to_second_model(monkeypatch):
    calls = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers, json):
            calls.append(json["model"])
            if json["model"] == "mock/test-model":
                return FakeResponse(429, {"error": {"message": "busy"}})
            return FakeResponse(
                200,
                {"choices": [{"message": {"content": "{\"full_name\": \"Jane\"}"}}]},
            )

    async def no_sleep(_):
        return None

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(main.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(main.settings, "openrouter_api_key", "mock-test-key")
    monkeypatch.setattr(main.settings, "openrouter_model", "mock/test-model")
    monkeypatch.setattr(main.settings, "openrouter_fallback_models", "mock/backup")
    monkeypatch.setattr(main.settings, "openrouter_max_retries", 1)

    response = upload_resume()

    assert response.status_code == 200
    assert response.json()["extracted"]["full_name"] == "Jane"
    assert calls == ["mock/test-model", "mock/test-model", "mock/backup"]


@pytest.mark.parametrize(
    "reply",
    [
        '```json\n{"full_name": "Jane"}\n```',
        'Here is the result:\n{"full_name": "Jane"}\nHope this helps!',
        '<think>reasoning</think>{"full_name": "Jane"}',
    ],
)
def test_parse_resume_accepts_fenced_or_wrapped_json(monkeypatch, reply):
    mock_openrouter(
        monkeypatch,
        FakeResponse(200, {"choices": [{"message": {"content": reply}}]}),
    )

    response = upload_resume()

    assert response.status_code == 200
    assert response.json()["extracted"]["full_name"] == "Jane"