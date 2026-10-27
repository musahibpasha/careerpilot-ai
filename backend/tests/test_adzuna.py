import httpx
import pytest
from fastapi.testclient import TestClient

from app import adzuna
from app.main import app


client = TestClient(app)


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeClient:
    response = None
    error = None
    calls = []

    def __init__(self, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get(self, url, *, params, headers):
        self.calls.append({"url": url, "params": params, "headers": headers})
        if self.error:
            raise self.error
        return self.response


@pytest.fixture(autouse=True)
def reset_fake_client(monkeypatch):
    FakeClient.calls = []
    FakeClient.error = None
    FakeClient.response = None
    monkeypatch.setattr(adzuna.httpx, "Client", FakeClient)
    monkeypatch.setattr(adzuna.settings, "adzuna_app_id", "test-app-id")
    monkeypatch.setattr(adzuna.settings, "adzuna_app_key", "test-app-secret")


def test_search_uses_backend_credentials_and_maps_provider_results(monkeypatch):
    FakeClient.response = FakeResponse(
        payload={
            "results": [
                {
                    "id": 123,
                    "title": "Python Engineer",
                    "company": {"display_name": "Example Ltd"},
                    "location": {"display_name": "Bengaluru"},
                    "redirect_url": "https://www.adzuna.co.uk/jobs/land/ad/123",
                    "description": (
                        "A short provider description. Build Python services and operate "
                        "Kubernetes clusters."
                    ),
                },
                {
                    "id": "bad",
                    "title": "Unsafe link",
                    "redirect_url": "javascript:alert(1)",
                },
                {
                    "id": "bad-port",
                    "title": "Invalid port",
                    "redirect_url": "https://adzuna.example:bad/listing",
                },
            ]
        }
    )

    result = adzuna.search_adzuna_jobs(
        country="gb",
        what="python engineer",
        where="Bengaluru",
        page=2,
    )

    assert result["source"] == "adzuna"
    assert result["page"] == 2
    assert result["description_is_snippet"] is True
    assert result["results"] == [
        {
            "source_job_id": "123",
            "title": "Python Engineer",
            "company": "Example Ltd",
            "location": "Bengaluru",
            "url": "https://www.adzuna.co.uk/jobs/land/ad/123",
            "description": (
                "A short provider description. Build Python services and operate "
                "Kubernetes clusters."
            ),
            "skills": ["python", "kubernetes"],
            "nice_to_have_skills": [],
            "work_arrangement": "",
            "experience_level": "",
            "salary_min": None,
            "salary_max": None,
            "salary_currency": "",
            "salary_period": "year",
        }
    ]
    request = FakeClient.calls[0]
    assert request["url"] == f"{adzuna.API_BASE}/jobs/gb/search/2"
    assert request["params"]["app_id"] == "test-app-id"
    assert request["params"]["app_key"] == "test-app-secret"
    assert request["params"]["what"] == "python engineer"
    assert request["params"]["where"] == "Bengaluru"
    assert request["params"]["results_per_page"] == 20
    assert "test-app-secret" not in str(result)


def test_missing_credentials_is_reported_without_request(monkeypatch):
    monkeypatch.setattr(adzuna.settings, "adzuna_app_id", "")
    with pytest.raises(adzuna.AdzunaError, match="ADZUNA_APP_ID") as error:
        adzuna.search_adzuna_jobs(country="gb", what="engineer", where="", page=1)
    assert error.value.status_code == 503
    assert FakeClient.calls == []


def test_discovery_endpoint_reports_missing_credentials(monkeypatch):
    monkeypatch.setattr(adzuna.settings, "adzuna_app_id", "")
    result = client.get("/api/jobs/discover", params={"what": "engineer"})
    assert result.status_code == 503
    assert "backend/.env" in result.json()["detail"]
    assert FakeClient.calls == []


def test_rate_limit_has_actionable_error():
    FakeClient.response = FakeResponse(status_code=429, payload={})
    with pytest.raises(adzuna.AdzunaError, match="rate-limited") as error:
        adzuna.search_adzuna_jobs(country="gb", what="engineer", where="", page=1)
    assert error.value.status_code == 429


def test_timeout_and_unexpected_payload_are_reported():
    FakeClient.error = httpx.TimeoutException("provider timeout")
    with pytest.raises(adzuna.AdzunaError, match="too long") as error:
        adzuna.search_adzuna_jobs(country="gb", what="engineer", where="", page=1)
    assert error.value.status_code == 504

    FakeClient.error = None
    FakeClient.response = FakeResponse(payload={"unexpected": []})
    with pytest.raises(adzuna.AdzunaError, match="unexpected search response"):
        adzuna.search_adzuna_jobs(country="gb", what="engineer", where="", page=1)


def test_discovery_endpoint_validates_inputs_and_returns_transient_results(monkeypatch):
    requested = []
    response = {
        "source": "adzuna",
        "country": "gb",
        "page": 1,
        "results": [
            {
                "source_job_id": "123",
                "title": "Python Engineer",
                "company": "Example Ltd",
                "location": "London",
                "url": "https://www.adzuna.co.uk/jobs/land/ad/123",
                "description": "Snippet.",
            }
        ],
        "has_more": False,
        "description_is_snippet": True,
    }

    def fake_search(**kwargs):
        requested.append(kwargs)
        return response

    monkeypatch.setattr("app.main.search_adzuna_jobs", fake_search)

    result = client.get(
        "/api/jobs/discover",
        params={"what": "python engineer", "where": "London", "country": "gb", "page": 1},
    )

    assert result.status_code == 200
    assert result.json()["results"][0]["source_job_id"] == "123"
    assert requested == [
        {"country": "gb", "what": "python engineer", "where": "London", "page": 1}
    ]
    assert client.get("/api/jobs/discover", params={"what": "a", "country": "gb"}).status_code == 422
    assert client.get("/api/jobs/discover", params={"what": "  ", "country": "gb"}).status_code == 422
    assert client.get(
        "/api/jobs/discover",
        params={"what": "engineer", "country": "gbr"},
    ).status_code == 422
