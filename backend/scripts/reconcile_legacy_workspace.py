"""Dry-run-first reconciliation of JSON workspace backups into MongoDB Atlas."""

import argparse
import getpass
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from bson import Binary, ObjectId, json_util
from bson.json_util import CANONICAL_JSON_OPTIONS
from dotenv import dotenv_values
from firebase_admin import auth
from pymongo import MongoClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import _firebase_app
from app.config import settings

COLLECTIONS = ("jobs", "applications", "profiles", "preferences", "resumes")
ATLAS_URL_ENV = "CAREERPILOT_MIGRATION_ATLAS_URL"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Inspect and report only (default).")
    mode.add_argument("--apply", action="store_true", help="Apply only the reviewed, gated manifest.")
    parser.add_argument("--backup-dir", type=Path, default=Path("migration-backups"))
    parser.add_argument("--atlas-db", help="Explicit Atlas database name; must be careerpilot.")
    parser.add_argument("--expected-email", help="Expected verified Firebase email (not identity proof).")
    parser.add_argument("--confirm-email", help="Required for --apply; must exactly match the verified email.")
    parser.add_argument("--confirm-target", help="Required for --apply; must exactly match --atlas-db.")
    parser.add_argument("--approval-manifest-sha256", help="Reviewed dry-run manifest hash required for --apply.")
    parser.add_argument("--readiness-file", type=Path, help="Backup and staging verification attestations.")
    return parser.parse_args()


def canonical(value):
    return json_util.dumps(
        value,
        json_options=CANONICAL_JSON_OPTIONS,
        sort_keys=True,
        separators=(",", ":"),
    )


def value_key(value):
    return canonical(value)


def load_json_documents(path):
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return []
    try:
        parsed = json_util.loads(text)
    except (ValueError, TypeError):
        parsed = [json_util.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list) or any(not isinstance(doc, dict) for doc in parsed):
        raise ValueError(f"{path.name} must contain a JSON document or array of documents.")
    return parsed


def extended_type_counts(value, counts):
    if isinstance(value, dict):
        for item in value.values():
            extended_type_counts(item, counts)
    elif isinstance(value, list):
        for item in value:
            extended_type_counts(item, counts)
    elif isinstance(value, datetime):
        counts["dates"] += 1
    elif isinstance(value, ObjectId):
        counts["object_ids"] += 1
    elif isinstance(value, Binary):
        counts["binary_values"] += 1


