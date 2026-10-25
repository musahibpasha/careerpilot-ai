import io
from datetime import datetime, timezone

import mongomock
import pytest
from bson import ObjectId
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.database import get_db
from app.main import app
from app.resumes import (
    MAX_RESUME_BYTES,
    MAX_RESUMES,
    check_pdf,
    clean_label,
    label_problems,
    safe_filename,
)

client = TestClient(app)


@pytest.fixture()
def db():
    """A fresh database per test. Restores any override set by other test files."""
    database = mongomock.MongoClient(tz_aware=True)["resumes_test"]
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = lambda: database
    yield database
    if previous is not None:
        app.dependency_overrides[get_db] = previous
    else:
        app.dependency_overrides.pop(get_db, None)


def make_pdf(pages=1, password=None):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    if password:
        writer.encrypt(password)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def upload(filename="cv.pdf", data=None, label=None):
    form = {} if label is None else {"label": label}
    return client.post(
        "/api/resumes",
        files={"file": (filename, make_pdf() if data is None else data, "application/pdf")},
        data=form,
    )


def make_job():
    return client.post("/api/jobs", json={"title": "Dev", "company": "Acme"}).json()["id"]


def track(job_id):
    response = client.post("/api/applications", json={"job_id": job_id})
    assert response.status_code == 201
    return response.json()


# ---------- the rules on their own ----------

def test_check_pdf_accepts_a_real_pdf():
    assert check_pdf("cv.pdf", make_pdf(2)) == (2, [])


@pytest.mark.parametrize(
    "filename, data, expected",
    [
        ("cv.docx", make_pdf(), "Only PDF files are supported"),
        ("cv.pdf", b"", "The file is empty"),
        ("cv.pdf", b"just text", "This does not look like a real PDF file"),
        ("cv.pdf", b"%PDF-garbage", "This PDF could not be read. Try exporting it again"),
        ("cv.pdf", make_pdf(password="secret"), "Password-protected PDFs are not supported"),
        ("cv.pdf", b"%PDF-" + b"0" * (MAX_RESUME_BYTES + 1), "The file is larger than 5 MB"),
    ],
)
def test_check_pdf_rejects_bad_files(filename, data, expected):
    pages, problems = check_pdf(filename, data)

    assert pages == 0
    assert expected in problems


def test_label_and_filename_helpers():
    assert clean_label("", "My Resume.PDF") == "My Resume"
    assert clean_label("  Front   end ", "x.pdf") == "Front end"
    assert safe_filename('../a"b;c.pdf') == "a_b_c.pdf"
    assert safe_filename("") == "resume.pdf"
    assert label_problems("cv", ["CV"]) == ["A resume called 'cv' already exists"]
    assert label_problems("", []) == ["Give this resume a name"]
    assert len(label_problems("x" * 101, [])) == 1


# ---------- uploading and listing ----------

def test_first_upload_is_active_and_the_label_defaults_to_the_file_name(db):
    response = upload("Jane Frontend.pdf")

    assert response.status_code == 201
    data = response.json()
    assert data["label"] == "Jane Frontend"
    assert data["is_active"] is True
    assert data["page_count"] == 1
    assert data["size"] > 0
    assert "data" not in data


def test_second_upload_is_not_active(db):
    upload("a.pdf")
    second = upload("b.pdf", label="Data")

    assert second.json()["is_active"] is False
    assert second.json()["label"] == "Data"


def test_list_shows_active_first_and_never_includes_file_contents(db):
    upload("b.pdf")
    upload("a.pdf")

    rows = client.get("/api/resumes").json()

    assert [r["label"] for r in rows] == ["b", "a"]
    assert all("data" not in r for r in rows)


@pytest.mark.parametrize(
    "filename, data",
    [
        ("cv.docx", b"not a pdf"),
        ("cv.pdf", b"fake pdf contents"),
        ("cv.pdf", b""),
        ("cv.pdf", b"%PDF-garbage"),
    ],
)
def test_bad_uploads_are_refused_and_nothing_is_stored(db, filename, data):
    response = upload(filename, data)

    assert response.status_code == 422
    assert db.resumes.count_documents({}) == 0


def test_duplicate_names_are_refused_ignoring_case(db):
    upload("cv.pdf", label="Frontend")

    response = upload("other.pdf", label="frontend")

    assert response.status_code == 422
    assert db.resumes.count_documents({}) == 1


def test_resume_limit(db):
    for i in range(MAX_RESUMES):
        db.resumes.insert_one({
            "label": f"R{i}", "filename": f"r{i}.pdf", "size": 1, "page_count": 1,
            "data": b"x", "is_active": i == 0, "uploaded_at": datetime.now(timezone.utc),
        })

    response = upload("one-more.pdf")

    assert response.status_code == 409
    assert db.resumes.count_documents({}) == MAX_RESUMES


# ---------- choosing the active resume, renaming, downloading ----------

