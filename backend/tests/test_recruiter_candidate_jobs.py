from datetime import datetime, timezone

import mongomock
from bson import ObjectId
from fastapi.testclient import TestClient

from app import main
from app.database import UserScopedDatabase, get_db


client = TestClient(main.app)


def make_candidate(name, email, skills, roles, **extra):
    payload = {
        "full_name": name,
        "email": email,
        "phone": "",
        "location": "Austin, TX",
        "preferred_location": "Austin, TX",
        "target_roles": roles,
        "skills": skills,
        "experience_summary": "Recruiter-managed profile.",
        "experience_years": 4,
        "availability": "Immediate",
        "status": "on_bench",
    }
    payload.update(extra)
    response = client.post("/api/candidates", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def make_job(candidate_id=None):
    response = client.post("/api/jobs", json={
        "candidate_id": candidate_id,
        "title": "Python Engineer",
        "company": "Example Systems",
        "location": "Austin, TX",
        "source": "manual",
        "url": "https://example.com/jobs/python-engineer",
        "description": "Build Python backend services.",
        "skills": ["Python", "FastAPI"],
    })
    assert response.status_code == 201, response.text
    return response.json()


def install_db(monkeypatch, user_id="recruiter-one"):
    raw_db = mongomock.MongoClient(tz_aware=True)[f"candidate-jobs-{user_id}"]
    scoped_db = UserScopedDatabase(raw_db, user_id)
    monkeypatch.setitem(main.app.dependency_overrides, get_db, lambda: scoped_db)
    return raw_db, scoped_db


def test_shared_job_saved_and_applied_for_two_candidates_with_candidate_scores(monkeypatch):
    _, _db = install_db(monkeypatch)
    first = make_candidate("Jordan Lee", "jordan@example.com", ["Python", "FastAPI"], ["Python Engineer"])
    second = make_candidate("Morgan Ray", "morgan@example.com", ["Salesforce"], ["Sales Manager"])
    job = make_job(first["id"])

    duplicate_post = client.post("/api/jobs", json={
        "candidate_id": second["id"],
        "title": "Python Engineer",
        "company": "Example Systems",
        "location": "Austin, TX",
        "source": "manual",
        "url": "https://example.com/jobs/python-engineer",
        "description": "Build Python backend services.",
        "skills": ["Python", "FastAPI"],
    })
    assert duplicate_post.status_code == 200
    assert duplicate_post.json()["id"] == job["id"]

    saved_for_second = client.put(
        f"/api/jobs/{job['id']}/saved",
        json={"saved": True, "candidate_id": second["id"]},
    )
    assert saved_for_second.status_code == 200
    assert set(saved_for_second.json()["saved_candidate_ids"]) == {first["id"], second["id"]}

    first_saved = client.get("/api/jobs/saved", params={"candidate_id": first["id"]})
    second_saved = client.get("/api/jobs/saved", params={"candidate_id": second["id"]})
    assert [item["id"] for item in first_saved.json()] == [job["id"]]
    assert [item["id"] for item in second_saved.json()] == [job["id"]]
    assert first_saved.json()[0]["match_score"] > second_saved.json()[0]["match_score"]

    first_application = client.post("/api/applications", json={
        "job_id": job["id"], "status": "saved", "candidate_id": first["id"],
    })
    second_application = client.post("/api/applications", json={
        "job_id": job["id"], "status": "saved", "candidate_id": second["id"],
    })
    assert first_application.status_code == 201
    assert second_application.status_code == 201
    assert first_application.json()["id"] != second_application.json()["id"]

    applied = client.patch(
        f"/api/applications/{first_application.json()['id']}/apply",
        json={"status": "applied", "notes": "Confirmed with employer."},
    )
    assert applied.status_code == 200
    first_list = client.get("/api/jobs/saved", params={"candidate_id": first["id"]})
    second_list = client.get("/api/jobs/saved", params={"candidate_id": second["id"]})
    assert first_list.json() == []
    assert [item["id"] for item in second_list.json()] == [job["id"]]

    first_apps = client.get("/api/applications", params={"candidate_id": first["id"]}).json()
    second_apps = client.get("/api/applications", params={"candidate_id": second["id"]}).json()
    assert len(first_apps) == len(second_apps) == 1
    assert first_apps[0]["candidate_name"] == first["full_name"]
    assert second_apps[0]["candidate_name"] == second["full_name"]


def test_application_status_transition_rules_and_same_status_edits(monkeypatch):
    install_db(monkeypatch)
    candidate = make_candidate("Taylor Quinn", "taylor@example.com", ["Python"], ["Engineer"])
    job = make_job(candidate["id"])
    application = client.post("/api/applications", json={
        "job_id": job["id"], "status": "saved", "candidate_id": candidate["id"],
    }).json()

    invalid_start = client.patch(
        f"/api/applications/{application['id']}/apply",
        json={"status": "placed"},
    )
    assert invalid_start.status_code == 409

    for status in ("applied", "assessment", "interview", "offer_received", "placed"):
        response = client.patch(
            f"/api/applications/{application['id']}/apply",
            json={"status": status, "notes": f"Updated to {status}."},
        )
        assert response.status_code == 200, response.text

    same_status = client.patch(
        f"/api/applications/{application['id']}/apply",
        json={"status": "placed", "notes": "Placement details updated."},
    )
    assert same_status.status_code == 200
    assert same_status.json()["notes"] == "Placement details updated."

    invalid_final = client.patch(
        f"/api/applications/{application['id']}/apply",
        json={"status": "rejected"},
    )
    assert invalid_final.status_code == 409


def test_board_search_is_scored_against_requested_candidate(monkeypatch):
    install_db(monkeypatch)
    python_candidate = make_candidate(
        "Python Candidate",
        "python@example.com",
        ["Python"],
        ["Backend Engineer"],
        programming_languages=["Python"],
        frameworks=["FastAPI"],
        cloud_platforms=["AWS"],
        devops_tools=["Docker"],
        databases=["PostgreSQL"],
    )
    sales_candidate = make_candidate("Sales Candidate", "sales@example.com", ["Salesforce"], ["Sales Manager"])
    search_calls = []

    def fake_search(**kwargs):
        search_calls.append(kwargs)
        return {
            "source": "indeed",
            "results": [{
                "source": "indeed",
                "source_job_id": "python-listing-1",
                "title": "Backend Engineer",
                "company": "Example Co",
                "location": "Austin, TX",
                "url": "https://www.indeed.com/viewjob?jk=python-listing-1",
                "description": "Build Python services using FastAPI.",
                "skills": ["Python", "FastAPI"],
                "employment_types": ["Full-time"],
                "published_at": datetime.now(timezone.utc).isoformat(),
            }],
        }

    monkeypatch.setattr(main, "search_jobspy_jobs", fake_search)
    scores = []
    for candidate in (python_candidate, sales_candidate):
        response = client.get("/api/jobs/discover/board", params={
            "site": "indeed",
            "candidate_id": candidate["id"],
            "what": "Backend Engineer",
            "where": "Austin, TX",
        })
        assert response.status_code == 200, response.text
        scores.append(response.json()["results"][0]["match_score"])
    assert len(search_calls) == 2
    assert scores[0] > scores[1]


def test_recruiter_cannot_search_using_another_recruiters_candidate(monkeypatch):
    raw_db, _ = install_db(monkeypatch, "recruiter-one")
    foreign_id = ObjectId("64f000000000000000000099")
    raw_db.candidates.insert_one({
        "_id": foreign_id,
        "owner_uid": "recruiter-two",
        "full_name": "Foreign Candidate",
        "archived": False,
    })
    response = client.get("/api/jobs/saved", params={"candidate_id": str(foreign_id)})
    assert response.status_code == 404
    assert response.json()["detail"] == "Candidate not found."
