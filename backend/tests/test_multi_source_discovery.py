import mongomock
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app import main, schemas
from app.auth import get_current_user_claims
from app.config import settings
from app.database import UserScopedDatabase, get_db


client = TestClient(main.app)


@pytest.fixture
def discovery_db():
    return mongomock.MongoClient(tz_aware=True)["multi_source_discovery_test"]


def _candidate(db, owner_uid):
    candidate_id = ObjectId()
    db.candidates.insert_one({
        "_id": candidate_id,
        "owner_uid": owner_uid,
        "full_name": "European Candidate",
        "target_roles": ["DevOps Engineer"],
        "skills": ["Python", "Kubernetes"],
        "preferred_location": "Brussels, Belgium",
        "archived": False,
    })
    return candidate_id


def _listing(source, source_job_id=None):
    return {
        "source_job_id": source_job_id or f"{source}-1",
        "title": "DevOps Engineer",
        "company": "Example Europe",
        "location": "Brussels, Belgium",
        "url": {
            "linkedin": "https://www.linkedin.com/jobs/view/123",
            "indeed": "https://www.indeed.com/viewjob?jk=123",
            "glassdoor": "https://www.glassdoor.com/job-listing/123",
            "jobicy": "https://jobicy.com/jobs/123-devops-engineer",
            "arbeitnow": "https://www.arbeitnow.com/jobs/companies/example/devops-engineer",
        }[source],
        "description": (
            "Build and operate reliable cloud infrastructure for European services. "
            "Work with Kubernetes, Python, CI/CD, monitoring, incident response, "
            "security, and deployment automation across production environments."
        ),
        "skills": ["Python", "Kubernetes"],
        "employment_types": ["Full Time"],
        "published_at": "2026-11-12T10:00:00+00:00",
        "work_arrangement": "remote",
        "experience_level": "mid",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "",
        "salary_period": "year",
    }


def test_all_boards_runs_sources_concurrently_and_deduplicates(monkeypatch, discovery_db):
    calls = []

    def search_board(*, site, what, where, **_filters):
        calls.append(site)
        return {"results": [_listing(site)]}

    def search_jobicy(*, what, where, geo):
        calls.append("jobicy")
        return {"results": [_listing("jobicy")], "geo": geo}

    def search_arbeitnow(*, what, where):
        calls.append("arbeitnow")
        return {"results": [_listing("arbeitnow")]}

    monkeypatch.setattr(main, "search_jobspy_jobs", search_board)
    monkeypatch.setattr(main.jobicy, "search_jobicy_jobs", search_jobicy)
    monkeypatch.setattr(main.europe_jobs, "search_arbeitnow_jobs", search_arbeitnow)
    candidate_id = _candidate(discovery_db, "owner")
    previous = main.app.dependency_overrides.get(get_db)
    main.app.dependency_overrides[get_db] = lambda: discovery_db
    try:
        response = client.get(
            "/api/jobs/discover/aggregate",
            params={
                "scope": "all",
                "candidate_id": str(candidate_id),
                "what": "DevOps Engineer",
                "where": "Belgium",
            },
        )
    finally:
        if previous is None:
            main.app.dependency_overrides.pop(get_db, None)
        else:
            main.app.dependency_overrides[get_db] = previous

    assert response.status_code == 200
    payload = response.json()
    assert set(calls) == {*main.SUPPORTED_SITES, "jobicy", "arbeitnow"}
    assert set(payload["successful_sources"]) == {*main.SUPPORTED_SITES, "jobicy", "arbeitnow"}
    assert payload["source_failures"] == []
    assert len(payload["results"]) == 1
    listing = payload["results"][0]
    assert listing["duplicate_status"] == "cross_board_duplicate"
    assert set(listing["available_sources"]) == {*main.SUPPORTED_SITES, "jobicy", "arbeitnow"}
    assert listing["match_score"] >= 0
    schemas.MultiSourceJobDiscoveryResponse.model_validate(payload)


