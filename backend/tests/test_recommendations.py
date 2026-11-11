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
    candidate_id = ObjectId()
    database.candidates.insert_one(
        {
            "_id": candidate_id,
            "full_name": "Candidate Example",
            "target_roles": ["Candidate Engineer"],
            "skills": ["Go"],
            "location": "Brussels, Belgium",
            "preferred_location": "Brussels, Belgium",
            "archived": False,
        }
    )
    database.resumes.insert_one(
        {
            "_id": ObjectId(),
            "candidate_id": candidate_id,
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


def test_recommendations_use_selected_candidate_and_target_countries(
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

    candidate_id = str(recommendation_db.candidates.find_one({})["_id"])
    response = client.get(
        "/api/jobs/recommendations",
        params={"candidate_id": candidate_id},
    )

    assert response.status_code == 200
    data = response.json()
    assert {call[2] for call in board_calls} == {"Belgium", "Germany"}
    assert all(call[3] == "match" for call in board_calls)
    assert all("Candidate Engineer" in call[1] and "Product Engineer" in call[1] for call in board_calls)
    assert all("Data Engineer" not in call[1] for call in board_calls)
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


def test_recommendations_reject_missing_or_archived_candidate(monkeypatch):
    mongo_client = mongomock.MongoClient(tz_aware=True)
    database = mongo_client["recommendations_without_resume_test"]
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = lambda: database
    archived_id = database.candidates.insert_one(
        {
            "full_name": "Archived Candidate",
            "target_roles": ["Engineer"],
            "skills": ["Python"],
            "archived": True,
        }
    ).inserted_id
    try:
        response = client.get(
            "/api/jobs/recommendations",
            params={"candidate_id": str(ObjectId())},
        )
        archived_response = client.get(
            "/api/jobs/recommendations",
            params={"candidate_id": str(archived_id)},
        )
    finally:
        if previous is not None:
            app.dependency_overrides[get_db] = previous
        else:
            app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 404
    assert response.json()["detail"] == "Candidate not found."
    assert archived_response.status_code == 404
