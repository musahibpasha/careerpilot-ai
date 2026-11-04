import hashlib

import mongomock
import pytest
from fastapi.testclient import TestClient

from app import jobspy_search
from app.database import get_db
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def discovery_db():
    mongo_client = mongomock.MongoClient(tz_aware=True)
    database = mongo_client["jobspy_search_test"]
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = lambda: database
    yield database
    if previous is not None:
        app.dependency_overrides[get_db] = previous
    else:
        app.dependency_overrides.pop(get_db, None)


class FakeFrame:
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, *, orient):
        assert orient == "records"
        return self.rows


def test_supported_sites_are_exactly_the_four_job_boards():
    assert jobspy_search.SUPPORTED_SITES == (
        "linkedin",
        "naukri",
        "indeed",
        "glassdoor",
    )


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
        return FakeFrame([
            _job(company_logo="https://cdn.example.test/company.png"),
            _job(job_url="https://linkedin.com.evil.example/fake"),
        ])

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
    assert listing["company_logo_url"] == "https://cdn.example.test/company.png"
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
        "easy_apply": None,
        "hours_old": 168,
        "fetch_description": True,
    }]


def test_search_ignores_unsafe_company_logo_urls(monkeypatch):
    monkeypatch.setattr(
        jobspy_search,
        "scrape_jobs",
        lambda **_kwargs: FakeFrame([_job(company_logo="http://127.0.0.1/logo.png")]),
    )

    result = jobspy_search.search_jobspy_jobs(
        site="linkedin",
        what="python engineer",
        where="",
    )

    assert result["results"][0]["company_logo_url"] is None


def test_indeed_search_uses_country_from_location_instead_of_jobspy_us_default(monkeypatch):
    calls = []

    def fake_scrape_jobs(**kwargs):
        calls.append(kwargs)
        return FakeFrame([
            _job(
                title="Software Engineer",
                job_url="https://in.indeed.com/viewjob?jk=india123",
            )
        ])

    monkeypatch.setattr(jobspy_search, "scrape_jobs", fake_scrape_jobs)
    result = jobspy_search.search_jobspy_jobs(
        site="indeed",
        what="Software Engineer",
        where="Bangalore, India",
        hours_old=168,
    )

    assert len(result["results"]) == 1
    assert result["results"][0]["url"] == "https://in.indeed.com/viewjob?jk=india123"
    assert calls == [{
        "site_name": ["indeed"],
        "search_term": "Software Engineer",
        "location": "Bangalore, India",
        "results_wanted": 20,
        "job_type": None,
        "is_remote": None,
        "easy_apply": None,
        "hours_old": 168,
        "fetch_description": False,
        "country_indeed": "india",
    }]


@pytest.mark.parametrize("site", jobspy_search.SUPPORTED_SITES)
def test_search_passes_easy_apply_filter_to_each_board(monkeypatch, site):
    calls = []

    def fake_scrape_jobs(**kwargs):
        calls.append(kwargs)
        return FakeFrame([_job()])

    monkeypatch.setattr(jobspy_search, "scrape_jobs", fake_scrape_jobs)
    jobspy_search.search_jobspy_jobs(
        site=site,
        what="python engineer",
        where="",
        easy_apply=True,
    )

    assert calls[0]["easy_apply"] is True


@pytest.mark.parametrize("site", jobspy_search.SUPPORTED_SITES)
def test_search_applies_selected_listing_filters_for_each_board(monkeypatch, site):
    domain_url = {
        "linkedin": "https://www.linkedin.com/jobs/view/",
        "naukri": "https://www.naukri.com/job-listings-",
        "indeed": "https://www.indeed.com/viewjob?jk=",
        "glassdoor": "https://www.glassdoor.com/job-listing/",
    }[site]
    calls = []

    def row(identifier, **overrides):
        values = {
            "id": identifier,
            "title": "Senior Data Analyst",
            "job_url": f"{domain_url}{identifier}",
            "description": "Experience: 5-10 years of experience. Hybrid role.",
            "job_type": "fulltime",
            "work_from_home_type": "Hybrid - Mumbai",
            "experience_range": "5-10 years",
            "min_amount": 1_200_000,
            "max_amount": 1_800_000,
            "currency": "INR",
            "interval": "yearly",
        }
        values.update(overrides)
        return _job(**values)

    def fake_scrape_jobs(**kwargs):
        calls.append(kwargs)
        return FakeFrame([
            row("match"),
            row("wrong-level", title="Intern Data Analyst", experience_range="Internship"),
            row("wrong-type", job_type="parttime"),
            row("wrong-arrangement", work_from_home_type="Work from office"),
            row("wrong-salary", min_amount=2_500_000, max_amount=3_000_000),
        ])

    monkeypatch.setattr(jobspy_search, "scrape_jobs", fake_scrape_jobs)
    result = jobspy_search.search_jobspy_jobs(
        site=site,
        what="data analyst",
        where="Mumbai, India",
        job_type="fulltime",
        work_arrangement="hybrid",
        experience_level="mid-senior",
        salary_min=1_000_000,
        salary_max=2_000_000,
        salary_currency="INR",
        salary_period="year",
        sort_by="newest",
    )

    assert [job["source_job_id"] for job in result["results"]] == ["match"]
    assert result["results"][0]["experience_level"] == "senior"
    assert result["results"][0]["work_arrangement"] == "hybrid"
    assert calls[0]["fetch_description"] is (site != "naukri")
    assert calls[0]["results_wanted"] == 20


