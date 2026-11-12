from types import SimpleNamespace

import mongomock
import pytest
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


def test_super_admin_email_allowlist_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(main.settings, "super_admin_emails", "admin@example.com")

    assert main._require_super_admin(_SUPER_ADMIN) == _SUPER_ADMIN

    with pytest.raises(HTTPException) as error:
        main._require_super_admin({"uid": "other", "email": "other@example.com"})
    assert error.value.status_code == 403


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