def test_source_failure_keeps_successful_all_board_results(monkeypatch, discovery_db):
    def search_board(*, site, **_kwargs):
        if site == "indeed":
            raise RuntimeError("Indeed unavailable")
        return {"results": [_listing(site)]}

    monkeypatch.setattr(main, "search_jobspy_jobs", search_board)
    monkeypatch.setattr(
        main.jobicy,
        "search_jobicy_jobs",
        lambda **kwargs: {"results": [_listing("jobicy")], "geo": kwargs["geo"]},
    )
    monkeypatch.setattr(
        main.europe_jobs,
        "search_arbeitnow_jobs",
        lambda **_kwargs: {"results": [_listing("arbeitnow")]},
    )
    candidate_id = _candidate(discovery_db, "owner")
    previous = main.app.dependency_overrides.get(get_db)
    main.app.dependency_overrides[get_db] = lambda: discovery_db
    try:
        response = client.get(
            "/api/jobs/discover/aggregate",
            params={
                "scope": "all",
                "candidate_id": str(candidate_id),
                "what": "DevOps",
                "where": "Belgium",
            },
        )
    finally:
        if previous is None:
            main.app.dependency_overrides.pop(get_db, None)
        else:
            main.app.dependency_overrides[get_db] = previous

    assert response.status_code == 200
    assert response.json()["source_failures"] == ["indeed"]
    assert "linkedin" in response.json()["successful_sources"]
    assert response.json()["results"]


def test_ict_jobs_uses_verified_europe_feeds_and_rejects_non_europe_locations(
    monkeypatch, discovery_db
):
    calls = []

    def search_jobicy(**kwargs):
        calls.append(("jobicy", kwargs["geo"]))
        return {"results": [_listing("jobicy")], "geo": kwargs["geo"]}

    def search_arbeitnow(**kwargs):
        calls.append(("arbeitnow", kwargs["where"]))
        return {"results": [_listing("arbeitnow")]}

    def jobspy_should_not_run(**_kwargs):
        raise AssertionError("ICT Jobs only queries the verified European feeds.")

    monkeypatch.setattr(main.jobicy, "search_jobicy_jobs", search_jobicy)
    monkeypatch.setattr(main.europe_jobs, "search_arbeitnow_jobs", search_arbeitnow)
    monkeypatch.setattr(main, "search_jobspy_jobs", jobspy_should_not_run)
    candidate_id = _candidate(discovery_db, "owner")
    previous = main.app.dependency_overrides.get(get_db)
    main.app.dependency_overrides[get_db] = lambda: discovery_db
    try:
        response = client.get(
            "/api/jobs/discover/aggregate",
            params={
                "scope": "ict_jobs",
                "candidate_id": str(candidate_id),
                "what": "DevOps",
                "where": "Brussels",
            },
        )
        outside_europe = client.get(
            "/api/jobs/discover/aggregate",
            params={
                "scope": "ict_jobs",
                "candidate_id": str(candidate_id),
                "what": "DevOps",
                "where": "Bengaluru, India",
            },
        )
    finally:
        if previous is None:
            main.app.dependency_overrides.pop(get_db, None)
        else:
            main.app.dependency_overrides[get_db] = previous

    assert response.status_code == 200
    assert set(response.json()["successful_sources"]) == {"jobicy", "arbeitnow"}
    assert set(calls) == {("jobicy", "belgium"), ("arbeitnow", "Brussels")}
    assert outside_europe.status_code == 200
    assert outside_europe.json()["results"] == []
    assert "limited to Europe" in outside_europe.json()["notice"]
    assert len(calls) == 2


