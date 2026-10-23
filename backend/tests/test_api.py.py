import mongomock
from fastapi.testclient import TestClient

from app.main import app
from app.database import get_db
from app.matching import calculate_match


_db = mongomock.MongoClient(tz_aware=True)["test"]
app.dependency_overrides[get_db] = lambda: _db

# Using TestClient without a context manager skips lifespan startup.
client = TestClient(app)


def valid_profile(**overrides):
    profile = {
        "full_name": "Jane Example",
        "email": "jane@example.com",
        "phone": "+91 98765 43210",
        "location": "Bangalore",
        "target_roles": ["Front-End Developer"],
        "skills": ["React"],
        "experience_summary": "Front-end developer.",
        "education": [{"institution": "ABC College", "degree": "BCA"}],
        "projects": [{"name": "NewsApp", "description": "Built a news app."}],
    }
    profile.update(overrides)
    return profile


def test_profile_requires_mandatory_fields():
    response = client.put("/api/profile", json={"full_name": "A", "skills": ["Python"]})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "Email is required" in detail
    assert "Add at least one education entry" in detail


def test_profile_rejects_incomplete_entries_and_bad_email():
    response = client.put(
        "/api/profile",
        json=valid_profile(
            email="not-an-email",
            work_experience=[{"company": "Acme"}],
        ),
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "Email is not a valid address" in detail
    assert "Work experience #1" in detail


def test_profile_full_roundtrip_and_blank_experience_ok_for_freshers():
    response = client.put(
        "/api/profile",
        json=valid_profile(
            achievements=["Won a hackathon"],
            certifications=[{"name": "Python Basics", "issuer": "Infosys"}],
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["achievements"] == ["Won a hackathon"]
    assert body["certifications"][0]["issuer"] == "Infosys"
    assert client.get("/api/profile").json()["projects"][0]["name"] == "NewsApp"


def test_match_basic():
    score, reasons = calculate_match(
        ["Python", "React"],
        ["python", "react", "go"],
    )

    assert score == 66.7
    assert "Potential gap: go" in reasons


def test_match_empty():
    assert calculate_match([], [], "")[0] == 0.0


def test_health():
    response = client.get("/api/health")

    assert response.json()["status"] == "ok"


def test_duplicate_source_id_and_url():
    body = {
        "title": "Dev",
        "company": "A",
        "source": "x",
        "source_job_id": "9",
    }

    assert client.post("/api/jobs", json=body).status_code == 201
    assert client.post("/api/jobs", json=body).status_code == 409

    url_body = {
        "title": "Dev",
        "company": "B",
        "url": "https://example.com/j/1",
    }

    assert client.post("/api/jobs", json=url_body).status_code == 201
    assert client.post("/api/jobs", json=url_body).status_code == 409


def test_application_workflow_and_applied_status_blocked():
    job_response = client.post(
        "/api/jobs",
        json={"title": "T", "company": "C"},
    )
    assert job_response.status_code == 201
    job_id = job_response.json()["id"]

    # Direct submission is blocked until the approval workflow exists.
    response = client.post(
        "/api/applications",
        json={"job_id": job_id, "status": "applied"},
    )
    assert response.status_code == 403

    # New applications cannot start at later stages.
    response = client.post(
        "/api/applications",
        json={"job_id": job_id, "status": "interview"},
    )
    assert response.status_code == 409

    response = client.post(
        "/api/applications",
        json={"job_id": job_id, "status": "offer"},
    )
    assert response.status_code == 409

    # A normal application starts at Saved.
    create_response = client.post(
        "/api/applications",
        json={"job_id": job_id},
    )
    assert create_response.status_code == 201

    application = create_response.json()
    application_id = application["id"]

    assert application["status"] == "saved"
    assert application["allowed_next"] == ["drafting","applied","withdrawn"]

    # Saved cannot jump directly to Interview or Offer.
    response = client.patch(
        f"/api/applications/{application_id}/status",
        params={"status": "interview"},
    )
    assert response.status_code == 409

    response = client.patch(
        f"/api/applications/{application_id}/status",
        params={"status": "offer"},
    )
    assert response.status_code == 409

    # Saved cannot be marked Applied directly.
    response = client.patch(
        f"/api/applications/{application_id}/status",
        params={"status": "applied"},
    )
    assert response.status_code == 403

    # The valid first transition works.
    response = client.patch(
        f"/api/applications/{application_id}/status",
        params={"status": "drafting"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "drafting"
    assert response.json()["allowed_next"] == [
        "needs_review",
        "saved",
        "withdrawn",
    ]


def test_interview_can_progress_to_offer():
    job_response = client.post(
        "/api/jobs",
        json={"title": "Engineer", "company": "Example"},
    )
    job_id = job_response.json()["id"]

    create_response = client.post(
        "/api/applications",
        json={"job_id": job_id},
    )
    application_id = create_response.json()["id"]

    # Move through the permitted stages to Interview.
    for status in ["drafting", "needs_review"]:
        response = client.patch(
            f"/api/applications/{application_id}/status",
            params={"status": status},
        )
        assert response.status_code == 200

    # The approval gate currently prevents moving to Applied.
    response = client.patch(
        f"/api/applications/{application_id}/status",
        params={"status": "applied"},
    )
    assert response.status_code == 403

    # Offer is not valid directly from Needs Review.
    response = client.patch(
        f"/api/applications/{application_id}/status",
        params={"status": "offer"},
    )
    assert response.status_code == 409


def test_profile_rescoring_and_bad_ids():
    job_response = client.post(
        "/api/jobs",
        json={
            "title": "R",
            "company": "Z",
            "skills": ["python"],
        },
    )
    job_id = job_response.json()["id"]

    saved = client.put("/api/profile", json=valid_profile(skills=["Python"]))
    assert saved.status_code == 200

    jobs = {
        job["id"]: job
        for job in client.get("/api/jobs").json()
    }

    assert jobs[job_id]["match_score"] == 100.0

    assert (
        client.post(
            "/api/applications",
            json={"job_id": "nope"},
        ).status_code
        == 404
    )

    assert (
        client.patch(
            "/api/applications/nope/status",
            params={"status": "saved"},
        ).status_code
        == 404
    )