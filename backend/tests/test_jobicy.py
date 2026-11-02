import httpx
import pytest
from fastapi.testclient import TestClient

from app import jobicy
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
    monkeypatch.setattr(jobicy.httpx, "Client", FakeClient)


def _job(**overrides):
    result = {
        "id": 123,
        "url": "https://jobicy.com/jobs/123-python-engineer",
        "jobTitle": "Python Engineer",
        "companyName": "Example Ltd",
        "jobGeo": "India",
        "jobType": ["Full-Time", "Remote"],
        "jobLevel": "Entry",
        "jobDescription": (
            "<h2>Python Engineer</h2><p>Build Python and React services.</p>"
            "<script>ignored skill</script>"
        ),
        "pubDate": "2026-10-27T09:00:00+00:00",
        "salaryMin": 100000,
        "salaryMax": 150000,
        "salaryCurrency": "INR",
        "salaryPeriod": "yearly",
    }
    result.update(overrides)
    return result


def test_search_maps_recent_listings_and_uses_public_api(monkeypatch):
    FakeClient.response = FakeResponse(
        payload={
            "jobs": [_job(), _job(id=124, url="https://example.com/jobs/unsafe")],
            "hasMore": True,
            "nextCursor": "cursor-2",
        }
    )

    result = jobicy.search_jobicy_jobs(
        what="software engineer",
        where="Bengaluru, India",
        geo="apac",
    )

    assert result["source"] == "jobicy"
    assert result["geo"] == "apac"
    assert result["has_more"] is True
    assert result["next_cursor"] == "cursor-2"
    assert len(result["results"]) == 1
    listing = result["results"][0]
    assert listing["source_job_id"] == "123"
    assert listing["title"] == "Python Engineer"
    assert listing["company"] == "Example Ltd"
    assert listing["location"] == "India"
    assert listing["url"] == "https://jobicy.com/jobs/123-python-engineer"
    assert listing["description"] == "Python Engineer Build Python and React services."
    assert listing["skills"] == ["python", "react"]
    assert listing["work_arrangement"] == "remote"
    assert listing["experience_level"] == "entry"
    assert listing["employment_types"] == ["Full-Time", "Remote"]
    assert listing["salary_min"] == 100000
    assert listing["salary_max"] == 150000
    assert listing["salary_currency"] == "INR"
    assert listing["salary_period"] == "year"
    assert FakeClient.calls == [{
        "url": jobicy.API_URL,
        "params": {"count": 20, "geo": "apac", "tag": "software engineer"},
        "headers": {"Accept": "application/json"},
    }]


def test_search_passes_cursor_and_filters_location_locally():
    FakeClient.response = FakeResponse(
        payload={
            "jobs": [
                _job(),
                _job(id=124, jobGeo="Singapore"),
                _job(id=125, jobGeo="Anywhere"),
            ],
            "hasMore": False,
            "nextCursor": None,
        }
    )

    result = jobicy.search_jobicy_jobs(
        what="backend",
        where="Singapore",
        geo="apac",
        cursor="opaque-token",
    )

    assert len(result["results"]) == 2
    assert result["results"][0]["location"] == "Singapore"
    assert result["results"][1]["location"] == "Anywhere"
    assert FakeClient.calls[0]["params"]["cursor"] == "opaque-token"


def test_invalid_source_links_and_unknown_locations_are_not_returned():
    FakeClient.response = FakeResponse(
        payload={
            "jobs": [
                _job(url="http://jobicy.com/jobs/insecure"),
                _job(url="https://jobicy.com.evil.example/jobs/fake"),
                _job(url="https://jobicy.com/jobs/another", jobGeo="Singapore"),
            ],
            "hasMore": False,
            "nextCursor": None,
        }
    )

    result = jobicy.search_jobicy_jobs(
        what="engineer",
        where="India",
        geo="apac",
    )

    assert result["results"] == []


def test_remote_jobs_stay_when_the_source_location_is_broad():
    FakeClient.response = FakeResponse(
        payload={
            "jobs": [
                _job(id=123, jobGeo="Remote"),
                _job(id=124, jobGeo="Singapore"),
                _job(id=125, jobGeo="India"),
            ],
            "hasMore": False,
            "nextCursor": None,
        }
    )

    result = jobicy.search_jobicy_jobs(
        what="engineer",
        where="Bengaluru, India",
        geo="apac",
    )

    assert [listing["location"] for listing in result["results"]] == ["Remote", "India"]


def test_provider_failures_are_reported():
    FakeClient.response = FakeResponse(status_code=429, payload={})
    with pytest.raises(jobicy.JobicyError, match="rate-limited") as error:
        jobicy.search_jobicy_jobs(what="engineer", where="", geo="apac")
    assert error.value.status_code == 429

    FakeClient.response = FakeResponse(payload={"unexpected": []})
    with pytest.raises(jobicy.JobicyError, match="unexpected search response"):
        jobicy.search_jobicy_jobs(what="engineer", where="", geo="apac")

    FakeClient.error = httpx.TimeoutException("provider timeout")
    with pytest.raises(jobicy.JobicyError, match="too long") as error:
        jobicy.search_jobicy_jobs(what="engineer", where="", geo="apac")
    assert error.value.status_code == 504


def test_jobicy_is_not_an_active_discovery_endpoint():
    assert client.get("/api/jobs/discover", params={"what": "engineer"}).status_code == 404
