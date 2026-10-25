from datetime import datetime, timezone

import mongomock
import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.workflow import (
    CREATABLE,
    GATED,
    STATUSES,
    TRANSITIONS,
    allowed_next,
    gated_next,
    migrate_statuses,
)

client = TestClient(app)


@pytest.fixture()
def db():
    """A fresh database per test. Restores any override set by other test files."""
    database = mongomock.MongoClient(tz_aware=True)["workflow_test"]
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = lambda: database
    yield database
    if previous is not None:
        app.dependency_overrides[get_db] = previous
    else:
        app.dependency_overrides.pop(get_db, None)


def make_application(status="saved"):
    job_id = client.post(
        "/api/jobs", json={"title": "Dev", "company": "Acme"}
    ).json()["id"]
    response = client.post(
        "/api/applications", json={"job_id": job_id, "status": status}
    )
    assert response.status_code == 201
    return response.json()


def move(application_id, status, note=None):
    body = {"status": status}
    if note is not None:
        body["note"] = note
    return client.patch(f"/api/applications/{application_id}/status", json=body)


def get_application(application_id):
    rows = client.get("/api/applications").json()
    return next(r for r in rows if r["id"] == application_id)


# ---------- the rules themselves ----------

def test_every_status_has_a_transition_entry_and_known_targets():
    assert set(TRANSITIONS) == set(STATUSES)
    for targets in TRANSITIONS.values():
        assert all(t in STATUSES for t in targets)


def test_allowed_next_never_offers_gated_statuses():
    for status in STATUSES:
        assert not set(allowed_next(status)) & GATED


def test_gated_statuses_are_only_reachable_in_order():
    sources_of_approved = [s for s, t in TRANSITIONS.items() if "approved" in t]
    sources_of_submitted = [s for s, t in TRANSITIONS.items() if "submitted" in t]
    assert sources_of_approved == ["ready_for_approval"]
    assert sources_of_submitted == ["approved"]
    assert gated_next("ready_for_approval") == ["approved"]


def test_terminal_statuses_have_no_next_step():
    assert allowed_next("rejected") == []
    assert allowed_next("withdrawn") == []


# ---------- creating and history ----------

def test_new_application_starts_with_one_history_entry(db):
    application = make_application()

    assert application["status"] == "saved"
    history = application["status_history"]
    assert len(history) == 1
    assert history[0]["from_status"] is None
    assert history[0]["to_status"] == "saved"
    assert history[0]["actor"] == "user"


def test_application_can_start_as_discovered(db):
    application = make_application("discovered")

    assert application["allowed_next"] == ["saved", "withdrawn"]


def test_each_change_adds_a_history_entry_in_order(db):
    application = make_application()
    application_id = application["id"]

    assert move(application_id, "under_review", note="Reading the role").status_code == 200
    assert move(application_id, "ready_for_approval").status_code == 200

    history = get_application(application_id)["status_history"]
    assert [(h["from_status"], h["to_status"]) for h in history] == [
        (None, "saved"),
        ("saved", "under_review"),
        ("under_review", "ready_for_approval"),
    ]
    assert history[1]["note"] == "Reading the role"
    assert history[0]["at"] <= history[1]["at"] <= history[2]["at"]


# ---------- invalid and blocked moves ----------

def test_invalid_transition_is_refused_and_not_recorded(db):
    application = make_application()
    application_id = application["id"]

    response = move(application_id, "interview")

    assert response.status_code == 409
    current = get_application(application_id)
    assert current["status"] == "saved"
    assert len(current["status_history"]) == 1


def test_terminal_status_cannot_move(db):
    application_id = make_application()["id"]
    assert move(application_id, "withdrawn").status_code == 200

    assert move(application_id, "saved").status_code == 409
    assert get_application(application_id)["allowed_next"] == []


def test_unknown_status_is_422(db):
    application_id = make_application()["id"]

    assert move(application_id, "applied").status_code == 422
    assert move(application_id, "banana").status_code == 422


@pytest.mark.parametrize("locked", sorted(GATED))
@pytest.mark.parametrize("start", [s for s in STATUSES if s not in GATED])
def test_gated_status_is_refused_from_every_start(db, locked, start):
    application_id = make_application()["id"]
    # Put the application in the starting state directly in the database,
    # so this test covers every start, not only reachable ones.
    db.applications.update_one({}, {"$set": {"status": start}})

    response = move(application_id, locked)

    assert response.status_code == 403
    assert get_application(application_id)["status"] == start
    assert len(get_application(application_id)["status_history"]) == 1


@pytest.mark.parametrize("locked", sorted(GATED))
def test_cannot_create_application_at_gated_status(db, locked):
    job_id = client.post(
        "/api/jobs", json={"title": "Dev", "company": "Acme"}
    ).json()["id"]

    response = client.post(
        "/api/applications", json={"job_id": job_id, "status": locked}
    )

    assert response.status_code == 403
    assert db.applications.count_documents({}) == 0


