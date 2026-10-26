import mongomock
import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.preferences import clean_preferences, preferences_problems

client = TestClient(app)


@pytest.fixture()
def db():
    """A fresh database per test. Restores any override set by other test files."""
    database = mongomock.MongoClient(tz_aware=True)["preferences_test"]
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = lambda: database
    yield database
    if previous is not None:
        app.dependency_overrides[get_db] = previous
    else:
        app.dependency_overrides.pop(get_db, None)


def valid_profile():
    return {
        "full_name": "Jane Example",
        "email": "jane@example.com",
        "phone": "+91 98765 43210",
        "location": "Bangalore",
        "target_roles": ["Front-End Developer"],
        "skills": ["React"],
        "experience_summary": "Front-end developer.",
        "education": [{"institution": "ABC College", "degree": "BCA"}],
        "projects": [{"name": "NewsApp", "description": "Built a news app."}],
    }


# ---------- the rules on their own ----------

def test_clean_trims_dedupes_and_uppercases():
    cleaned = clean_preferences({
        "locations": [" Pune ", "pune", "", "Remote   India"],
        "work_arrangements": ["remote", "remote", "hybrid"],
        "salary_currency": " inr ",
    })

    assert cleaned["locations"] == ["Pune", "Remote India"]
    assert cleaned["work_arrangements"] == ["remote", "hybrid"]
    assert cleaned["salary_currency"] == "INR"


def test_empty_preferences_have_no_problems():
    assert preferences_problems(clean_preferences({})) == []


def test_salary_problems_are_reported():
    assert preferences_problems(
        {"locations": [], "salary_min": 10, "salary_max": 5, "salary_currency": "INR"}
    ) == ["Maximum salary cannot be lower than minimum salary"]

    assert len(
        preferences_problems({"locations": [], "salary_min": 10, "salary_currency": ""})
    ) == 1


# ---------- the API ----------

def test_get_returns_empty_defaults_before_anything_is_saved(db):
    response = client.get("/api/preferences")

    assert response.status_code == 200
    data = response.json()
    assert data["locations"] == []
    assert data["experience_level"] == ""
    assert data["work_arrangements"] == []
    assert data["salary_min"] is None
    assert data["salary_max"] is None
    assert data["salary_period"] == "year"
    assert data["updated_at"] is None


def test_save_and_read_back(db):
    response = client.put(
        "/api/preferences",
        json={
            "locations": [" Bengaluru ", "bengaluru", "Remote"],
            "experience_level": "entry",
            "work_arrangements": ["remote", "hybrid", "remote"],
            "salary_min": 600000,
            "salary_max": 1200000,
            "salary_currency": "inr",
            "salary_period": "year",
        },
    )

    assert response.status_code == 200
    saved = response.json()
    assert saved["locations"] == ["Bengaluru", "Remote"]
    assert saved["work_arrangements"] == ["remote", "hybrid"]
    assert saved["salary_currency"] == "INR"
    assert saved["updated_at"] is not None

    assert client.get("/api/preferences").json() == saved


def test_every_field_is_optional(db):
    response = client.put("/api/preferences", json={})

    assert response.status_code == 200


def test_saving_twice_keeps_a_single_record(db):
    client.put("/api/preferences", json={"experience_level": "entry"})
    client.put("/api/preferences", json={"experience_level": "mid"})

    assert db.preferences.count_documents({}) == 1
    assert client.get("/api/preferences").json()["experience_level"] == "mid"


@pytest.mark.parametrize(
    "body",
    [
        {"salary_min": 100, "salary_max": 50, "salary_currency": "INR"},
        {"salary_min": 100},
        {"salary_max": 100, "salary_currency": "rupees"},
        {"salary_min": -5, "salary_currency": "INR"},
        {"experience_level": "wizard"},
        {"work_arrangements": ["underwater"]},
        {"salary_period": "week"},
        {"locations": ["x" * 101]},
    ],
)
def test_bad_preferences_are_refused_and_not_saved(db, body):
    response = client.put("/api/preferences", json=body)

    assert response.status_code == 422
    assert db.preferences.count_documents({}) == 0


def test_preferences_can_be_saved_without_a_profile(db):
    response = client.put("/api/preferences", json={"locations": ["Pune"]})

    assert response.status_code == 200
    assert db.profiles.count_documents({}) == 0


def test_preferences_change_match_scores_but_not_the_profile(db):
    assert client.put("/api/profile", json=valid_profile()).status_code == 200
    job = client.post(
        "/api/jobs",
        json={
            "title": "Front-End Developer",
            "company": "Acme",
            "skills": ["React"],
            "location": "Pune",
            "work_arrangement": "onsite",
        },
    ).json()
    assert job["match_score"] == 100.0  # no preferences yet, so only skills and role are judged
    profile_before = db.profiles.find_one({})

    response = client.put(
        "/api/preferences",
        json={"locations": ["Mumbai"], "work_arrangements": ["remote"]},
    )

    assert response.status_code == 200
    after = next(j for j in client.get("/api/jobs").json() if j["id"] == job["id"])
    assert after["match_score"] < job["match_score"]
    assert any("Location" in r for r in after["match_reasons"])
    assert db.profiles.find_one({}) == profile_beforeb