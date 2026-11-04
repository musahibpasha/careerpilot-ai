import mongomock
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app


client = TestClient(app)


@pytest.fixture
def recommendation_db():
    mongo_client = mongomock.MongoClient(tz_aware=True)
    database = mongo_client["recommendations_test"]
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = lambda: database
    database.resumes.insert_one(
        {
            "_id": ObjectId(),
            "label": "Active resume",
            "filename": "resume.pdf",
            "data": b"%PDF-1.4",
            "is_active": True,
            "extracted": {
                "target_roles": ["Product Engineer"],
                "skills": ["Python", "TypeScript"],
                "work_experience": [{"title": "Platform Engineer"}],
            },
            "extracted_at": None,
        }
    )
    database.profiles.insert_one(
        {
            "target_roles": ["Data Engineer"],
            "skills": ["Python"],
        }
    )
    database.preferences.insert_one(
        {
            "locations": ["Bengaluru, India"],
            "experience_level": "",
            "work_arrangements": [],
        }
    )
    yield database
    if previous is not None:
        app.dependency_overrides[get_db] = previous
    else:
        app.dependency_overrides.pop(get_db, None)


def _listing(source_job_id, location, url):
    return {
        "source_job_id": source_job_id,
        "title": "Data Engineer",
        "company": "Example Ltd",
        "location": location,
        "url": url,
        "description": "Build data platforms with Python.",
        "skills": ["Python"],
    }


def test_recommendations_use_resume_profile_and_target_countries(
    recommendation_db, monkeypatch
):
    from app import main

    board_calls = []
    jobicy_calls = []

    def search_board(*, site, what, where, sort_by):
        board_calls.append((site, what, where, sort_by))
        if site == "indeed" and where == "Germany":
            raise RuntimeError("Germany search unavailable")
        listing = _listing(
            f"{site}-{where}",
            "Brussels, Belgium" if where == "Belgium" else "Berlin, Germany",
            f"https://{site}.example.test/jobs/{where.casefold()}",
        )
        listing["company"] = f"{site.title()} Example"
        return {
            "results": [listing]
        }

    def search_jobicy(*, what, where, geo):
        jobicy_calls.append((what, where, geo))
        country = where.split(",", maxsplit=1)[0]
        city = "Brussels" if country == "Belgium" else "Berlin"
        return {
            "results": [
                _listing(
                    f"jobicy-{country}-{what}",
                    f"{city}, {country}",
                    f"https://jobicy.com/jobs/{country.casefold()}-product-engineer",
                )
            ]
        }

    monkeypatch.setattr(main, "search_jobspy_jobs", search_board)
    monkeypatch.setattr(main.jobicy, "search_jobicy_jobs", search_jobicy)

    response = client.get("/api/jobs/recommendations")

    assert response.status_code == 200
    data = response.json()
    assert {call[2] for call in board_calls} == {"Belgium", "Germany"}
    assert all(call[3] == "match" for call in board_calls)
    assert all("Data Engineer" in call[1] and "Product Engineer" in call[1] for call in board_calls)
    assert {call[2] for call in jobicy_calls} == {"europe"}
    assert any("brussels" in call[1].casefold() for call in jobicy_calls)
    assert any("berlin" in call[1].casefold() for call in jobicy_calls)
    assert "naukri" not in {call[0] for call in board_calls}
    assert {job["source"] for job in data["results"]} >= {
        "linkedin",
        "glassdoor",
        "jobicy",
    }
    assert {job["location"].split(",")[-1].strip() for job in data["results"]} == {
        "Belgium",
        "Germany",
    }
    assert "Indeed (Germany)" in data["source_failures"]
    assert "indeed" in data["successful_sources"]
    assert recommendation_db.profiles.find_one({})["skills"] == ["Python"]


def test_recommendations_require_an_active_resume(monkeypatch):
    mongo_client = mongomock.MongoClient(tz_aware=True)
    database = mongo_client["recommendations_without_resume_test"]
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = lambda: database
    try:
        response = client.get("/api/jobs/recommendations")
    finally:
        if previous is not None:
            app.dependency_overrides[get_db] = previous
        else:
            app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 409
    assert "active resume" in response.json()["detail"].lower()
