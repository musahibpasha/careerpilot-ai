from datetime import datetime, timezone
from collections import Counter
import json

from bson import ObjectId

from scripts.reconcile_legacy_workspace import (
    build_plan,
    changed_fields,
    extended_type_counts,
    load_backups,
)


def test_changed_fields_reports_names_without_changing_content():
    before = {"_id": ObjectId(), "title": "private", "owner_uid": "old"}
    after = {"_id": before["_id"], "title": "different private value", "owner_uid": "new"}

    assert changed_fields(before, after) == ["title"]


def test_extended_json_type_inventory_counts_object_ids_and_dates():
    counts = Counter()
    extended_type_counts(
        {"_id": ObjectId(), "updated_at": datetime.now(timezone.utc)},
        counts,
    )

    assert counts == {"object_ids": 1, "dates": 1}


def test_plan_tags_matching_records_without_overwriting_and_preserves_profile_conflict():
    import mongomock

    mongo = mongomock.MongoClient()
    database = mongo["careerpilot"]
    shared_job_id = ObjectId()
    atlas_only_job_id = ObjectId()
    shared_profile_id = ObjectId()
    local_only_job_id = ObjectId()
    uid = "verified-user"

    database.jobs.insert_many(
        [
            {"_id": shared_job_id, "title": "Atlas version", "match_score": 40},
            {"_id": atlas_only_job_id, "title": "Unassigned Atlas record"},
        ]
    )
    database.profiles.insert_one({"_id": shared_profile_id, "name": "Atlas profile"})

    source = {
        "jobs": [
            {"_id": shared_job_id, "title": "Local version", "match_score": 70},
            {"_id": local_only_job_id, "owner_uid": uid, "title": "Owned local job"},
        ],
        "applications": [],
        "profiles": [
            {"_id": shared_profile_id, "name": "Local profile"},
            {"_id": ObjectId(), "owner_uid": uid, "name": "Separate local profile"},
        ],
        "preferences": [],
        "resumes": [],
    }

    plan = build_plan(source, database, uid)

    assert plan["collections"]["jobs"]["planned_owner_uid_only_updates"] == 1
    assert plan["collections"]["jobs"]["planned_inserts"] == 1
    assert plan["collections"]["jobs"]["atlas_only_preserved"] == 1
    assert plan["collections"]["jobs"]["different_field_names"] == {"match_score": 1, "title": 1}
    assert plan["collections"]["profiles"]["planned_owner_uid_only_updates"] == 0
    assert plan["collections"]["profiles"]["planned_inserts"] == 0
    assert plan["collections"]["profiles"]["skipped"] == {
        "conflicting_profile_requires_manual_resolution": 1,
        "profile_requires_manual_resolution": 1,
    }
    assert database.jobs.find_one({"_id": shared_job_id}).get("owner_uid") is None


def test_load_backups_accepts_careerpilot_prefixed_extended_json_files(tmp_path):
    for collection in ("jobs", "applications", "profiles", "preferences", "resumes"):
        (tmp_path / f"careerpilot.{collection}.json").write_text(
            json.dumps([{"_id": {"$oid": "507f1f77bcf86cd799439011"}}]),
            encoding="utf-8",
        )

    documents, summary = load_backups(tmp_path)

    assert summary["status"] == "PARSED_NOT_RESTORE_VERIFIED"
    assert summary["collections"]["jobs"]["documents"] == 1
    assert summary["collections"]["jobs"]["extended_json_object_ids"] == 1
    assert documents["jobs"][0]["_id"] == ObjectId("507f1f77bcf86cd799439011")
