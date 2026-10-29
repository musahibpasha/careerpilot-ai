from copy import deepcopy

from fastapi import Depends
from pymongo import MongoClient
from pymongo.database import Database
from app.auth import get_current_user_id
from app.config import settings

# MongoClient connects lazily, so importing this module never fails if MongoDB is down.
_client = MongoClient(settings.mongodb_url, tz_aware=True, serverSelectionTimeoutMS=5000)


class UserScopedCollection:
    """Apply the authenticated user's ownership boundary to collection access."""

    def __init__(self, collection, user_id: str):
        self._collection = collection
        self._user_id = user_id

    def _query(self, query: dict | None) -> dict:
        if not query:
            return {"owner_uid": self._user_id}
        return {"$and": [query, {"owner_uid": self._user_id}]}

    def _update(self, update):
        if isinstance(update, list):
            raise TypeError("Aggregation-pipeline updates are not supported for user data.")
        safe_update = deepcopy(update)
        if any(not key.startswith("$") for key in safe_update):
            safe_update["owner_uid"] = self._user_id
            return safe_update

        def is_owner_path(path: str) -> bool:
            return path == "owner_uid" or path.startswith("owner_uid.")

        for operator, fields in safe_update.items():
            if not isinstance(fields, dict):
                continue
            for path, target in list(fields.items()):
                if is_owner_path(path) or (
                    operator == "$rename"
                    and isinstance(target, str)
                    and is_owner_path(target)
                ):
                    del fields[path]
        safe_update.setdefault("$setOnInsert", {})["owner_uid"] = self._user_id
        return safe_update

    def find(self, query: dict | None = None, *args, **kwargs):
        return self._collection.find(self._query(query), *args, **kwargs)

    def find_one(self, query: dict | None = None, *args, **kwargs):
        return self._collection.find_one(self._query(query), *args, **kwargs)

    def insert_one(self, document: dict, *args, **kwargs):
        owned = deepcopy(document)
        owned["owner_uid"] = self._user_id
        return self._collection.insert_one(owned, *args, **kwargs)

    def insert_many(self, documents, *args, **kwargs):
        owned = []
        for document in documents:
            item = deepcopy(document)
            item["owner_uid"] = self._user_id
            owned.append(item)
        return self._collection.insert_many(owned, *args, **kwargs)

    def update_one(self, query: dict, update, *args, **kwargs):
        return self._collection.update_one(
            self._query(query), self._update(update), *args, **kwargs
        )

    def update_many(self, query: dict, update, *args, **kwargs):
        return self._collection.update_many(
            self._query(query), self._update(update), *args, **kwargs
        )

    def replace_one(self, query: dict, replacement: dict, *args, **kwargs):
        owned = deepcopy(replacement)
        owned["owner_uid"] = self._user_id
        return self._collection.replace_one(self._query(query), owned, *args, **kwargs)

    def delete_one(self, query: dict, *args, **kwargs):
        return self._collection.delete_one(self._query(query), *args, **kwargs)

    def delete_many(self, query: dict, *args, **kwargs):
        return self._collection.delete_many(self._query(query), *args, **kwargs)

    def count_documents(self, query: dict, *args, **kwargs):
        return self._collection.count_documents(self._query(query), *args, **kwargs)

    def distinct(self, key: str, query: dict | None = None, *args, **kwargs):
        return self._collection.distinct(key, self._query(query), *args, **kwargs)

    def find_one_and_update(self, query: dict, update, *args, **kwargs):
        return self._collection.find_one_and_update(
            self._query(query), self._update(update), *args, **kwargs
        )

    def find_one_and_replace(self, query: dict, replacement: dict, *args, **kwargs):
        owned = deepcopy(replacement)
        owned["owner_uid"] = self._user_id
        return self._collection.find_one_and_replace(
            self._query(query), owned, *args, **kwargs
        )

    def find_one_and_delete(self, query: dict, *args, **kwargs):
        return self._collection.find_one_and_delete(self._query(query), *args, **kwargs)

class UserScopedDatabase:
    """Expose only the app's personal collections, scoped to one Firebase UID."""

    _COLLECTIONS = {"jobs", "applications", "profiles", "preferences", "resumes"}

    def __init__(self, database: Database, user_id: str):
        self._database = database
        self._user_id = user_id

    def __getattr__(self, collection_name: str) -> UserScopedCollection:
        if collection_name not in self._COLLECTIONS:
            raise AttributeError(f"Collection {collection_name!r} is not available.")
        return UserScopedCollection(
            getattr(self._database, collection_name),
            self._user_id,
        )


def get_base_db() -> Database:
    return _client[settings.mongodb_db]


def get_db(user_id: str = Depends(get_current_user_id)) -> UserScopedDatabase:
    return UserScopedDatabase(get_base_db(), user_id)


def ensure_indexes(db: Database) -> None:
    """Create indexes that preserve uniqueness within, not across, user workspaces."""
    db.jobs.create_index(
        [("owner_uid", 1), ("source", 1), ("source_job_id", 1)],
        unique=True,
        name="uq_job_owner_source_id",
        partialFilterExpression={"source_job_id": {"$type": "string"}},
    )
    db.jobs.create_index(
        [("owner_uid", 1), ("url", 1)],
        unique=True,
        name="uq_job_owner_url",
        partialFilterExpression={"url": {"$type": "string"}},
    )
    db.jobs.create_index([("owner_uid", 1), ("created_at", -1)])
    db.applications.create_index([("owner_uid", 1), ("job_id", 1)])
    db.applications.create_index([("owner_uid", 1), ("updated_at", -1)])
    db.profiles.create_index("owner_uid", unique=True, name="uq_profile_owner")
    db.preferences.create_index("owner_uid", unique=True, name="uq_preferences_owner")
    db.resumes.create_index([("owner_uid", 1), ("uploaded_at", -1)])
