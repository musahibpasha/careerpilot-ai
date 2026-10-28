"""The explicit review -> approve -> submitted path used by the Application Assistant.

Why this exists: workflow.GATED says 'approved' and 'submitted' may only be set
by an explicit approval workflow. The generic endpoints (POST /api/applications,
PATCH .../status) still refuse them for everyone; nothing here changes that.

This module is the one dedicated path, and it is deliberately narrow:
  * approving needs the user's review confirmation of the exact current
    answers (matched by a content hash the server recomputes);
  * 'submitted' needs a prior approval of that same hash, and is only ever
    recorded because the user said they submitted it themselves;
  * every step follows workflow.TRANSITIONS and is written to status_history
    with a matching-status guard, so stale clicks cannot double-apply;
  * changing the answers after approval withdraws the approval.
Nothing here talks to any external site.
"""

from datetime import datetime

from app.workflow import TRANSITIONS, history_entry

# Moves the dedicated path may make. Everything else is refused.
_ROUTE_TO_READY = {
    "discovered": ["saved", "under_review", "ready_for_approval"],
    "saved": ["under_review", "ready_for_approval"],
    "under_review": ["ready_for_approval"],
    "ready_for_approval": [],
}


class ApprovalError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _revision_filter(revision: int | None) -> dict:
    if revision is None:
        return {}
    if revision == 0:
        return {
            "$or": [
                {"assistant.revision": 0},
                {"assistant.revision": {"$exists": False}},
            ]
        }
    return {"assistant.revision": revision}


def _move(
    db,
    app_id,
    current: str,
    to: str,
    at: datetime,
    note: str,
    actor: str,
    *,
    expected_revision: int | None = None,
    extra_set: dict | None = None,
) -> str:
    """One guarded status change + history line. Only moves TRANSITIONS lists."""
    if to not in TRANSITIONS.get(current, []):
        raise ApprovalError(409, f"Invalid status transition: '{current}' cannot transition to '{to}'.")
    query = {"_id": app_id, "status": current, **_revision_filter(expected_revision)}
    update = {
        "$set": {"status": to, "updated_at": at, **(extra_set or {})},
        "$push": {"status_history": history_entry(current, to, at, actor=actor, note=note)},
    }
    res = db.applications.update_one(query, update)
    if res.matched_count == 0:
        raise ApprovalError(409, "This application changed in the meantime. Reload and try again.")
    return to


def approve_reviewed(
    db,
    application: dict,
    digest: str,
    at: datetime,
    expected_revision: int | None = None,
) -> str:
    """Record the user's review confirmation: walk to ready_for_approval, then approved."""
    status = application["status"]
    stored = (application.get("assistant") or {}).get("review") or {}

    if status == "approved" and stored.get("hash") == digest:
        return status  # already approved for exactly this content
    if status not in _ROUTE_TO_READY and status != "approved":
        raise ApprovalError(409, f"An application that is '{status}' cannot be approved.")

    transitions = list(_ROUTE_TO_READY.get(status, []))
    if status == "approved":
        transitions.insert(0, "ready_for_approval")
    transitions.append("approved")

    history = []
    current = application["status"]
    for next_status in transitions:
        if next_status not in TRANSITIONS.get(current, []):
            raise ApprovalError(
                409, f"Invalid status transition: '{current}' cannot transition to '{next_status}'."
            )
        if current == "approved" and next_status == "ready_for_approval":
            actor, note = "system", "Answers changed; approval renewed"
        elif next_status == "approved":
            actor = "user"
            note = (
                "User reviewed the exact answers in the Application Assistant and confirmed them "
                f"(review {digest[:8]})"
            )
        else:
            actor, note = "user", "Prepared in the Application Assistant"
        history.append(history_entry(current, next_status, at, actor=actor, note=note))
        current = next_status

    query = {
        "_id": application["_id"],
        "status": application["status"],
        **_revision_filter(expected_revision),
    }
    result = db.applications.update_one(
        query,
        {
            "$set": {
                "status": "approved",
                "updated_at": at,
                "assistant.review": {"hash": digest, "confirmed_at": at},
                "assistant.current_step": "review",
            },
            "$addToSet": {"assistant.visited": "review"},
            "$push": {"status_history": {"$each": history}},
        },
    )
    if result.matched_count == 0:
        raise ApprovalError(409, "The application changed during review. Reload and review it again.")
    return "approved"


def withdraw_approval(db, application: dict, at: datetime) -> str:
    """Answers changed after approval: approved -> ready_for_approval (an existing allowed move)."""
    if application["status"] != "approved":
        return application["status"]
    return _move(db, application["_id"], "approved", "ready_for_approval", at,
                 "Answers changed after review; approval withdrawn", "system")


def record_submitted(
    db,
    application: dict,
    digest: str,
    at: datetime,
    expected_revision: int | None = None,
) -> str:
    """The user says they submitted on the external site. approved -> submitted."""
    status = application["status"]
    review = (application.get("assistant") or {}).get("review") or {}
    if status == "submitted" or application.get("assistant", {}).get("submitted_at"):
        raise ApprovalError(409, "This application is already recorded as submitted.")
    if status != "approved":
        raise ApprovalError(409, "Review and confirm the application before recording it as submitted.")
    if not review.get("hash") or review["hash"] != digest:
        raise ApprovalError(409, "The answers changed after you confirmed them. Review them again first.")
    return _move(
        db,
        application["_id"],
        "approved",
        "submitted",
        at,
        "User confirmed they submitted this application themselves on the employer's site. "
        "CareerPilot did not submit it and has no confirmation from the employer's site.",
        "user",
        expected_revision=expected_revision,
        extra_set={
            "assistant.submitted_at": at,
            "assistant.current_step": "done",
        },
    )
