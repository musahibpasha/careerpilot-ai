from datetime import datetime, timezone
from io import BytesIO
import json
from zipfile import ZipFile

import mongomock
from bson import ObjectId
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.database import UserScopedDatabase, get_db
from app import assistant_routes
from app.main import app


client = TestClient(app)


@pytest.fixture()
def db():
    database = mongomock.MongoClient(tz_aware=True)["candidate_test"]
    scoped_database = UserScopedDatabase(database, "bench-user")
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = lambda: scoped_database
    yield scoped_database
    if previous is not None:
        app.dependency_overrides[get_db] = previous
    else:
        app.dependency_overrides.pop(get_db, None)


def make_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def make_docx() -> bytes:
    output = BytesIO()
    document = b"""<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body><w:p><w:r><w:t>Resume text</w:t></w:r></w:p></w:body>
    </w:document>"""
    with ZipFile(output, "w") as archive:
        archive.writestr("word/document.xml", document)
    return output.getvalue()


def make_candidate_payload(**overrides):
    return {
        "full_name": "Jordan Lee",
        "email": "jordan@example.com",
        "phone": "+1 555 0100",
        "location": "Austin, TX",
        "target_roles": ["Software Engineer"],
        "skills": ["Python", "FastAPI"],
        "experience_summary": "Backend engineer.",
        "experience_years": 4,
        "availability": "15 days",
        "status": "on_bench",
        **overrides,
    }


