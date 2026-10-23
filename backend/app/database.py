from pymongo import MongoClient
from pymongo.database import Database
from app.config import settings

# MongoClient connects lazily, so importing this module never fails if MongoDB is down.
_client = MongoClient(settings.mongodb_url, tz_aware=True, serverSelectionTimeoutMS=5000)


def get_db() -> Database:
    return _client[settings.mongodb_db]


def ensure_indexes(db: Database) -> None:
    """Idempotent. Unique indexes only apply when the field is actually a string."""
    db.jobs.create_index(
        [("source", 1), ("source_job_id", 1)], unique=True, name="uq_job_source_id",
        partialFilterExpression={"source_job_id": {"$type": "string"}},
    )
    db.jobs.create_index(
        "url", unique=True, name="uq_job_url",
        partialFilterExpression={"url": {"$type": "string"}},
    )
    db.jobs.create_index([("created_at", -1)])
    db.applications.create_index("job_id")
    db.applications.create_index([("updated_at", -1)])
