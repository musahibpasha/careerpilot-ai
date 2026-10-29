"""Firebase ID-token verification for protected API requests."""

import json
from functools import lru_cache

import firebase_admin
from fastapi import Header, HTTPException
from firebase_admin import auth, credentials
from firebase_admin.exceptions import FirebaseError

from app.config import settings


@lru_cache(maxsize=1)
def _firebase_app():
    if not settings.firebase_project_id or not settings.firebase_service_account_json:
        raise HTTPException(
            status_code=503,
            detail="Firebase authentication is not configured on the backend.",
        )

    try:
        service_account = json.loads(settings.firebase_service_account_json)
        if service_account.get("project_id") != settings.firebase_project_id:
            raise ValueError("Firebase service account project does not match the configured project.")
        credential = credentials.Certificate(service_account)
        return firebase_admin.initialize_app(
            credential,
            {"projectId": settings.firebase_project_id},
            name="careerpilot",
        )
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Firebase Admin credentials are invalid.",
        ) from exc


def get_current_user_id(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Sign in to continue.")

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Sign in to continue.")

    try:
        claims = auth.verify_id_token(token, app=_firebase_app())
    except (auth.ExpiredIdTokenError, auth.RevokedIdTokenError, auth.InvalidIdTokenError) as exc:
        raise HTTPException(status_code=401, detail="Your sign-in has expired. Sign in again.") from exc
    except FirebaseError as exc:
        raise HTTPException(
            status_code=503,
            detail="Firebase token verification is temporarily unavailable.",
        ) from exc

    user_id = claims.get("uid")
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(status_code=401, detail="Sign-in token has no user identity.")
    if not claims.get("email_verified", False):
        raise HTTPException(
            status_code=403,
            detail="Verify your email address before using CareerPilot.",
        )
    return user_id
