import datetime

import mongomock
import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app

_db = mongomock.MongoClient(tz_aware=True)["feedback"]


@pytest.fixture()
def client():
    _db.client.drop_database("feedback")
    app.dependency_overrides[get_db] = lambda: _db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def add_job(client, title="Dev"):
    return client.post("/api/jobs", json={"title": title, "company": "Acme", "skills": ["react"]}).json()


def put(client, job, **body):
    return client.put(f"/api/jobs/{job['id']}/feedback", json=body)


def test_new_jobs_are_not_dismissed(client):
    job = add_job(client)
    assert job["feedback"] == "" and job["feedback_reason"] == ""


def test_dismiss_with_reason_then_restore(client):
    job = add_job(client)

    r = put(client, job, value="not_interested", reason="location")
    assert r.status_code == 200
    assert r.json()["feedback"] == "not_interested" and r.json()["feedback_reason"] == "location"
    assert client.get("/api/jobs").json()[0]["feedback_reason"] == "location"

    r = put(client, job, value="")
    assert r.json()["feedback"] == "" and r.json()["feedback_reason"] == ""
    stored = _db.jobs.find_one({})
    assert "feedback" not in stored and "feedback_at" not in stored


def test_reason_is_optional(client):
    job = add_job(client)
    r = put(client, job, value="not_interested")
    assert r.status_code == 200 and r.json()["feedback_reason"] == ""


def test_can_change_the_reason(client):
    job = add_job(client)
    put(client, job, value="not_interested", reason="role")
    assert put(client, job, value="not_interested", reason="salary").json()["feedback_reason"] == "salary"


def test_dismissal_does_not_change_the_score_and_survives_rescoring(client):
    job = add_job(client)
    put(client, job, value="not_interested", reason="skills")
    assert client.post("/api/jobs/rescore").status_code == 200
    after = client.get("/api/jobs").json()[0]
    assert after["feedback"] == "not_interested" and after["feedback_reason"] == "skills"
    assert after["match_score"] == job["match_score"]


def test_rejects_bad_input_and_unknown_jobs(client):
    job = add_job(client)
    assert put(client, job, value="maybe").status_code == 422
    assert put(client, job, value="not_interested", reason="boredom").status_code == 422
    assert put(client, job, value="", reason="role").status_code == 422  # a reason needs a dismissal
    assert client.put("/api/jobs/000000000000000000000000/feedback",
                      json={"value": "not_interested"}).status_code == 404
    assert client.put("/api/jobs/not-an-id/feedback", json={"value": "not_interested"}).status_code == 404


def test_jobs_saved_by_the_earlier_build_still_read_correctly(client):
    now = datetime.datetime.now(datetime.timezone.utc)
    _db.jobs.insert_many([
        {"title": "Old A", "company": "X", "skills": [], "feedback": "not_relevant", "created_at": now},
        {"title": "Old B", "company": "X", "skills": [], "feedback": "relevant", "created_at": now},
    ])
    by_title = {j["title"]: j for j in client.get("/api/jobs").json()}
    assert by_title["Old A"]["feedback"] == "not_interested"
    assert by_title["Old B"]["feedback"] == ""