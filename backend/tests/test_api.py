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


def set_status(application_id, status, note=None):
    body = {"status": status}
    if note is not None:
        body["note"] = note
    return client.patch(f"/api/applications/{application_id}/status", json=body)


def test_application_workflow_and_gated_statuses_blocked():
    job_response = client.post(
        "/api/jobs",
        json={"title": "T", "company": "C"},
    )
    assert job_response.status_code == 201
    job_id = job_response.json()["id"]

    # Approved and Submitted are locked until the approval workflow exists.
    for locked in ["approved", "submitted"]:
        response = client.post(
            "/api/applications",
            json={"job_id": job_id, "status": locked},
        )
        assert response.status_code == 403

    # The old 'applied' status no longer exists.
    response = client.post(
        "/api/applications",
        json={"job_id": job_id, "status": "applied"},
    )
    assert response.status_code == 422

    # New applications cannot start at later stages.
    for later in ["interview", "offer"]:
        response = client.post(
            "/api/applications",
            json={"job_id": job_id, "status": later},
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
    assert application["allowed_next"] == ["under_review", "withdrawn"]

    # Saved cannot jump directly to Interview or Offer.
    assert set_status(application_id, "interview").status_code == 409
    assert set_status(application_id, "offer").status_code == 409

    # Saved cannot be marked Submitted directly.
    assert set_status(application_id, "submitted").status_code == 403

    # The valid first transition works.
    response = set_status(application_id, "under_review")
    assert response.status_code == 200
    assert response.json()["status"] == "under_review"
    assert response.json()["allowed_next"] == [
        "saved",
        "ready_for_approval",
        "withdrawn",
    ]


def test_cannot_skip_ahead_or_approve():
    job_id = client.post(
        "/api/jobs",
        json={"title": "Engineer", "company": "Example"},
    ).json()["id"]

    application_id = client.post(
        "/api/applications",
        json={"job_id": job_id},
    ).json()["id"]

    for status in ["under_review", "ready_for_approval"]:
        assert set_status(application_id, status).status_code == 200

    # The approval gate blocks Approved and Submitted.
    assert set_status(application_id, "approved").status_code == 403
    assert set_status(application_id, "submitted").status_code == 403

    # Offer is not valid directly from Ready for approval.
    assert set_status(application_id, "offer").status_code == 409


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
            json={"status": "saved"},
        ).status_code
        == 404
    )

# ---------- skill normalisation ----------

from app.matching import normalize_skill


def test_normalize_skill_variants_become_one_skill():
    for spelling in ["React", "React.js", "ReactJS", "react js", " REACT "]:
        assert normalize_skill(spelling) == "react"
    assert normalize_skill("Node.js") == "node"
    assert normalize_skill("NodeJS") == "node"
    assert normalize_skill("JS") == "javascript"
    assert normalize_skill("Postgres") == "postgresql"
    assert normalize_skill("C#") == "c#"


def test_match_treats_skill_spellings_as_the_same():
    score, reasons = calculate_match(["ReactJS", "Node.js"], ["React", "node", "Python"])

    assert score == 66.7
    assert "Matched skill: react" in reasons
    assert "Potential gap: python" in reasons


def test_match_does_not_double_count_duplicate_spellings():
    score, _ = calculate_match(["react"], ["React", "React.js", "ReactJS"])

    assert score == 100.0


def test_match_uses_normalised_description_keywords_when_no_skills_listed():
    score, reasons = calculate_match(["React"], [], "We use ReactJS")

    assert "Matched skill: react" in reasons
    assert score > 0