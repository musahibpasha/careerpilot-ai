import json
from pathlib import Path

import httpx
import pytest

from app import europe_jobs, schemas


FIXTURE = Path(__file__).parent / "fixtures" / "arbeitnow_page.json"


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload
        self.content = json.dumps(payload).encode() if payload is not None else b""

    def json(self):
        return self.payload


class FakeClient:
    response = None
    error = None
    calls = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get(self, url, *, params, headers):
        self.calls.append((url, params, headers, self.kwargs))
        if self.error:
            raise self.error
        return self.response


@pytest.fixture(autouse=True)
def reset_feed(monkeypatch):
    FakeClient.calls = []
    FakeClient.error = None
    FakeClient.response = FakeResponse(payload=json.loads(FIXTURE.read_text(encoding="utf-8")))
    monkeypatch.setattr(europe_jobs.httpx, "Client", FakeClient)
    with europe_jobs._cache_lock:
        europe_jobs._cache.clear()
    yield
    with europe_jobs._cache_lock:
        europe_jobs._cache.clear()


def test_search_filters_to_requested_european_location_and_normalizes_listing():
    result = europe_jobs.search_arbeitnow_jobs(
        what="Python",
        where="Brussels, Belgium",
    )

    assert result["source"] == "arbeitnow"
    assert len(result["results"]) == 1
    listing = result["results"][0]
    assert listing["source_job_id"] == "python-engineer-brussels-1"
    assert listing["title"] == "Python Engineer"
    assert listing["location"] == "Brussels, Belgium"
    assert listing["url"].startswith("https://www.arbeitnow.com/jobs/")
    assert "hidden text" not in listing["description"]
    assert listing["published_at"].isoformat().startswith("2026-11-13T")
    assert listing["skills"] == ["python"]
    assert FakeClient.calls[0][1] == {"page": 1}
    assert FakeClient.calls[0][2]["User-Agent"] == europe_jobs.USER_AGENT
    assert FakeClient.calls[0][3]["follow_redirects"] is False


def test_search_supports_alternatives_and_caches_page_results():
    first = europe_jobs.search_arbeitnow_jobs(
        what="Python OR Data Engineer",
        where="",
    )
    second = europe_jobs.search_arbeitnow_jobs(
        what="Python OR Data Engineer",
        where="",
    )

    assert {job["source_job_id"] for job in first["results"]} == {
        "python-engineer-brussels-1",
        "data-engineer-berlin-2",
    }
    assert second["results"] == first["results"]
    assert len(FakeClient.calls) == 3


def test_search_excludes_ambiguous_emea_location_from_country_search():
    FakeClient.response = FakeResponse(payload={
        "jobs": [{
            "id": 123,
            "url": "https://jobicy.com/jobs/123-emea",
            "jobTitle": "DevOps Engineer",
            "companyName": "Example",
            "jobGeo": "EMEA",
            "jobType": ["Full-Time"],
            "jobDescription": "<p>Build services with Python and Kubernetes.</p>",
        }],
        "hasMore": False,
        "nextCursor": None,
    })

    from app import jobicy

    result = jobicy.search_jobicy_jobs(
        what="DevOps Engineer",
        where="Brussels, Belgium",
        geo="belgium",
    )

    assert result["results"] == []


def test_mapping_uses_a_stable_hash_when_the_feed_has_no_slug():
    item = {
        "title": "Systems Engineer",
        "company_name": "Example",
        "location": "Munich",
        "url": "https://www.arbeitnow.com/jobs/companies/example/systems-engineer",
    }

    first = europe_jobs._map_listing(item)
    second = europe_jobs._map_listing(item)

    assert first["source_job_id"] == second["source_job_id"]
    assert first["location"] == "Munich, Germany"


@pytest.mark.parametrize(
    "item",
    [
        {"title": "Unsafe", "company_name": "Example", "url": "http://arbeitnow.com/jobs/1"},
        {"title": "Unsafe", "company_name": "Example", "url": "https://arbeitnow.com.evil.test/jobs/1"},
        {"title": "No URL", "company_name": "Example", "url": ""},
    ],
)
def test_invalid_listing_urls_are_rejected(item):
    assert europe_jobs._map_listing(item) is None


def test_non_european_listings_and_non_matching_keywords_are_excluded():
    result = europe_jobs.search_arbeitnow_jobs(what="Platform Architect", where="")
    assert result["results"] == []


def test_provider_failures_are_reported():
    FakeClient.error = httpx.TimeoutException("provider timeout")

    with pytest.raises(europe_jobs.EuropeJobsError, match="too long") as error:
        europe_jobs.search_arbeitnow_jobs(what="engineer")
    assert error.value.status_code == 504

    FakeClient.error = None
    FakeClient.response = FakeResponse(status_code=429, payload={})
    with pytest.raises(europe_jobs.EuropeJobsError, match="rate-limited") as error:
        europe_jobs.search_arbeitnow_jobs(what="engineer")
    assert error.value.status_code == 429


def test_source_and_listing_validate_against_discovery_schemas():
    listing = europe_jobs.search_arbeitnow_jobs(what="Python", where="Belgium")["results"][0]
    listing["source"] = "arbeitnow"
    schemas.JobRecommendation.model_validate(listing)
    schemas.JobSourceReference.model_validate({
        "source": "arbeitnow",
        "source_job_id": listing["source_job_id"],
        "url": listing["url"],
    })
