"""Dry-run-first assignment of the legacy workspace to one verified Firebase user."""

import argparse
import sys
from urllib.parse import urlsplit

from firebase_admin import auth

from app.config import settings
from app.auth import _firebase_app
from app.database import get_base_db

COLLECTIONS = ("jobs", "applications", "profiles", "preferences", "resumes")
LEGACY_JOB_INDEXES = ("uq_job_source_id", "uq_job_url")


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Inspect the legacy CareerPilot workspace. Writing requires both "
            "--apply and an explicit --confirm-email."
        )
    )
    parser.add_argument("--email", required=True, help="Firebase account email to inspect.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply ownership tags after the separate migration approval.",
    )
    parser.add_argument(
        "--confirm-email",
        help="Must exactly match --email to permit writes.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    app = _firebase_app()
    user = auth.get_user_by_email(args.email, app=app)
    if not user.email_verified:
        raise SystemExit("Refusing migration: the requested Firebase email is not verified.")

    db = get_base_db()
    target_host = urlsplit(settings.mongodb_url).hostname or "(unknown)"
    print(f"MongoDB target: {target_host}/{settings.mongodb_db}")
    ids = {
        name: {doc["_id"] for doc in db[name].find({}, {"_id": 1})}
        for name in COLLECTIONS
    }

    print(f"Verified Firebase UID: {user.uid}")
    print("Read-only dry-run results (record values are not displayed):")
    blocked = False
    for name in COLLECTIONS:
        collection = db[name]
        count = collection.count_documents({})
        candidate = collection.count_documents({"owner_uid": {"$exists": False}})
        already_owner = collection.count_documents({"owner_uid": user.uid})
        other_owner = count - candidate - already_owner
        print(
            f"  {name}: total={count}, unassigned={candidate}, "
            f"already_for_this_uid={already_owner}, assigned_elsewhere={other_owner}"
        )

    orphan_app_jobs = 0
    orphan_app_resumes = 0
    for application in db.applications.find({}, {"job_id": 1, "resume_id": 1}):
        if application.get("job_id") not in ids["jobs"]:
            orphan_app_jobs += 1
        resume_id = application.get("resume_id")
        if resume_id is not None and resume_id not in ids["resumes"]:
            orphan_app_resumes += 1
    print(
        f"  relationship checks: applications_without_job={orphan_app_jobs}, "
        f"applications_with_missing_resume={orphan_app_resumes}"
    )
    blocked = blocked or orphan_app_jobs > 0 or orphan_app_resumes > 0

    if not args.apply:
        print("No records or indexes were changed.")
        return
    if args.confirm_email != args.email:
        raise SystemExit("Refusing writes: --confirm-email must exactly match --email.")
    if blocked:
        raise SystemExit("Refusing writes: review ownership or relationship anomalies first.")

    with db.client.start_session() as session:
        with session.start_transaction():
            for name in COLLECTIONS:
                db[name].update_many(
                    {"owner_uid": {"$exists": False}},
                    {"$set": {"owner_uid": user.uid}},
                    session=session,
                )

    jobs = db.jobs
    existing_indexes = jobs.index_information()
    for index_name in LEGACY_JOB_INDEXES:
        if index_name in existing_indexes:
            jobs.drop_index(index_name)
    print("Migration completed; no records were deleted or replaced.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Migration stopped ({type(exc).__name__}); no success is claimed.", file=sys.stderr)
        raise