def load_backups(folder):
    if not folder.is_dir():
        return None, {
            "status": "BLOCKED",
            "reason": "Backup directory is missing.",
            "directory": str(folder),
        }

    documents = {}
    summary = {}
    missing = []
    for collection in COLLECTIONS:
        path = folder / f"{collection}.json"
        if not path.is_file():
            path = folder / f"careerpilot.{collection}.json"
        if not path.is_file():
            missing.append(f"{collection}.json or careerpilot.{collection}.json")
            continue
        rows = load_json_documents(path)
        if any("_id" not in row for row in rows):
            raise ValueError(f"{path.name} contains a document without _id.")
        ids = [value_key(row["_id"]) for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{path.name} contains duplicate _id values.")
        types = Counter()
        for row in rows:
            extended_type_counts(row, types)
        documents[collection] = rows
        summary[collection] = {
            "documents": len(rows),
            "extended_json_object_ids": types["object_ids"],
            "extended_json_dates": types["dates"],
            "extended_json_binary_values": types["binary_values"],
        }
    if missing:
        return None, {
            "status": "BLOCKED",
            "reason": "All five collection backup files are required.",
            "missing_files": missing,
            "collections_found": summary,
        }
    return documents, {
        "status": "PARSED_NOT_RESTORE_VERIFIED",
        "collections": summary,
    }


def atlas_url():
    configured = os.environ.get(ATLAS_URL_ENV)
    if not configured:
        configured = dotenv_values(".env").get(ATLAS_URL_ENV)
    return configured


def verify_identity(expected_email):
    if not expected_email:
        raise ValueError("--expected-email is required.")
    token = getpass.getpass("Paste a current Firebase ID token (input hidden): ").strip()
    if not token:
        raise ValueError("A Firebase ID token is required.")
    claims = auth.verify_id_token(token, app=_firebase_app())
    email = claims.get("email")
    uid = claims.get("uid")
    if not claims.get("email_verified") or email != expected_email or not isinstance(uid, str) or not uid:
        raise ValueError("Verified token identity does not match the expected verified account.")
    return uid, email


def establish_target(database_name):
    if database_name != "careerpilot":
        raise ValueError("Refusing target: --atlas-db must be exactly 'careerpilot'.")
    uri = atlas_url()
    if not uri:
        raise ValueError(f"{ATLAS_URL_ENV} is not configured; local MONGODB_URL is never used as fallback.")
    parsed = urlsplit(uri)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme not in ("mongodb", "mongodb+srv") or not hostname.endswith(".mongodb.net"):
        raise ValueError("Refusing target: the explicit migration URI is not a MongoDB Atlas hostname.")
    client = MongoClient(uri, serverSelectionTimeoutMS=7000, connectTimeoutMS=7000)
    hello = client.admin.command("hello")
    if not (hello.get("setName") or hello.get("msg") == "isdbgrid"):
        client.close()
        raise ValueError("Refusing target: Atlas topology did not confirm a transaction-capable deployment.")
    return client, client[database_name]


def changed_fields(left, right):
    return sorted(
        key
        for key in set(left) | set(right)
        if key != "owner_uid" and canonical(left.get(key)) != canonical(right.get(key))
    )


def has_owner(doc, uid):
    return doc.get("owner_uid") == uid


def reference_status(doc, jobs, resumes):
    missing_job = value_key(doc.get("job_id")) not in jobs if doc.get("job_id") is not None else True
    resume_id = doc.get("resume_id")
    missing_resume = resume_id is not None and value_key(resume_id) not in resumes
    return missing_job, missing_resume


def unique_index_conflicts(collection, candidates, existing):
    indexes = collection.index_information()
    unique_indexes = [
        {"name": name, "keys": [field for field, _ in index["key"]]}
        for name, index in indexes.items()
        if index.get("unique")
    ]
    conflicts = Counter()
    blocked_ids = set()
    for index in unique_indexes:
        fields = index["keys"]
        if fields == ["_id"]:
            continue
        seen = {}
        for doc in existing:
            if all(field in doc and doc[field] is not None for field in fields):
                seen[tuple(value_key(doc[field]) for field in fields)] = value_key(doc["_id"])
        for doc in candidates:
            if not all(field in doc and doc[field] is not None for field in fields):
                continue
            key = tuple(value_key(doc[field]) for field in fields)
            if key in seen and seen[key] != value_key(doc["_id"]):
                conflicts[index["name"]] += 1
                blocked_ids.add(value_key(doc["_id"]))
            else:
                seen[key] = value_key(doc["_id"])
    return unique_indexes, dict(conflicts), blocked_ids


def build_plan(source, database, uid):
    target_rows = {name: list(database[name].find({})) for name in COLLECTIONS}
    target_by_id = {
        name: {value_key(row["_id"]): row for row in rows}
        for name, rows in target_rows.items()
    }
    source_by_id = {
        name: {value_key(row["_id"]): row for row in rows}
        for name, rows in source.items()
    }
    plan = {
        "target_database": "careerpilot",
        "collections": {},
        "reference_checks": {},
        "manifest_sha256": "",
    }
    ownership_ops = []
    insert_ops = []
    conflict_total = 0

    for name in COLLECTIONS:
        target = target_by_id[name]
        source_map = source_by_id[name]
        ownership_ids = []
        insert_docs = []
        skipped = Counter()
        changed_fields_summary = Counter()
        content_conflicts = 0
        for doc_key, local_doc in source_map.items():
            atlas_doc = target.get(doc_key)
            if atlas_doc is None:
                if has_owner(local_doc, uid):
                    if name == "profiles":
                        skipped["profile_requires_manual_resolution"] += 1
                    else:
                        insert_docs.append(local_doc)
                else:
                    skipped["local_only_unassigned_preserved"] += 1
                continue

            differences = changed_fields(local_doc, atlas_doc)
            if differences:
                content_conflicts += 1
                changed_fields_summary.update(differences)
            atlas_owner = atlas_doc.get("owner_uid")
            if atlas_owner is not None and atlas_owner != uid:
                skipped["owned_by_another_uid"] += 1
            elif atlas_owner == uid:
                skipped["already_owned_by_target_uid"] += 1
            elif name == "profiles" and differences:
                skipped["conflicting_profile_requires_manual_resolution"] += 1
            elif name == "applications":
                missing_job, missing_resume = reference_status(
                    atlas_doc,
                    set(target_by_id["jobs"]) | {
                        value_key(row["_id"]) for row in insert_ops if row[0] == "jobs"
                    },
                    set(target_by_id["resumes"]) | {
                        value_key(row["_id"]) for row in insert_ops if row[0] == "resumes"
                    },
                )
                if missing_job or missing_resume:
                    skipped["application_reference_conflict"] += 1
                else:
                    ownership_ids.append(atlas_doc["_id"])
            else:
                ownership_ids.append(atlas_doc["_id"])

        unique_indexes, index_conflicts, blocked_ids = unique_index_conflicts(
            database[name], insert_docs, target_rows[name]
        )
        if index_conflicts:
            conflict_total += sum(index_conflicts.values())
            skipped["unique_index_conflict"] += sum(index_conflicts.values())
            insert_docs = [doc for doc in insert_docs if value_key(doc["_id"]) not in blocked_ids]

        ownership_ops.extend((name, doc_id) for doc_id in ownership_ids)
        insert_ops.extend((name, doc) for doc in insert_docs)
        atlas_only = len(set(target) - set(source_map))
        atlas_only_unassigned = sum(
            1 for key in set(target) - set(source_map)
            if target[key].get("owner_uid") is None
        )
        plan["collections"][name] = {
            "backup_documents": len(source_map),
            "atlas_documents": len(target),
            "same_id_matches": len(set(source_map) & set(target)),
            "same_id_content_differences": content_conflicts,
            "different_field_names": dict(sorted(changed_fields_summary.items())),
            "atlas_only_preserved": atlas_only,
            "atlas_only_unassigned": atlas_only_unassigned,
            "planned_owner_uid_only_updates": len(ownership_ids),
            "planned_inserts": len(insert_docs),
            "skipped": dict(skipped),
            "unique_indexes": unique_indexes,
            "unique_index_conflicts": index_conflicts,
            "expected_final_documents": len(target) + len(insert_docs),
        }

    target_job_ids = set(target_by_id["jobs"]) | {
        value_key(doc["_id"]) for name, doc in insert_ops if name == "jobs"
    }
    target_resume_ids = set(target_by_id["resumes"]) | {
        value_key(doc["_id"]) for name, doc in insert_ops if name == "resumes"
    }
    local_orphan_jobs = 0
    local_orphan_resumes = 0
    for doc in source["applications"]:
        missing_job, missing_resume = reference_status(doc, target_job_ids, target_resume_ids)
        local_orphan_jobs += int(missing_job)
        local_orphan_resumes += int(missing_resume)
    atlas_orphan_jobs = 0
    atlas_orphan_resumes = 0
    for doc in target_rows["applications"]:
        missing_job, missing_resume = reference_status(
            doc, target_by_id["jobs"], target_by_id["resumes"]
        )
        atlas_orphan_jobs += int(missing_job)
        atlas_orphan_resumes += int(missing_resume)
    plan["reference_checks"] = {
        "backup_applications_without_job_after_plan": local_orphan_jobs,
        "backup_applications_with_missing_resume_after_plan": local_orphan_resumes,
        "atlas_applications_without_job": atlas_orphan_jobs,
        "atlas_applications_with_missing_resume": atlas_orphan_resumes,
    }
    plan_hash_content = {
        "collections": plan["collections"],
        "reference_checks": plan["reference_checks"],
        "owner_updates": [
            [name, value_key(doc_id)] for name, doc_id in ownership_ops
        ],
        "inserts": [
            [name, hashlib.sha256(canonical(doc).encode("utf-8")).hexdigest()]
            for name, doc in insert_ops
        ],
    }
    plan["manifest_sha256"] = hashlib.sha256(
        canonical(plan_hash_content).encode("utf-8")
    ).hexdigest()
    plan["_ownership_ops"] = ownership_ops
    plan["_insert_ops"] = insert_ops
    plan["_blocking_conflicts"] = conflict_total
    return plan


def verify_readiness(path, backup_dir):
    if not path or not path.is_file():
        raise ValueError("--apply requires a readiness file confirming backups and isolated staging.")
    readiness = json.loads(path.read_text(encoding="utf-8"))
    required = (
        "json_source_sha256",
        "local_database_backup_file",
        "local_database_backup_sha256",
        "atlas_database_backup_file",
        "atlas_database_backup_sha256",
        "isolated_restore_verified",
        "staging_reconciliation_verified",
    )
    if any(not readiness.get(key) for key in required):
        raise ValueError("Readiness file is missing required backup or isolated staging verification.")
    if readiness["isolated_restore_verified"] is not True:
        raise ValueError("Local and Atlas backup restore tests must both be verified.")
    if readiness["staging_reconciliation_verified"] is not True:
        raise ValueError("Isolated Atlas staging reconciliation must be verified.")
    if not readiness.get("verified_at") or not readiness.get("verified_by"):
        raise ValueError("Readiness file must identify when and by whom verification was completed.")
    if not backup_dir.is_dir():
        raise ValueError("Backup directory is missing.")
    source_digest = hashlib.sha256()
    for collection in COLLECTIONS:
        backup_file = backup_dir / f"{collection}.json"
        if not backup_file.is_file():
            backup_file = backup_dir / f"careerpilot.{collection}.json"
        source_digest.update(backup_file.name.encode("utf-8"))
        source_digest.update(backup_file.read_bytes())
    if readiness["json_source_sha256"] != source_digest.hexdigest():
        raise ValueError("JSON source hash does not match the current five exports.")
    for path_key, hash_key, label in (
        ("local_database_backup_file", "local_database_backup_sha256", "local database"),
        ("atlas_database_backup_file", "atlas_database_backup_sha256", "Atlas database"),
    ):
        backup_path = Path(readiness[path_key])
        if not backup_path.is_absolute():
            backup_path = path.parent / backup_path
        if not backup_path.is_file():
            raise ValueError(f"Readiness file must identify the verified {label} archive.")
        if readiness[hash_key] != hashlib.sha256(backup_path.read_bytes()).hexdigest():
            raise ValueError(f"{label.title()} backup hash does not match its archive.")
    return readiness


def apply_plan(database, plan, uid):
    with database.client.start_session() as session:
        with session.start_transaction():
            for name, doc_id in plan["_ownership_ops"]:
                database[name].update_one(
                    {
                        "_id": doc_id,
                        "$or": [
                            {"owner_uid": {"$exists": False}},
                            {"owner_uid": None},
                        ],
                    },
                    {"$set": {"owner_uid": uid}},
                    session=session,
                )
            for name, source_doc in plan["_insert_ops"]:
                document = dict(source_doc)
                if document.get("owner_uid") != uid:
                    raise ValueError("Refusing insert: source owner does not match verified UID.")
                database[name].insert_one(document, session=session)


def main():
    args = parse_args()
    report = {
        "mode": "apply" if args.apply else "dry-run",
        "backup": {},
        "identity": "NOT VERIFIED",
        "target": "NOT VERIFIED",
        "blockers": [],
    }
    source, backup_report = load_backups(args.backup_dir)
    report["backup"] = backup_report
    if source is None:
        report["blockers"].append(backup_report["reason"])
        print(json.dumps(report, indent=2, sort_keys=True))
        return 2
    if args.atlas_db != "careerpilot":
        report["blockers"].append("--atlas-db careerpilot is required.")
    if not atlas_url():
        report["blockers"].append(f"{ATLAS_URL_ENV} must explicitly identify the Atlas target.")
    if report["blockers"]:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 2

    uid, verified_email = verify_identity(args.expected_email)
    report["identity"] = {"status": "VERIFIED"}
    client, database = establish_target(args.atlas_db)
    report["target"] = {"status": "ATLAS_VERIFIED", "database": args.atlas_db}
    try:
        plan = build_plan(source, database, uid)
        report["plan"] = {
            key: value for key, value in plan.items() if not key.startswith("_")
        }
        if args.apply:
            verify_readiness(args.readiness_file, args.backup_dir)
            if args.approval_manifest_sha256 != plan["manifest_sha256"]:
                raise ValueError("Approval manifest hash does not match the current dry-run.")
            if args.confirm_email != verified_email:
                raise ValueError("--confirm-email must exactly match the verified Firebase email.")
            if args.confirm_target != args.atlas_db:
                raise ValueError("--confirm-target must exactly match the verified Atlas database.")
            if plan["_blocking_conflicts"]:
                raise ValueError("Unique-index conflicts must be resolved before apply.")
            refs = plan["reference_checks"]
            if any(refs.values()):
                raise ValueError("Reference anomalies must be reviewed before apply.")
            print("Sanitized write target confirmation: MongoDB Atlas / careerpilot.")
            confirmation = input("Type APPLY careerpilot to proceed: ")
            if confirmation != "APPLY careerpilot":
                raise ValueError("Target confirmation did not match; no writes were performed.")
            apply_plan(database, plan, uid)
            report["apply_status"] = "completed"
        else:
            report["apply_status"] = "NOT RUN; review and approve the manifest first."
    finally:
        client.close()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"Migration stopped: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except Exception as exc:
        print(f"Migration stopped ({type(exc).__name__}); details withheld.", file=sys.stderr)
        raise SystemExit(2)
