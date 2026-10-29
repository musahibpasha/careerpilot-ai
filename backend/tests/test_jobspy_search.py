import hashlib

import pytest
from fastapi.testclient import TestClient

from app import jobspy_search
from app.main import app

client = TestClient(app)


class FakeFrame:
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, *, orient):
        assert orient == "records"
        return self.rows


def _job(**overrides):
    row = {
        "id": None,
        "title": "Python Engineer",
        "company": "Example Ltd",
        "location": "Bengaluru, India",
        "job_url": "https://www.linkedin.com/jobs/view/123",
        "description": "Build Python services.",
        "job_type": "fulltime",
        "date_posted": "2026-10-27",
        "is_remote": True,
        "min_amount": 100000,
        "max_amount": 150000,
        "currency": "inr",
        "interval": "yearly",
    }
    row.update(overrides)
    return row


def test_search_normalizes_listings_and_uses_bounded_jobspy_query(monkeypatch):
    calls = []

    def fake_scrape_jobs(**kwargs):
        calls.append(kwargs)
        return FakeFrame([_job(), _job(job_url="https://linkedin.com.evil.example/fake")])

    monkeypatch.setattr(jobspy_search, "scrape_jobs", fake_scrape_jobs)
    result = jobspy_search.search_jobspy_jobs(
        site="linkedin",
        what="python engineer",
        where="Bengaluru, India",
        job_type="fulltime",
        is_remote=True,
        hours_old=168,
    )

    assert result["source"] == "linkedin"
    assert len(result["results"]) == 1
    listing = result["results"][0]
    assert listing["title"] == "Python Engineer"
    assert listing["source_job_id"] == hashlib.sha256(
        listing["url"].encode("utf-8")
    ).hexdigest()[:32]
    assert listing["published_at"] == "2026-10-27T00:00:00"
    assert listing["salary_min"] == 100000
    assert listing["salary_currency"] == "INR"
    assert listing["salary_period"] == "year"
    assert calls == [{
        "site_name": ["linkedin"],
        "search_term": "python engineer",
        "location": "Bengaluru, India",
        "results_wanted": 20,
        "job_type": "fulltime",
        "is_remote": True,
        "hours_old": 168,
    }]


def test_search_keeps_only_supported_employer_application_links(monkeypatch):
    def fake_scrape_jobs(**_kwargs):
        return FakeFrame([
            _job(job_url_direct="https://job-boards.greenhouse.io/acme/jobs/123"),
            _job(
                id="indeed-2",
                job_url="https://www.linkedin.com/jobs/view/456",
                job_url_direct="https://careers.example.com/apply/456",
            ),
        ])

    monkeypatch.setattr(jobspy_search, "scrape_jobs", fake_scrape_jobs)
    result = jobspy_search.search_jobspy_jobs(
        site="linkedin",
        what="engineer",
        where="",
    )

    assert result["results"][0]["application_url"] == "https://job-boards.greenhouse.io/acme/jobs/123"
    assert result["results"][1]["application_url"] is None


def test_search_fails_explicitly_when_jobspy_fails(monkeypatch):
    def fail(**_kwargs):
        raise TimeoutError("provider timed out")

    monkeypatch.setattr(jobspy_search, "scrape_jobs", fail)
    with pytest.raises(jobspy_search.JobSpyError, match="LinkedIn search is unavailable") as error:
        jobspy_search.search_jobspy_jobs(site="linkedin", what="engineer", where="")
    assert error.value.status_code == 502


def test_board_discovery_endpoint_validates_source_and_forwards_filters(monkeypatch):
    requests = []

    def fake_search(**kwargs):
        requests.append(kwargs)
        return {
            "source": "glassdoor",
            "results": [{
                "source_job_id": "job-1",
                "title": "Data Analyst",
                "company": "Example Ltd",
                "url": "https://www.glassdoor.com/job-listing/job-1",
            }],
        }

    monkeypatch.setattr("app.main.search_jobspy_jobs", fake_search)
    response = client.get(
        "/api/jobs/discover/board",
        params={
            "site": "glassdoor",
            "what": "data analyst",
            "where": "Mumbai, India",
            "job_type": "fulltime",
            "is_remote": "true",
            "hours_old": 24,
        },
    )

    assert response.status_code == 200
    assert response.json()["source"] == "glassdoor"
    assert response.json()["results"][0]["title"] == "Data Analyst"
    assert requests == [{
        "site": "glassdoor",
        "what": "data analyst",
        "where": "Mumbai, India",
        "job_type": "fulltime",
        "is_remote": True,
        "hours_old": 24,
    }]
    assert client.get(
        "/api/jobs/discover/board",
        params={"site": "unknown", "what": "engineer"},
    ).status_code == 422
    assert client.get(
        "/api/jobs/discover/board",
        params={"site": "indeed", "what": "x"},
    ).status_code == 422