@pytest.mark.parametrize("site", jobspy_search.SUPPORTED_SITES)
def test_remote_filter_rejects_non_remote_results_even_when_board_ignores_it(monkeypatch, site):
    domain_url = {
        "linkedin": "https://www.linkedin.com/jobs/view/",
        "naukri": "https://www.naukri.com/job-listings-",
        "indeed": "https://www.indeed.com/viewjob?jk=",
        "glassdoor": "https://www.glassdoor.com/job-listing/",
    }[site]

    monkeypatch.setattr(
        jobspy_search,
        "scrape_jobs",
        lambda **_kwargs: FakeFrame([
            _job(job_url=f"{domain_url}remote", is_remote=True),
            _job(
                id="onsite",
                title="Python Engineer",
                job_url=f"{domain_url}onsite",
                is_remote=False,
                work_from_home_type="Work from office",
            ),
        ]),
    )
    result = jobspy_search.search_jobspy_jobs(
        site=site,
        what="python engineer",
        where="",
        is_remote=True,
        work_arrangement="remote",
    )

    assert [job["url"] for job in result["results"]] == [f"{domain_url}remote"]


def test_salary_filter_converts_monthly_provider_salary_to_requested_yearly_range(monkeypatch):
    monkeypatch.setattr(
        jobspy_search,
        "scrape_jobs",
        lambda **_kwargs: FakeFrame([
            _job(
                min_amount=100_000,
                max_amount=150_000,
                interval="monthly",
                currency="INR",
            ),
        ]),
    )
    result = jobspy_search.search_jobspy_jobs(
        site="linkedin",
        what="python engineer",
        where="",
        salary_min=1_000_000,
        salary_max=2_000_000,
        salary_currency="INR",
        salary_period="year",
    )

    assert result["results"][0]["salary_min"] == 1_200_000
    assert result["results"][0]["salary_max"] == 1_800_000
    assert result["results"][0]["salary_period"] == "year"


def test_newest_sort_orders_matched_results_by_posted_date(monkeypatch):
    monkeypatch.setattr(
        jobspy_search,
        "scrape_jobs",
        lambda **_kwargs: FakeFrame([
            _job(title="Python Engineer", job_url="https://www.linkedin.com/jobs/view/old",
                 date_posted="2026-01-01"),
            _job(title="Python Engineer", job_url="https://www.linkedin.com/jobs/view/new",
                 date_posted="2026-11-01"),
        ]),
    )
    result = jobspy_search.search_jobspy_jobs(
        site="linkedin",
        what="python engineer",
        where="",
        sort_by="newest",
    )

    assert [job["url"] for job in result["results"]] == [
        "https://www.linkedin.com/jobs/view/new",
        "https://www.linkedin.com/jobs/view/old",
    ]


def test_indeed_country_infers_supported_location_suffixes():
    assert jobspy_search._indeed_country("Bengaluru, Karnataka, India") == "india"
    assert jobspy_search._indeed_country("London, United Kingdom") == "uk"


@pytest.mark.parametrize(
    ("site", "job_url"),
    [
        ("linkedin", "https://www.linkedin.com/jobs/view/123"),
        ("naukri", "https://www.naukri.com/job-listings-123"),
        ("indeed", "https://www.indeed.com/viewjob?jk=123"),
        ("glassdoor", "https://www.glassdoor.com/job-listing/123"),
    ],
)
def test_search_returns_only_titles_matching_all_search_terms(monkeypatch, site, job_url):
    def fake_scrape_jobs(**_kwargs):
        return FakeFrame([
            _job(title="Senior DevOps Engineer", job_url=f"{job_url}-1"),
            _job(
                title="Software Engineer",
                description="Work on DevOps engineering systems.",
                job_url=f"{job_url}-2",
            ),
            _job(title="DevOps Platform Engineer", job_url=f"{job_url}-3"),
            _job(title="DevOps Consultant", job_url=f"{job_url}-4"),
        ])

    monkeypatch.setattr(jobspy_search, "scrape_jobs", fake_scrape_jobs)
    result = jobspy_search.search_jobspy_jobs(
        site=site,
        what="devops engineer",
        where="",
    )

    assert [job["title"] for job in result["results"]] == [
        "Senior DevOps Engineer",
        "DevOps Platform Engineer",
    ]