def test_making_another_resume_active_unsets_the_first(db):
    first = upload("a.pdf").json()
    second = upload("b.pdf").json()

    response = client.patch(f"/api/resumes/{second['id']}", json={"is_active": True})

    assert response.status_code == 200
    rows = {r["id"]: r for r in client.get("/api/resumes").json()}
    assert rows[second["id"]]["is_active"] is True
    assert rows[first["id"]]["is_active"] is False
    assert sum(r["is_active"] for r in rows.values()) == 1


def test_an_active_resume_cannot_be_switched_off_directly(db):
    first = upload("a.pdf").json()

    response = client.patch(f"/api/resumes/{first['id']}", json={"is_active": False})

    assert response.status_code == 422
    assert client.get("/api/resumes").json()[0]["is_active"] is True


def test_rename_and_rename_conflicts(db):
    first = upload("a.pdf", label="Frontend").json()
    upload("b.pdf", label="Data")

    ok = client.patch(f"/api/resumes/{first['id']}", json={"label": "Web"})
    clash = client.patch(f"/api/resumes/{first['id']}", json={"label": "data"})
    same = client.patch(f"/api/resumes/{first['id']}", json={"label": "Web"})

    assert ok.status_code == 200 and ok.json()["label"] == "Web"
    assert clash.status_code == 422
    assert same.status_code == 200


def test_download_returns_the_same_file(db):
    data = make_pdf(3)
    saved = upload("cv.pdf", data).json()

    response = client.get(f"/api/resumes/{saved['id']}/file")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content == data


def test_unknown_ids_are_404(db):
    missing = "000000000000000000000000"

    assert client.get("/api/resumes/nope/file").status_code == 404
    assert client.get(f"/api/resumes/{missing}/file").status_code == 404
    assert client.patch(f"/api/resumes/{missing}", json={"label": "x"}).status_code == 404
    assert client.delete(f"/api/resumes/{missing}").status_code == 404


# ---------- deleting ----------

def test_deleting_the_active_resume_promotes_the_oldest_remaining(db):
    a = upload("a.pdf").json()
    b = upload("b.pdf").json()
    upload("c.pdf")
    client.patch(f"/api/resumes/{b['id']}", json={"is_active": True})

    response = client.delete(f"/api/resumes/{b['id']}")

    assert response.status_code == 204
    rows = client.get("/api/resumes").json()
    assert [r["label"] for r in rows] == ["a", "c"]
    assert rows[0]["is_active"] is True
    assert rows[0]["id"] == a["id"]


def test_deleting_the_last_resume_leaves_none_active(db):
    only = upload("a.pdf").json()

    assert client.delete(f"/api/resumes/{only['id']}").status_code == 204
    assert client.get("/api/resumes").json() == []


# ---------- applications use the active resume ----------

def test_a_new_application_records_the_active_resume(db):
    resume = upload("cv.pdf", label="Frontend").json()

    application = track(make_job())

    assert application["resume_id"] == resume["id"]
    assert application["resume_label"] == "Frontend"


def test_changing_the_active_resume_does_not_change_old_applications(db):
    first = upload("a.pdf", label="Frontend").json()
    second = upload("b.pdf", label="Data").json()
    old_application = track(make_job())

    client.patch(f"/api/resumes/{second['id']}", json={"is_active": True})
    new_application = track(make_job())

    assert old_application["resume_id"] == first["id"]
    assert new_application["resume_id"] == second["id"]
    rows = {a["id"]: a for a in client.get("/api/applications").json()}
    assert rows[old_application["id"]]["resume_label"] == "Frontend"
    assert rows[new_application["id"]]["resume_label"] == "Data"


def test_application_without_any_resume_has_none(db):
    application = track(make_job())

    assert application["resume_id"] is None
    assert application["resume_label"] is None


def test_a_resume_used_by_an_application_cannot_be_deleted(db):
    resume = upload("cv.pdf").json()
    track(make_job())

    response = client.delete(f"/api/resumes/{resume['id']}")

    assert response.status_code == 409
    assert db.resumes.count_documents({}) == 1


def test_a_resume_that_no_application_uses_can_still_be_deleted(db):
    upload("a.pdf")
    other = upload("b.pdf").json()
    track(make_job())  # uses the active resume "a"

    assert client.delete(f"/api/resumes/{other['id']}").status_code == 204


def test_uploading_resumes_does_not_change_the_profile_or_scores(db):
    profile = {
        "full_name": "Jane Example", "email": "jane@example.com",
        "phone": "+91 98765 43210", "location": "Bangalore",
        "target_roles": ["Front-End Developer"], "skills": ["React"],
        "experience_summary": "Developer.",
        "education": [{"institution": "ABC College", "degree": "BCA"}],
        "projects": [{"name": "NewsApp", "description": "Built a news app."}],
    }
    assert client.put("/api/profile", json=profile).status_code == 200
    job = client.post(
        "/api/jobs",
        json={"title": "Dev", "company": "Acme", "skills": ["React", "Go"]},
    ).json()
    profile_before = db.profiles.find_one({})

    upload("a.pdf")
    upload("b.pdf")

    after = next(j for j in client.get("/api/jobs").json() if j["id"] == job["id"])
    assert after["match_score"] == job["match_score"]
    assert db.profiles.find_one({}) == profile_before