def test_recruiter_and_super_admin_search_and_save_in_private_workspaces(
    monkeypatch,
):
    base = mongomock.MongoClient(tz_aware=True)["multi_source_workspace_test"]
    actor = {"uid": "recruiter-uid", "email": "recruiter@example.com"}
    recruiter_candidate = _candidate(base, actor["uid"])
    admin_uid = "super-admin-uid"
    admin_candidate = _candidate(base, admin_uid)
    previous_db = main.app.dependency_overrides.get(get_db)
    previous_claims = main.app.dependency_overrides.get(get_current_user_claims)
    previous_admins = settings.super_admin_emails
    settings.super_admin_emails = "admin@example.com"
    main.app.dependency_overrides[get_db] = lambda: UserScopedDatabase(base, actor["uid"])
    main.app.dependency_overrides[get_current_user_claims] = lambda: {
        "uid": actor["uid"],
        "email": actor["email"],
    }

    def search_board(*, site, **_kwargs):
        return {"results": [_listing(site)]}

    monkeypatch.setattr(main, "search_jobspy_jobs", search_board)
    monkeypatch.setattr(
        main.jobicy,
        "search_jobicy_jobs",
        lambda **kwargs: {"results": [_listing("jobicy")], "geo": kwargs["geo"]},
    )
    monkeypatch.setattr(
        main.europe_jobs,
        "search_arbeitnow_jobs",
        lambda **_kwargs: {"results": [_listing("arbeitnow")]},
    )

    try:
        for uid, email, candidate_id, role in (
            ("recruiter-uid", "recruiter@example.com", recruiter_candidate, "recruiter"),
            ("super-admin-uid", "admin@example.com", admin_candidate, "super_admin"),
        ):
            actor.update(uid=uid, email=email)
            session = client.get("/api/admin/session")
            assert session.status_code == 200
            assert session.json()["role"] == role

            response = client.get(
                "/api/jobs/discover/aggregate",
                params={
                    "scope": "all",
                    "candidate_id": str(candidate_id),
                    "what": "DevOps",
                    "where": "Belgium",
                },
            )
            assert response.status_code == 200
            listing = response.json()["results"][0]
            saved = client.post(
                "/api/jobs",
                json={
                    "candidate_id": str(candidate_id),
                    "title": listing["title"],
                    "company": listing["company"],
                    "location": listing["location"],
                    "source": listing["source"],
                    "source_job_id": listing["source_job_id"],
                    "url": listing["url"],
                    "description": listing["description"],
                    "skills": listing["skills"],
                    "employment_types": listing["employment_types"],
                    "available_sources": listing["available_sources"],
                    "source_listings": listing["source_listings"],
                    "duplicate_group_id": listing["duplicate_group_id"],
                    "duplicate_status": listing["duplicate_status"],
                    "duplicate_confidence": listing["duplicate_confidence"],
                },
            )
            assert saved.status_code == 201
            assert saved.json()["candidate_id"] == str(candidate_id)

            application = client.post(
                "/api/applications",
                json={
                    "job_id": saved.json()["id"],
                    "candidate_id": str(candidate_id),
                    "status": "saved",
                },
            )
            assert application.status_code == 201
            moved = client.patch(
                f"/api/applications/{application.json()['id']}/status",
                json={"status": "under_review"},
            )
            assert moved.status_code == 200
            assert moved.json()["status"] == "under_review"
            assert moved.json()["candidate_id"] == str(candidate_id)

        assert base.jobs.count_documents({"owner_uid": "recruiter-uid"}) == 1
        assert base.jobs.count_documents({"owner_uid": "super-admin-uid"}) == 1
        assert base.job_duplicate_groups.count_documents({"owner_uid": "recruiter-uid"}) == 1
        assert base.job_duplicate_groups.count_documents({"owner_uid": "super-admin-uid"}) == 1
        assert base.applications.count_documents({"owner_uid": "recruiter-uid"}) == 1
        assert base.applications.count_documents({"owner_uid": "super-admin-uid"}) == 1
    finally:
        settings.super_admin_emails = previous_admins
        if previous_db is None:
            main.app.dependency_overrides.pop(get_db, None)
        else:
            main.app.dependency_overrides[get_db] = previous_db
        if previous_claims is None:
            main.app.dependency_overrides.pop(get_current_user_claims, None)
        else:
            main.app.dependency_overrides[get_current_user_claims] = previous_claims