@pytest.mark.parametrize(
    ("site", "job_url_prefix"),
    [
        ("linkedin", "https://www.linkedin.com/jobs/view/"),
        ("naukri", "https://www.naukri.com/job-listings-"),
        ("indeed", "https://www.indeed.com/viewjob?jk="),
        ("glassdoor", "https://www.glassdoor.com/job-listing/"),
    ],
)
@pytest.mark.parametrize(
    "search_term",
    [
        "frontend developer OR data analyst",
        "frontend developer, data analyst",
        "frontend developer; data analyst",
    ],
)
def test_title_search_runs_each_alternative_and_merges_results(
    monkeypatch, site, job_url_prefix, search_term
):
    calls = []

    def fake_scrape_jobs(**_kwargs):
        calls.append(_kwargs["search_term"])
        return FakeFrame([
            _job(title="Frontend Developer", job_url=f"{job_url_prefix}101"),
            _job(title="Senior Data Analyst", job_url=f"{job_url_prefix}202"),
            _job(title="Frontend Designer", job_url=f"{job_url_prefix}303"),
        ])

    monkeypatch.setattr(jobspy_search, "scrape_jobs", fake_scrape_jobs)
    result = jobspy_search.search_jobspy_jobs(
        site=site,
        what=search_term,
        where="",
    )

    assert calls == ["frontend developer", "data analyst"]
    assert [job["title"] for job in result["results"]] == [
        "Frontend Developer",
        "Senior Data Analyst",
    ]


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
            "easy_apply": "true",
            "work_arrangement": "hybrid",
            "experience_level": "mid-senior",
            "salary_min": "100000",
            "salary_max": "200000",
            "salary_currency": "INR",
            "salary_period": "year",
            "sort_by": "newest",
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
        "easy_apply": True,
        "work_arrangement": "hybrid",
        "experience_level": "mid-senior",
        "salary_min": 100000,
        "salary_max": 200000,
        "salary_currency": "INR",
        "salary_period": "year",
        "sort_by": "newest",
        "hours_old": 24,
    }]
    assert client.get(
        "/api/jobs/discover/board",
        params={"site": "unknown", "what": "engineer"},
    ).status_code == 422
    assert client.get(
        "/api/jobs/discover/board",
        params={"site": "zip_recruiter", "what": "engineer"},
    ).status_code == 422


def test_board_discovery_scores_for_user_and_sorts_by_match_score(monkeypatch, discovery_db):
    profile = {"skills": ["SQL"], "job_titles": ["Data Analyst"]}
    preferences = {"experience_level": "entry"}
    discovery_db.profiles.insert_one(profile)
    discovery_db.preferences.insert_one(preferences)
    score_inputs = []
    scores = {"Lower score": 32, "Higher score": 91}

    def fake_search(**kwargs):
        return {
            "source": "linkedin",
            "results": [
                {"source_job_id": "lower", "title": "Lower score", "company": "Example",
                 "url": "https://example.com/lower", "published_at": "2025-01-02"},
                {"source_job_id": "higher", "title": "Higher score", "company": "Example",
                 "url": "https://example.com/higher", "published_at": "2024-01-02"},
            ],
        }

    def fake_score_fields(job, actual_profile, actual_preferences):
        score_inputs.append((actual_profile, actual_preferences))
        return {
            "match_score": scores[job["title"]],
            "match_reasons": [],
            "match_confidence": "high",
            "match_breakdown": [],
        }

    monkeypatch.setattr("app.main.search_jobspy_jobs", fake_search)
    monkeypatch.setattr("app.main.score_fields", fake_score_fields)

    response = client.get(
        "/api/jobs/discover/board",
        params={"site": "linkedin", "what": "data analyst"},
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert [job["title"] for job in results] == ["Higher score", "Lower score"]
    assert [job["match_score"] for job in results] == [91, 32]
    assert score_inputs == [(profile, preferences), (profile, preferences)]


@pytest.mark.parametrize(
    "filters",
    [
        {"experience_level": "unknown"},
        {"work_arrangement": "remote-ish"},
        {"sort_by": "company"},
        {"salary_min": 200000, "salary_max": 100000, "salary_currency": "INR"},
        {"salary_min": 100000},
    ],
)
def test_board_discovery_rejects_invalid_or_incomplete_filter_values(filters):
    response = client.get(
        "/api/jobs/discover/board",
        params={"site": "linkedin", "what": "data analyst", **filters},
    )

    assert response.status_code == 422


@pytest.mark.parametrize("site", jobspy_search.SUPPORTED_SITES)
def test_board_discovery_endpoint_accepts_each_supported_source(monkeypatch, site):
    monkeypatch.setattr(
        "app.main.search_jobspy_jobs",
        lambda **kwargs: {"source": kwargs["site"], "results": []},
    )

    response = client.get(
        "/api/jobs/discover/board",
        params={"site": site, "what": "software engineer"},
    )

    assert response.status_code == 200
    assert response.json()["source"] == site
