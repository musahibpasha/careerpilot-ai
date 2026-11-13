from types import SimpleNamespace

import mongomock
import pytest
from bson import ObjectId
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main
from app.database import ensure_indexes, get_base_db
from app.main import app


_SUPER_ADMIN = {"uid": "super-admin", "email": "ADMIN@example.com"}


def test_database_indexes_are_idempotent_with_recruiter_email_collation():
    db = mongomock.MongoClient().indexes
    db.recruiters.create_index(
        "email",
        unique=True,
        name="uq_recruiter_email",
        collation={"locale": "en", "strength": 2},
    )

    ensure_indexes(db)
    ensure_indexes(db)

    assert db.recruiters.index_information()["uq_recruiter_email"]["unique"] is True


def test_database_indexes_keep_legacy_case_sensitive_recruiter_email_index():
    db = mongomock.MongoClient().legacy_indexes
    db.recruiters.create_index(
        "email",
        unique=True,
        name="uq_recruiter_email",
    )

    ensure_indexes(db)

    email_index = db.recruiters.index_information()["uq_recruiter_email"]
    assert email_index["unique"] is True
    assert "collation" not in email_index


def test_super_admin_email_allowlist_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(main.settings, "super_admin_emails", "admin@example.com")

    assert main._require_super_admin(_SUPER_ADMIN) == _SUPER_ADMIN

    with pytest.raises(HTTPException) as error:
        main._require_super_admin({"uid": "other", "email": "other@example.com"})
    assert error.value.status_code == 403


def test_admin_session_reports_regular_role_without_a_forbidden_response(
    monkeypatch,
):
    monkeypatch.setattr(main.settings, "super_admin_emails", "admin@example.com")
    previous_claims = app.dependency_overrides.get(main.get_current_user_claims)
    app.dependency_overrides[main.get_current_user_claims] = lambda: {
        "uid": "recruiter",
        "email": "recruiter@example.com",
    }
    client = TestClient(app)
    try:
        response = client.get("/api/admin/session")
        assert response.status_code == 200
        assert response.json()["role"] == "recruiter"

        forbidden = client.get("/api/admin/recruiters")
        assert forbidden.status_code == 403
    finally:
        if previous_claims is None:
            app.dependency_overrides.pop(main.get_current_user_claims, None)
        else:
            app.dependency_overrides[main.get_current_user_claims] = previous_claims


def test_super_admin_routes_create_list_and_show_workspace_only(monkeypatch):
    db = mongomock.MongoClient().admin_recruiter_test
    previous_db = app.dependency_overrides.get(get_base_db)
    previous_admin = app.dependency_overrides.get(main._require_super_admin)
    app.dependency_overrides[get_base_db] = lambda: db
    app.dependency_overrides[main._require_super_admin] = lambda: _SUPER_ADMIN
    created_args = {}

    def fake_create_user(**kwargs):
        created_args.update(kwargs)
        return SimpleNamespace(uid="recruiter-uid-1")

    monkeypatch.setattr(main.firebase_auth, "create_user", fake_create_user)
    monkeypatch.setattr(main, "_firebase_app", lambda: object())

    client = TestClient(app)
    try:
        created = client.post(
            "/api/admin/recruiters",
            json={"display_name": "  Alex Recruiter ", "email": " ALEX@Example.com "},
        )
        assert created.status_code == 201
        assert created.json()["email"] == "alex@example.com"
        assert created.json()["display_name"] == "Alex Recruiter"
        assert created.json()["workspace"] == {
            "candidates": 0,
            "jobs": 0,
            "applications": 0,
            "resumes": 0,
        }
        assert created_args["email"] == "alex@example.com"
        assert "password" not in created_args

        db.candidates.insert_one({"owner_uid": "recruiter-uid-1"})
        db.candidates.insert_one({"owner_uid": "another-recruiter"})
        db.jobs.insert_one({"owner_uid": "recruiter-uid-1"})
        db.applications.insert_one({"owner_uid": "recruiter-uid-1"})
        db.resumes.insert_one({"owner_uid": "recruiter-uid-1"})

        listing = client.get("/api/admin/recruiters")
        assert listing.status_code == 200
        assert len(listing.json()["items"]) == 1
        assert listing.json()["items"][0]["workspace"] == {
            "candidates": 1,
            "jobs": 1,
            "applications": 1,
            "resumes": 1,
        }

        details = client.get("/api/admin/recruiters/recruiter-uid-1")
        assert details.status_code == 200
        assert details.json()["uid"] == "recruiter-uid-1"
        assert details.json()["workspace"]["candidates"] == 1

        duplicate = client.post(
            "/api/admin/recruiters",
            json={"display_name": "Alex Again", "email": "alex@example.com"},
        )
        assert duplicate.status_code == 409
        assert client.delete("/api/admin/recruiters/recruiter-uid-1").status_code == 405
    finally:
        if previous_db is None:
            app.dependency_overrides.pop(get_base_db, None)
        else:
            app.dependency_overrides[get_base_db] = previous_db
        if previous_admin is None:
            app.dependency_overrides.pop(main._require_super_admin, None)
        else:
            app.dependency_overrides[main._require_super_admin] = previous_admin


