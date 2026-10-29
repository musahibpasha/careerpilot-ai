import mongomock
import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import auth, database
from app.database import UserScopedCollection, UserScopedDatabase


def test_protected_api_rejects_missing_bearer_token():
    test_app = FastAPI()

    @test_app.get("/protected")
    def protected(user_id: str = Depends(auth.get_current_user_id)):
        return {"uid": user_id}

    assert TestClient(test_app).get("/protected").status_code == 401


def test_current_user_requires_bearer_token():
    with pytest.raises(HTTPException) as error:
        auth.get_current_user_id(None)
    assert error.value.status_code == 401


def test_current_user_rejects_unverified_email(monkeypatch):
    monkeypatch.setattr(auth, "_firebase_app", lambda: object())
    monkeypatch.setattr(
        auth,
        "auth",
        type(
            "FakeFirebaseAuth",
            (),
            {"verify_id_token": staticmethod(lambda token, app: {"uid": "user-1", "email_verified": False})},
        ),
    )

    with pytest.raises(HTTPException) as error:
        auth.get_current_user_id("Bearer verified-format-token")
    assert error.value.status_code == 403


def test_database_dependency_scopes_api_reads_by_verified_uid(monkeypatch):
    mongo = mongomock.MongoClient()
    monkeypatch.setattr(database, "_client", mongo)
    monkeypatch.setattr(database.settings, "mongodb_db", "api_isolation_test")
    mongo["api_isolation_test"].jobs.insert_many(
        [
            {"_id": "alice-job", "owner_uid": "alice", "title": "Alice"},
            {"_id": "bob-job", "owner_uid": "bob", "title": "Bob"},
        ]
    )

    test_app = FastAPI()

    @test_app.get("/jobs/{job_id}")
    def get_job(job_id: str, db: UserScopedDatabase = Depends(database.get_db)):
        job = db.jobs.find_one({"_id": job_id})
        if not job:
            raise HTTPException(status_code=404)
        return {"title": job["title"]}

    client = TestClient(test_app)
    assert client.get("/jobs/alice-job").status_code == 401

    test_app.dependency_overrides[auth.get_current_user_id] = lambda: "alice"
    assert client.get("/jobs/alice-job").json() == {"title": "Alice"}
    assert client.get("/jobs/bob-job").status_code == 404

    test_app.dependency_overrides[auth.get_current_user_id] = lambda: "bob"
    assert client.get("/jobs/bob-job").json() == {"title": "Bob"}
    assert client.get("/jobs/alice-job").status_code == 404


def test_user_scoped_collection_isolates_reads_and_writes():
    raw = mongomock.MongoClient()["test"]["jobs"]
    alice = UserScopedCollection(raw, "alice")
    bob = UserScopedCollection(raw, "bob")

    alice.insert_one({"_id": "job-a", "owner_uid": "bob", "title": "Alice's job"})
    bob.insert_one({"_id": "job-b", "title": "Bob's job"})

    assert alice.find_one({"_id": "job-b"}) is None
    assert bob.find_one({"_id": "job-a"}) is None
    assert alice.count_documents({}) == 1
    assert bob.count_documents({}) == 1

    assert alice.update_one(
        {"_id": "job-b"},
        {"$set": {"title": "hacked"}},
    ).matched_count == 0
    alice.update_one(
        {"_id": "job-a"},
        {"$set": {"owner_uid": "bob", "title": "updated"}},
    )
    alice.update_one(
        {"_id": "job-a"},
        {"$currentDate": {"owner_uid": True, "updated_at": True}},
    )
    assert raw.find_one({"_id": "job-a"})["owner_uid"] == "alice"
    assert alice.delete_one({"_id": "job-b"}).deleted_count == 0
    assert bob.find_one({"_id": "job-b"})["title"] == "Bob's job"
