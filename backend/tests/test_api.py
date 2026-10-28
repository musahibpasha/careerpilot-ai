import json
from io import BytesIO

import mongomock
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app import main
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


def test_generate_job_application_materials_uses_profile_and_job(monkeypatch):
    profile = valid_profile(
        skills=["Python", "FastAPI"],
        experience_summary="Backend developer who builds APIs.",
        work_experience=[
            {
                "title": "Developer",
                "company": "Example Co",
                "duration": "2024 - 2025",
                "description": "Built REST APIs with Python.",
            }
        ],
    )
    assert client.put("/api/profile", json=profile).status_code == 200
    job_response = client.post(
        "/api/jobs",
        json={
            "title": "Python Engineer",
            "company": "Hiring Co",
            "description": "Build APIs with Python and FastAPI.",
            "skills": ["Python", "FastAPI"],
        },
    )
    assert job_response.status_code == 201
    job_id = job_response.json()["id"]
    sent_messages = []

    class FakeResponse:
        status_code = 200
        is_error = False

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "cover_letter": "Dear Hiring Manager,\nI build APIs with Python.",
                                    "resume_summary": "Backend developer experienced in Python APIs.",
                                    "skills_to_prioritize": ["Python", "FastAPI", "Kubernetes"],
                                    "bullet_suggestions": [
                                        {
                                            "section": "Developer at Example Co",
                                            "source_text": "Built REST APIs with Python.",
                                            "tailored_text": "Built REST APIs using Python.",
                                        },
                                        {
                                            "section": "Invented role",
                                            "source_text": "Managed a team of 10.",
                                            "tailored_text": "Led a team of 10 engineers.",
                                        },
                                    ],
                                    "caveats": [],
                                }
                            )
                        }
                    }
                ]
            }

    async def fake_completion(client_arg, messages):
        sent_messages.extend(messages)
        return FakeResponse()

    monkeypatch.setattr(main.settings, "openrouter_api_key", "mock-test-key")
    monkeypatch.setattr(main, "_post_with_retry_and_fallback", fake_completion)

    response = client.post(f"/api/jobs/{job_id}/application-materials")

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["cover_letter"].startswith("Dear Hiring Manager")
    assert body["resume_summary"].startswith("Backend developer")
    assert body["skills_to_prioritize"] == ["Python", "FastAPI"]
    assert len(body["bullet_suggestions"]) == 1
    assert body["bullet_suggestions"][0]["source_text"] == "Built REST APIs with Python."
    assert "was omitted" in body["caveats"][0]
    submitted_context = sent_messages[1]["content"]
    assert "Python Engineer" in submitted_context
    assert "Built REST APIs with Python." in submitted_context
    assert "jane@example.com" not in submitted_context
    assert "+91 98765 43210" not in submitted_context


def test_generate_job_application_materials_requires_openrouter_key(monkeypatch):
    job_response = client.post("/api/jobs", json={"title": "Engineer", "company": "Example"})
    assert job_response.status_code == 201
    monkeypatch.setattr(main.settings, "openrouter_api_key", "")

    response = client.post(
        f"/api/jobs/{job_response.json()['id']}/application-materials"
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "OpenRouter API key is not configured."


def test_download_cover_letter_pdf_contains_reviewed_text_and_job_context():
    assert client.put("/api/profile", json=valid_profile()).status_code == 200
    job_response = client.post(
        "/api/jobs",
        json={
            "title": "Python Engineer",
            "company": "Hiring & Co",
        },
    )
    assert job_response.status_code == 201

    response = client.post(
        f"/api/jobs/{job_response.json()['id']}/cover-letter.pdf",
        json={
            "cover_letter": (
                "Dear Hiring Manager,\n\n"
                "I build reliable Python services and would welcome a conversation."
            )
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("attachment; filename=")
    reader = PdfReader(BytesIO(response.content))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "Jane Example" in extracted
    assert "Python Engineer" in extracted
    assert "Hiring & Co" in extracted
    assert "I build reliable Python services" in extracted


def test_download_cover_letter_pdf_rejects_missing_job_and_empty_text():
    missing_job = client.post(
        "/api/jobs/000000000000000000000000/cover-letter.pdf",
        json={"cover_letter": "Dear Hiring Manager"},
    )
    assert missing_job.status_code == 404

    job_response = client.post(
        "/api/jobs",
        json={"title": "Engineer", "company": "Example"},
    )
    empty_letter = client.post(
        f"/api/jobs/{job_response.json()['id']}/cover-letter.pdf",
        json={"cover_letter": ""},
    )
    assert empty_letter.status_code == 422
    whitespace_letter = client.post(
        f"/api/jobs/{job_response.json()['id']}/cover-letter.pdf",
        json={"cover_letter": " \n "},
    )
    assert whitespace_letter.status_code == 422


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


def test_jobicy_listing_metadata_is_saved_and_returned():
    response = client.post(
        "/api/jobs",
        json={
            "title": "Remote Python Engineer",
            "company": "Example Ltd",
            "source": "jobicy",
            "source_job_id": "jobicy-test-123",
            "url": "https://jobicy.com/jobs/jobicy-test-123",
            "employment_types": ["Full-Time"],
            "published_at": "2026-10-27T09:00:00Z",
            "work_arrangement": "remote",
        },
    )

    assert response.status_code == 201
    assert response.json()["employment_types"] == ["Full-Time"]
    assert response.json()["published_at"] == "2026-10-27T09:00:00Z"

    saved = client.get("/api/jobs").json()
    job = next(item for item in saved if item["source_job_id"] == "jobicy-test-123")
    assert job["source"] == "jobicy"
    assert job["published_at"] == "2026-10-27T09:00:00Z"
    assert job["employment_types"] == ["Full-Time"]


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
            "title": "Front-End Developer",
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