def test_super_admin_can_view_recruiter_application_and_candidate_but_recruiter_cannot(
    monkeypatch,
):
    from datetime import datetime, timezone

    db = mongomock.MongoClient(tz_aware=True).admin_applications_test
    recruiter_uid = "recruiter-uid"
    candidate_id = ObjectId()
    job_id = ObjectId()
    application_id = ObjectId()
    now = datetime.now(timezone.utc)
    db.recruiters.insert_one({
        "uid": recruiter_uid,
        "display_name": "Alex Recruiter",
        "email": "alex@example.com",
    })
    db.candidates.insert_one({
        "_id": candidate_id,
        "owner_uid": recruiter_uid,
        "full_name": "Jordan Candidate",
    })
    db.jobs.insert_one({
        "_id": job_id,
        "owner_uid": recruiter_uid,
        "title": "DevOps Engineer",
        "company": "Example Europe",
        "url": "https://example.com/jobs/devops-engineer",
    })
    db.applications.insert_one({
        "_id": application_id,
        "owner_uid": recruiter_uid,
        "job_id": job_id,
        "candidate_id": candidate_id,
        "status": "saved",
        "status_history": [],
        "notes": "",
        "created_at": now,
        "updated_at": now,
        "apply_status": "applied",
        "apply_status_history": [{
            "from_status": "not_applied",
            "to_status": "applied",
            "at": now,
            "actor": "user",
            "note": "",
        }],
        "applied_at": now,
        "apply_updated_at": now,
    })

    previous_db = app.dependency_overrides.get(get_base_db)
    previous_claims = app.dependency_overrides.get(main.get_current_user_claims)
    previous_emails = main.settings.super_admin_emails
    app.dependency_overrides[get_base_db] = lambda: db
    main.settings.super_admin_emails = "admin@example.com"
    claims = {"uid": "super-admin", "email": "admin@example.com"}
    app.dependency_overrides[main.get_current_user_claims] = lambda: claims
    client = TestClient(app)
    try:
        response = client.get("/api/admin/applications")
        assert response.status_code == 200
        assert len(response.json()) == 1
        record = response.json()[0]
        assert record["recruiter_uid"] == recruiter_uid
        assert record["recruiter_name"] == "Alex Recruiter"
        assert record["recruiter_email"] == "alex@example.com"
        assert record["candidate_name"] == "Jordan Candidate"
        assert record["job_title"] == "DevOps Engineer"
        assert record["job_url"] == "https://example.com/jobs/devops-engineer"
        assert record["apply_status"] == "applied"
        assert "notes" not in record
        assert "apply_documents" not in record

        claims = {"uid": recruiter_uid, "email": "alex@example.com"}
        forbidden = client.get("/api/admin/applications")
        assert forbidden.status_code == 403
    finally:
        main.settings.super_admin_emails = previous_emails
        if previous_db is None:
            app.dependency_overrides.pop(get_base_db, None)
        else:
            app.dependency_overrides[get_base_db] = previous_db
        if previous_claims is None:
            app.dependency_overrides.pop(main.get_current_user_claims, None)
        else:
            app.dependency_overrides[main.get_current_user_claims] = previous_claims


def test_super_admin_can_see_all_firebase_accounts_including_accounts_not_admin_added(monkeypatch):
    from datetime import datetime, timezone

    db = mongomock.MongoClient(tz_aware=True).admin_account_directory_test
    db.recruiters.insert_one({
        "uid": "admin-added-uid",
        "display_name": "Added Recruiter",
        "email": "added@example.com",
    })
    db.candidates.insert_one({"owner_uid": "previous-login-uid"})

    user_metadata = SimpleNamespace(
        creation_timestamp=1_700_000_000_000,
        last_sign_in_timestamp=1_730_000_000_000,
    )
    users = [
        SimpleNamespace(
            uid="previous-login-uid", email="previous@example.com",
            display_name="Previous Login", email_verified=True, disabled=False,
            user_metadata=user_metadata,
        ),
        SimpleNamespace(
            uid="admin-added-uid", email="added@example.com",
            display_name="Added Recruiter", email_verified=True, disabled=False,
            user_metadata=user_metadata,
        ),
    ]
    monkeypatch.setattr(main.firebase_auth, "list_users", lambda **kwargs: SimpleNamespace(users=users, next_page_token=None))
    monkeypatch.setattr(main, "_firebase_app", lambda: object())

    previous_db = app.dependency_overrides.get(get_base_db)
    previous_admin = app.dependency_overrides.get(main._require_super_admin)
    app.dependency_overrides[get_base_db] = lambda: db
    app.dependency_overrides[main._require_super_admin] = lambda: _SUPER_ADMIN
    client = TestClient(app)
    try:
        response = client.get("/api/admin/accounts")
        assert response.status_code == 200
        items = {item["uid"]: item for item in response.json()["items"]}
        assert set(items) == {"previous-login-uid", "admin-added-uid"}
        assert items["previous-login-uid"]["email"] == "previous@example.com"
        assert items["previous-login-uid"]["recruiter_registered_by_admin"] is False
        assert items["previous-login-uid"]["workspace"]["candidates"] == 1
        assert items["admin-added-uid"]["recruiter_registered_by_admin"] is True
    finally:
        if previous_db is None:
            app.dependency_overrides.pop(get_base_db, None)
        else:
            app.dependency_overrides[get_base_db] = previous_db
        if previous_admin is None:
            app.dependency_overrides.pop(main._require_super_admin, None)
        else:
            app.dependency_overrides[main._require_super_admin] = previous_admin