def make_job():
    response = client.post(
        "/api/jobs",
        json={
            "title": "Backend Engineer",
            "company": "Example Co",
            "location": "Austin, TX",
            "url": "https://example.com/jobs/backend",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_candidate_create_update_and_archive_persist(db):
    created = client.post("/api/candidates", json=make_candidate_payload())
    assert created.status_code == 201, created.text
    candidate_id = created.json()["id"]
    assert created.json()["full_name"] == "Jordan Lee"
    assert created.json()["resume_count"] == 0

    updated = client.put(
        f"/api/candidates/{candidate_id}",
        json=make_candidate_payload(location="Dallas, TX"),
    )
    assert updated.status_code == 200
    assert updated.json()["location"] == "Dallas, TX"

    status = client.patch(
        f"/api/candidates/{candidate_id}/status",
        json={"status": "active"},
    )
    assert status.status_code == 200
    assert status.json()["status"] == "active"

    notes = client.patch(
        f"/api/candidates/{candidate_id}/notes",
        json={"notes": "Follow up next week."},
    )
    assert notes.status_code == 200

    assert client.get(f"/api/candidates/{candidate_id}").json()["notes"] == "Follow up next week."
    archived = client.patch(
        f"/api/candidates/{candidate_id}/archive",
        json={"archived": True},
    )
    assert archived.status_code == 200
    assert archived.json() == {"archived": True}
    assert client.get("/api/candidates").json()["items"] == []
    archived_list = client.get("/api/candidates?include_archived=true").json()
    assert archived_list["items"][0]["archived"] is True
    assert archived_list["summary"]["archived"] == 1


def test_candidate_summary_active_includes_unarchived_pipeline_statuses(db):
    for index, status in enumerate(
        ("active", "on_bench", "under_review", "temporarily_unavailable", "not_set", "placed")
    ):
        response = client.post(
            "/api/candidates",
            json=make_candidate_payload(
                full_name=f"Candidate {index}",
                email=f"candidate{index}@example.com",
                status=status,
            ),
        )
        assert response.status_code == 201, response.text

    summary = client.get("/api/candidates").json()["summary"]

    assert summary["total"] == 6
    assert summary["active"] == 5
    assert summary["on_bench"] == 1
    assert summary["under_review"] == 1


def test_candidate_list_has_no_fixed_500_candidate_cap(db):
    db.candidates.insert_many([
        {
            **make_candidate_payload(
                full_name=f"Candidate {index}",
                email=f"candidate{index}@example.com",
            ),
            "archived": False,
        }
        for index in range(501)
    ])

    response = client.get("/api/candidates")

    assert response.status_code == 200
    assert len(response.json()["items"]) == 501
    assert response.json()["summary"]["total"] == 501


def test_candidate_email_duplicates_are_rejected_case_insensitively(db):
    created = client.post("/api/candidates", json=make_candidate_payload())
    assert created.status_code == 201
    duplicate = client.post(
        "/api/candidates",
        json=make_candidate_payload(full_name="Morgan Lee", email="JORDAN@example.com"),
    )
    assert duplicate.status_code == 409
    assert "email already exists" in duplicate.json()["detail"]


def test_candidate_resume_upload_download_and_detail(db):
    created = client.post("/api/candidates", json=make_candidate_payload())
    candidate_id = created.json()["id"]

    data = make_pdf()
    uploaded = client.post(
        "/api/resumes",
        data={
            "candidate_id": candidate_id,
            "extracted_data": json.dumps({
                "full_name": "Jordan Lee",
                "email": "jordan@example.com",
                "skills": ["Python", "FastAPI"],
                "experience_summary": "Backend engineer.",
            }),
        },
        files={"file": ("jordan.pdf", data, "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    resume_id = uploaded.json()["id"]
    assert uploaded.json()["filename"] == "jordan.pdf"

    detail = client.get(f"/api/candidates/{candidate_id}")
    assert detail.status_code == 200
    assert detail.json()["resume_count"] == 1
    assert detail.json()["resumes"][0]["id"] == resume_id
    assert detail.json()["resumes"][0]["extracted"]["skills"] == ["Python", "FastAPI"]
    assert detail.json()["resume_extracted"]["experience_summary"] == "Backend engineer."
    assert client.get("/api/candidates").json()["summary"]["resumes_uploaded"] == 1

    downloaded = client.get(f"/api/resumes/{resume_id}/file")
    assert downloaded.status_code == 200
    assert downloaded.content == data


def test_unassigned_resume_can_be_explicitly_attached_to_candidate(db):
    uploaded = client.post(
        "/api/resumes",
        files={"file": ("unassigned.pdf", make_pdf(), "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    resume_id = uploaded.json()["id"]
    assert uploaded.json()["candidate_id"] is None

    candidate = client.post("/api/candidates", json=make_candidate_payload())
    assert candidate.status_code == 201, candidate.text
    candidate_id = candidate.json()["id"]
    assert candidate.json()["resume_count"] == 0
    assert client.get("/api/candidates").json()["summary"]["resumes_uploaded"] == 0

    attached = client.post(
        f"/api/candidates/{candidate_id}/resumes/{resume_id}/attach",
    )
    assert attached.status_code == 200, attached.text
    assert attached.json()["candidate_id"] == candidate_id

    detail = client.get(f"/api/candidates/{candidate_id}")
    assert detail.status_code == 200
    assert detail.json()["resume_count"] == 1
    assert detail.json()["resumes"][0]["id"] == resume_id
    assert client.get("/api/candidates").json()["summary"]["resumes_uploaded"] == 1

    other_candidate = client.post(
        "/api/candidates",
        json=make_candidate_payload(
            full_name="Morgan Lee",
            email="morgan@example.com",
        ),
    ).json()
    conflict = client.post(
        f"/api/candidates/{other_candidate['id']}/resumes/{resume_id}/attach",
    )
    assert conflict.status_code == 409


def test_candidate_resume_limit_is_five_for_upload_and_attachment(db):
    candidate = client.post("/api/candidates", json=make_candidate_payload()).json()
    candidate_id = candidate["id"]

    for index in range(5):
        uploaded = client.post(
            "/api/resumes",
            data={"candidate_id": candidate_id},
            files={"file": (f"jordan-{index}.pdf", make_pdf(), "application/pdf")},
        )
        assert uploaded.status_code == 201, uploaded.text

    sixth_upload = client.post(
        "/api/resumes",
        data={"candidate_id": candidate_id},
        files={"file": ("jordan-6.pdf", make_pdf(), "application/pdf")},
    )
    assert sixth_upload.status_code == 409
    assert "maximum of 5 resumes" in sixth_upload.json()["detail"]

    unassigned = client.post(
        "/api/resumes",
        files={"file": ("unassigned.pdf", make_pdf(), "application/pdf")},
    )
    assert unassigned.status_code == 201, unassigned.text
    attach_over_limit = client.post(
        f"/api/candidates/{candidate_id}/resumes/{unassigned.json()['id']}/attach",
    )
    assert attach_over_limit.status_code == 409
    assert "maximum of 5 resumes" in attach_over_limit.json()["detail"]


def test_candidate_can_store_and_download_original_docx(db):
    candidate = client.post("/api/candidates", json=make_candidate_payload()).json()
    data = make_docx()
    uploaded = client.post(
        "/api/resumes",
        data={"candidate_id": candidate["id"]},
        files={
            "file": (
                "jordan.docx",
                data,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["filename"] == "jordan.docx"
    downloaded = client.get(f"/api/resumes/{uploaded.json()['id']}/file")
    assert downloaded.status_code == 200
    assert downloaded.content == data
    assert downloaded.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_candidate_structured_resume_fields_persist(db):
    created = client.post(
        "/api/candidates",
        json=make_candidate_payload(
            alternate_phone="+1 555 0199",
            skill_categories={"cloud": ["AWS", "Azure"]},
            work_experience=[
                {"company": "ThoughtLabs", "title": "DevOps Engineer", "technologies": ["Kubernetes"]},
                {"company": "Example Bank", "title": "Systems Engineer"},
            ],
            education=[{"degree": "MSc", "institution": "Example University", "graduation_year": "2020"}],
            certifications=[{"name": "Cloud Certificate", "credential_id": "CERT-123"}],
            training=["Kubernetes administration"],
            willing_to_relocate=True,
        ),
    )
    assert created.status_code == 201, created.text
    candidate = client.get(f"/api/candidates/{created.json()['id']}").json()
    assert candidate["alternate_phone"] == "+1 555 0199"
    assert candidate["skill_categories"] == {"cloud": ["AWS", "Azure"]}
    assert len(candidate["work_experience"]) == 2
    assert candidate["work_experience"][0]["technologies"] == ["Kubernetes"]
    assert candidate["education"][0]["graduation_year"] == "2020"
    assert candidate["certifications"][0]["credential_id"] == "CERT-123"
    assert candidate["training"] == ["Kubernetes administration"]
    assert candidate["willing_to_relocate"] is True


def test_candidate_create_normalizes_common_ai_extraction_shapes(db):
    created = client.post(
        "/api/candidates",
        json=make_candidate_payload(
            skill_categories={"Cloud": "AWS, Azure", "Networking": 42},
            work_experience=[{
                "company": "ThoughtLabs",
                "title": "DevOps Engineer",
                "start_date": 2021,
                "technologies": "Kubernetes, Terraform",
                "responsibilities": "Built CI/CD pipelines",
            }],
            education=[{
                "degree": "Master of Science",
                "institution": "Example University",
                "graduation_year": 2020,
            }],
            certifications=[{
                "name": "Cloud Certificate",
                "expiry_date": None,
            }],
            projects=[{
                "name": "Cloud migration",
                "technologies": ["AWS", "Terraform"],
            }],
            training="Kubernetes administration",
        ),
    )

    assert created.status_code == 201, created.text
    result = created.json()
    assert result["skill_categories"]["Networking"] == ["42"]
    assert result["work_experience"][0]["start_date"] == "2021"
    assert result["work_experience"][0]["responsibilities"] == ["Built CI/CD pipelines"]
    assert result["education"][0]["graduation_year"] == "2020"
    assert result["certifications"][0]["expiry_date"] == ""
    assert result["projects"][0]["technologies"] == "AWS, Terraform"
    assert result["training"] == ["Kubernetes administration"]


def test_candidate_create_normalizes_malformed_ai_scalar_values(db):
    created = client.post(
        "/api/candidates",
        json=make_candidate_payload(
            full_name=["Jordan Lee"],
            phone=None,
            location={"city": "Austin"},
            experience_years="about 5 years",
            relevant_experience_years="3.5",
            expected_salary="not disclosed",
            willing_to_relocate="yes",
            professional_headline=["Platform", "Engineer"],
            recruiter_notes={"note": "internal"},
            skills=["Python", None, 42, {"skill": "unknown"}],
        ),
    )

    assert created.status_code == 201, created.text
    result = created.json()
    assert result["full_name"] == "Jordan Lee"
    assert result["phone"] == ""
    assert result["location"] == ""
    assert result["experience_years"] is None
    assert result["relevant_experience_years"] == 3.5
    assert result["expected_salary"] is None
    assert result["willing_to_relocate"] is True
    assert result["professional_headline"] == "Platform, Engineer"
    assert result["recruiter_notes"] == ""
    assert result["skills"] == ["Python", "42"]


def test_candidate_applications_are_real_records_and_not_saved_jobs(db):
    candidate = client.post("/api/candidates", json=make_candidate_payload()).json()
    job = make_job()

    tracked = client.post(
        f"/api/candidates/{candidate['id']}/applications",
        json={"job_id": job["id"], "status": "saved"},
    )
    assert tracked.status_code == 201, tracked.text
    assert tracked.json()["candidate_id"] == candidate["id"]

    details = client.get(f"/api/candidates/{candidate['id']}").json()
    assert len(details["applications"]) == 1
    assert details["applications"][0]["job_title"] == "Backend Engineer"
    assert details["applications"][0]["url"] == "https://example.com/jobs/backend"

    # Linking an existing application from another candidate is explicitly rejected.
    another = client.post(
        "/api/candidates",
        json=make_candidate_payload(
            full_name="Morgan Lee",
            email="morgan@example.com",
        ),
    ).json()
    conflict = client.post(
        f"/api/candidates/{another['id']}/applications",
        json={"job_id": job["id"], "status": "saved"},
    )
    assert conflict.status_code == 409


def test_saved_job_and_application_keep_their_candidate_association(db):
    candidate = client.post("/api/candidates", json=make_candidate_payload()).json()
    another = client.post(
        "/api/candidates",
        json=make_candidate_payload(full_name="Morgan Lee", email="morgan@example.com"),
    ).json()
    created = client.post(
        "/api/jobs",
        json={
            "candidate_id": candidate["id"],
            "title": "Backend Engineer",
            "company": "Example Co",
            "url": "https://example.com/jobs/backend",
        },
    )
    assert created.status_code == 201, created.text
    job = created.json()
    assert job["candidate_id"] == candidate["id"]

    conflict = client.put(
        f"/api/jobs/{job['id']}/saved",
        json={"saved": True, "candidate_id": another["id"]},
    )
    assert conflict.status_code == 409
    assert client.get("/api/jobs").json()[0]["candidate_id"] == candidate["id"]

    conflict = client.post(
        "/api/applications",
        json={
            "job_id": job["id"],
            "status": "saved",
            "candidate_id": another["id"],
        },
    )
    assert conflict.status_code == 409

    tracked = client.post(
        "/api/applications",
        json={"job_id": job["id"], "status": "saved"},
    )
    assert tracked.status_code == 201, tracked.text
    assert tracked.json()["candidate_id"] == candidate["id"]


def test_candidate_job_rescore_uses_candidate_not_recruiter_profile(db):
    db.profiles.insert_one(
        {
            "target_roles": ["Backend Engineer"],
            "skills": ["Kubernetes"],
            "location": "Austin, TX",
        }
    )
    candidate = client.post(
        "/api/candidates",
        json=make_candidate_payload(
            target_roles=["Software Engineer"],
            skills=["Python"],
        ),
    ).json()
    job = client.post(
        "/api/jobs",
        json={
            "candidate_id": candidate["id"],
            "title": "Backend Engineer",
            "company": "Example Co",
            "location": "Austin, TX",
            "skills": ["Kubernetes"],
        },
    )
    assert job.status_code == 201, job.text

    rescored = client.post("/api/jobs/rescore")
    assert rescored.status_code == 200
    result = client.get("/api/jobs").json()[0]
    skill_factor = next(
        factor for factor in result["match_breakdown"] if factor["key"] == "skills"
    )
    assert skill_factor["details"]["matched"] == []
    assert skill_factor["details"]["missing_must_have"] == ["kubernetes"]


def test_candidate_application_assistant_uses_only_its_candidate_resume(db):
    candidate = client.post(
        "/api/candidates",
        json=make_candidate_payload(full_name="Jordan Lee", skills=["Python"]),
    ).json()
    another = client.post(
        "/api/candidates",
        json=make_candidate_payload(full_name="Morgan Lee", email="morgan@example.com"),
    ).json()
    candidate_resume_id = db.resumes.insert_one(
        {
            "candidate_id": ObjectId(candidate["id"]),
            "filename": "jordan.pdf",
            "label": "Jordan Resume",
            "uploaded_at": datetime.now(timezone.utc),
            "data": b"%PDF-1.4 Jordan",
        }
    ).inserted_id
    other_resume_id = db.resumes.insert_one(
        {
            "candidate_id": ObjectId(another["id"]),
            "filename": "morgan.pdf",
            "label": "Morgan Resume",
            "is_active": True,
            "uploaded_at": datetime.now(timezone.utc),
            "data": b"%PDF-1.4 Morgan",
        }
    ).inserted_id
    job = client.post(
        "/api/jobs",
        json={
            "candidate_id": candidate["id"],
            "title": "Backend Engineer",
            "company": "Example Co",
        },
    ).json()
    application = client.post(
        "/api/applications",
        json={
            "job_id": job["id"],
            "status": "saved",
            "candidate_id": candidate["id"],
        },
    )
    assert application.status_code == 201, application.text
    assert application.json()["resume_id"] == str(candidate_resume_id)

    state = client.get(
        f"/api/applications/{application.json()['id']}/assistant"
    )
    assert state.status_code == 200, state.text
    assistant_state = state.json()
    assert assistant_state["resume"]["id"] == str(candidate_resume_id)
    assert {item["id"] for item in assistant_state["available_resumes"]} == {
        str(candidate_resume_id)
    }

    rejected_resume = client.put(
        f"/api/applications/{application.json()['id']}/assistant/resume",
        json={"resume_id": str(other_resume_id)},
    )
    assert rejected_resume.status_code == 404


def test_import_profile_preserves_existing_data_and_attaches_existing_resumes(db):
    profile_id = ObjectId()
    db.profiles.insert_one({
        "_id": profile_id,
        "full_name": "Saved Profile",
        "email": "saved@example.com",
        "skills": ["TypeScript"],
        "target_roles": ["Frontend Engineer"],
        "experience_summary": "Saved summary.",
        "work_experience": [],
        "projects": [],
        "education": [],
        "certifications": [],
        "achievements": [],
    })
    resume_id = ObjectId()
    db.resumes.insert_one({
        "_id": resume_id,
        "label": "Saved resume",
        "filename": "saved.pdf",
        "size": 1,
        "page_count": 1,
        "data": b"pdf",
        "is_active": True,
        "uploaded_at": datetime.now(timezone.utc),
    })
    job = make_job()
    existing_application = client.post(
        "/api/applications",
        json={"job_id": job["id"]},
    )
    assert existing_application.status_code == 201

    imported = client.post("/api/candidates/import-profile")
    assert imported.status_code == 200, imported.text
    assert imported.json()["full_name"] == "Saved Profile"
    assert imported.json()["status"] == "not_set"
    assert imported.json()["resume_count"] == 1
    details = client.get(f"/api/candidates/{imported.json()['id']}").json()
    assert details["applications"][0]["id"] == existing_application.json()["id"]

    repeated = client.post("/api/candidates/import-profile")
    assert repeated.status_code == 200
    assert repeated.json()["id"] == imported.json()["id"]
    assert db.resumes.find_one({"_id": resume_id})["candidate_id"] == ObjectId(imported.json()["id"])


def test_candidate_data_is_scoped_to_authenticated_database(db):
    candidate = client.post("/api/candidates", json=make_candidate_payload()).json()
    other_user_db = UserScopedDatabase(db._database, "another-user")
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = lambda: other_user_db
    try:
        assert client.get(f"/api/candidates/{candidate['id']}").status_code == 404
        assert client.get("/api/candidates").json()["items"] == []
    finally:
        if previous is not None:
            app.dependency_overrides[get_db] = previous
        else:
            app.dependency_overrides.pop(get_db, None)