def test_only_discovered_and_saved_can_be_created(db):
    job_id = client.post(
        "/api/jobs", json={"title": "Dev", "company": "Acme"}
    ).json()["id"]

    for status in STATUSES:
        if status in GATED or status in CREATABLE:
            continue
        response = client.post(
            "/api/applications", json={"job_id": job_id, "status": status}
        )
        assert response.status_code == 409, status


def test_status_must_be_sent_in_the_body(db):
    application_id = make_application()["id"]

    # Old style (query parameter) is no longer accepted.
    response = client.patch(
        f"/api/applications/{application_id}/status",
        params={"status": "under_review"},
    )
    assert response.status_code == 422
    assert get_application(application_id)["status"] == "saved"


def test_response_lists_locked_next_steps_separately(db):
    application_id = make_application()["id"]
    move(application_id, "under_review")
    move(application_id, "ready_for_approval")

    current = get_application(application_id)

    assert current["allowed_next"] == ["under_review", "withdrawn"]
    assert current["gated_next"] == ["approved"]


# ---------- migration from the old statuses ----------

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
CREATED = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


def insert_old(db, status, **extra):
    job_id = db.jobs.insert_one({"title": "Dev", "company": "Acme"}).inserted_id
    doc = {
        "job_id": job_id,
        "status": status,
        "notes": "",
        "created_at": CREATED,
        "updated_at": CREATED,
    }
    doc.update(extra)
    return db.applications.insert_one(doc).inserted_id


def test_migration_maps_old_statuses(db):
    ids = {
        old: insert_old(db, old)
        for old in [
            "saved", "drafting", "needs_review", "applied",
            "interview", "offer", "rejected", "withdrawn",
        ]
    }

    result = migrate_statuses(db, NOW)

    assert result["unknown"] == []
    expected = {
        "saved": "saved",
        "drafting": "under_review",
        "needs_review": "under_review",
        "applied": "submitted",
        "interview": "interview",
        "offer": "offer",
        "rejected": "rejected",
        "withdrawn": "withdrawn",
    }
    for old, new in expected.items():
        doc = db.applications.find_one({"_id": ids[old]})
        assert doc["status"] == new, old


def test_migration_records_history_and_keeps_old_value(db):
    changed = insert_old(db, "drafting")
    unchanged = insert_old(db, "saved")

    migrate_statuses(db, NOW)

    doc = db.applications.find_one({"_id": changed})
    assert doc["legacy_status"] == "drafting"
    assert [(h["from_status"], h["to_status"], h["actor"]) for h in doc["status_history"]] == [
        (None, "drafting", "system"),
        ("drafting", "under_review", "migration"),
    ]
    assert doc["updated_at"].replace(tzinfo=timezone.utc) == CREATED

    same = db.applications.find_one({"_id": unchanged})
    assert "legacy_status" not in same
    assert len(same["status_history"]) == 1


def test_migration_is_repeatable_and_does_nothing_second_time(db):
    insert_old(db, "drafting")
    insert_old(db, "saved")

    first = migrate_statuses(db, NOW)
    second = migrate_statuses(db, NOW)

    assert first["migrated"] == 1 and first["history_started"] == 1
    assert second == {"migrated": 0, "history_started": 0, "unknown": []}


def test_migration_dry_run_changes_nothing(db):
    old_id = insert_old(db, "drafting")

    result = migrate_statuses(db, NOW, dry_run=True)

    assert result["migrated"] == 1
    doc = db.applications.find_one({"_id": old_id})
    assert doc["status"] == "drafting"
    assert "status_history" not in doc


def test_migration_leaves_unknown_statuses_alone_and_reports_them(db):
    odd_id = insert_old(db, "mystery")

    result = migrate_statuses(db, NOW)

    assert result["unknown"] == ["mystery"]
    doc = db.applications.find_one({"_id": odd_id})
    assert doc["status"] == "mystery"
    assert "status_history" not in doc


def test_migration_skips_applications_that_already_have_history(db):
    existing = [{"from_status": None, "to_status": "saved", "at": CREATED,
                 "actor": "user", "note": ""}]
    kept = insert_old(db, "saved", status_history=existing)

    result = migrate_statuses(db, NOW)

    assert result["history_started"] == 0
    assert len(db.applications.find_one({"_id": kept})["status_history"]) == 1


def test_migrated_submitted_application_still_cannot_be_set_through_the_api(db):
    insert_old(db, "applied")
    migrate_statuses(db, NOW)
    application = client.get("/api/applications").json()[0]

    assert application["status"] == "submitted"
    assert application["gated_next"] == []

    other = make_application()
    assert move(other["id"], "submitted").status_code == 403