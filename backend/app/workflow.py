"""Application status rules: the statuses, who can move where, and the
one-time migration from the old statuses. No FastAPI or database imports
here, so these rules are easy to test on their own."""

from datetime import datetime

STATUSES = (
    "discovered",
    "saved",
    "under_review",
    "ready_for_approval",
    "approved",
    "submitted",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
)

# Only the approval workflow (Stage 6) may ever set these. The normal
# status endpoint must refuse them every time.
GATED = frozenset({"approved", "submitted"})

GATED_BLOCKED = (
    "'approved' and 'submitted' require the explicit approval workflow "
    "(not yet built). They cannot be set directly."
)

# Every move that is structurally possible. Gated targets are listed so the
# full journey is visible, but allowed_next() filters them out.
TRANSITIONS = {
    "discovered": ["saved", "withdrawn"],
    "saved": ["under_review", "withdrawn"],
    "under_review": ["saved", "ready_for_approval", "withdrawn"],
    "ready_for_approval": ["under_review", "approved", "withdrawn"],
    "approved": ["ready_for_approval", "submitted", "withdrawn"],
    "submitted": ["interview", "rejected", "withdrawn"],
    "interview": ["offer", "rejected", "withdrawn"],
    "offer": ["withdrawn"],
    "rejected": [],
    "withdrawn": [],
}

# New applications may only start here.
CREATABLE = ("discovered", "saved")


def allowed_next(status: str) -> list[str]:
    """Moves the API will accept right now (gated ones removed)."""
    return [s for s in TRANSITIONS.get(status, []) if s not in GATED]


def gated_next(status: str) -> list[str]:
    """Moves that exist but are locked until the approval workflow is built."""
    return [s for s in TRANSITIONS.get(status, []) if s in GATED]


def history_entry(
    from_status: str | None,
    to_status: str,
    at: datetime,
    actor: str = "user",
    note: str = "",
) -> dict:
    return {
        "from_status": from_status,
        "to_status": to_status,
        "at": at,
        "actor": actor,
        "note": note,
    }


# ---------------------------------------------------------
# Migration from the old statuses
# ---------------------------------------------------------

# needs_review goes to under_review (not ready_for_approval) on purpose:
# nothing has confirmed those applications are ready for approval.
LEGACY_STATUS_MAP = {
    "saved": "saved",
    "drafting": "under_review",
    "needs_review": "under_review",
    "applied": "submitted",
    "interview": "interview",
    "offer": "offer",
    "rejected": "rejected",
    "withdrawn": "withdrawn",
}


def migrate_statuses(db, now: datetime, dry_run: bool = False) -> dict:
    """Move old applications to the new statuses and start their history.

    Safe to run many times: it only touches applications that have no
    status_history yet. The old status is kept in `legacy_status`, and
    `updated_at` is left alone. Statuses it does not recognise are not
    touched, only reported.
    """
    result = {"migrated": 0, "history_started": 0, "unknown": []}

    for doc in list(db.applications.find({"status_history": {"$exists": False}})):
        old = doc.get("status")
        new = LEGACY_STATUS_MAP.get(old)
        if new is None:
            result["unknown"].append(str(old))
            continue

        created = doc.get("created_at") or now
        history = [
            history_entry(
                None,
                old,
                created,
                actor="system",
                note="Created (history added later)",
            )
        ]
        changes = {"status_history": history}

        if new != old:
            history.append(
                history_entry(
                    old,
                    new,
                    now,
                    actor="migration",
                    note=f"Migrated from old status '{old}'",
                )
            )
            changes["status"] = new
            changes["legacy_status"] = old
            result["migrated"] += 1
        else:
            result["history_started"] += 1

        if not dry_run:
            db.applications.update_one(
                {"_id": doc["_id"], "status_history": {"$exists": False}},
                {"$set": changes},
            )

    return result