# ---------- filling the profile form from a stored resume ----------

FAKE_RESULT = {
    "message": "Resume parsed. Review the extracted details before saving.",
    "extracted": {"full_name": "Jane", "skills": ["React", "Go"], "projects": []},
    "requires_confirmation": True,
}


@pytest.fixture()
def fake_parser(monkeypatch):
    """Stands in for the AI parser. Records every call it receives."""
    calls = []

    async def fake(file):
        calls.append({
            "filename": file.filename,
            "content_type": file.content_type,
            "data": await file.read(),
        })
        return FAKE_RESULT

    monkeypatch.setattr("app.main.parse_resume", fake)
    return calls


def test_extract_reads_the_stored_file_with_the_parser(db, fake_parser):
    data = make_pdf(2)
    saved = upload("cv.pdf", data).json()

    response = client.post(f"/api/resumes/{saved['id']}/extract")

    assert response.status_code == 200
    body = response.json()
    assert body["extracted"]["skills"] == ["React", "Go"]
    assert body["cached"] is False
    assert len(fake_parser) == 1
    assert fake_parser[0]["content_type"] == "application/pdf"
    assert fake_parser[0]["data"] == data


def test_extract_is_kept_so_the_second_call_is_instant(db, fake_parser):
    saved = upload("cv.pdf").json()
    assert client.get("/api/resumes").json()[0]["extracted_at"] is None

    client.post(f"/api/resumes/{saved['id']}/extract")
    second = client.post(f"/api/resumes/{saved['id']}/extract")

    assert second.json()["cached"] is True
    assert second.json()["extracted"]["skills"] == ["React", "Go"]
    assert len(fake_parser) == 1
    assert client.get("/api/resumes").json()[0]["extracted_at"] is not None


def test_refresh_reads_the_resume_again(db, fake_parser):
    saved = upload("cv.pdf").json()
    client.post(f"/api/resumes/{saved['id']}/extract")

    again = client.post(f"/api/resumes/{saved['id']}/extract?refresh=true")

    assert again.json()["cached"] is False
    assert len(fake_parser) == 2


def test_each_resume_keeps_its_own_extracted_details(db, fake_parser):
    first = upload("a.pdf").json()
    second = upload("b.pdf").json()

    client.post(f"/api/resumes/{first['id']}/extract")

    assert db.resumes.find_one({"_id": ObjectId(first["id"])}).get("extracted")
    assert not db.resumes.find_one({"_id": ObjectId(second["id"])}).get("extracted")


def test_a_failed_read_is_not_kept(db, monkeypatch):
    saved = upload("cv.pdf").json()

    async def failing(file):
        raise HTTPException(status_code=503, detail="OpenRouter API key is not configured.")

    monkeypatch.setattr("app.main.parse_resume", failing)

    response = client.post(f"/api/resumes/{saved['id']}/extract")

    assert response.status_code == 503
    assert not db.resumes.find_one({"_id": ObjectId(saved["id"])}).get("extracted")


def test_a_failed_refresh_keeps_the_earlier_details(db, fake_parser, monkeypatch):
    saved = upload("cv.pdf").json()
    client.post(f"/api/resumes/{saved['id']}/extract")

    async def failing(file):
        raise HTTPException(status_code=502, detail="Resume parsing failed.")

    monkeypatch.setattr("app.main.parse_resume", failing)

    response = client.post(f"/api/resumes/{saved['id']}/extract?refresh=true")

    assert response.status_code == 502
    kept = client.post(f"/api/resumes/{saved['id']}/extract")
    assert kept.status_code == 200 and kept.json()["cached"] is True


def test_extract_unknown_resume_is_404(db, fake_parser):
    assert client.post("/api/resumes/nope/extract").status_code == 404
    assert client.post("/api/resumes/000000000000000000000000/extract").status_code == 404
    assert fake_parser == []


def test_extract_never_changes_the_profile_or_the_active_resume(db, fake_parser):
    profile = {
        "full_name": "Jane Example", "email": "jane@example.com",
        "phone": "+91 98765 43210", "location": "Bangalore",
        "target_roles": ["Front-End Developer"], "skills": ["React"],
        "experience_summary": "Developer.",
        "education": [{"institution": "ABC College", "degree": "BCA"}],
        "projects": [{"name": "NewsApp", "description": "Built a news app."}],
    }
    assert client.put("/api/profile", json=profile).status_code == 200
    first = upload("a.pdf").json()
    second = upload("b.pdf").json()
    profile_before = db.profiles.find_one({})

    client.post(f"/api/resumes/{second['id']}/extract")

    assert db.profiles.find_one({}) == profile_before
    rows = {r["id"]: r for r in client.get("/api/resumes").json()}
    assert rows[first["id"]]["is_active"] is True
    assert rows[second["id"]]["is_active"] is False