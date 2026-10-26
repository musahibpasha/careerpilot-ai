import mongomock
import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.matching import score_job

PROFILE = {"skills": ["React", "TS"], "target_roles": ["Front-End Developer"]}


def factor(result, key):
    return next(f for f in result["breakdown"] if f["key"] == key)


def test_skills_only_job_is_scored_on_skills_and_has_medium_confidence():
    r = score_job({"title": "Chef", "skills": ["React", "Go"]}, {"skills": ["react"]}, {})
    assert r["score"] == 50.0
    assert r["confidence"] == "medium"  # skills alone cover half the weight
    assert factor(r, "location")["status"] == "unknown"
    assert factor(r, "salary")["score"] is None


def test_must_have_and_nice_to_have_are_weighted_and_gaps_listed():
    job = {"title": "Dev", "skills": ["React", "GraphQL"], "nice_to_have_skills": ["Jest"]}
    r = score_job(job, {"skills": ["react"]}, {})
    skills = factor(r, "skills")
    assert skills["score"] == 40.0  # 0.8 * 1/2 + 0.2 * 0/1
    assert skills["details"]["missing_must_have"] == ["graphql"]
    assert skills["details"]["missing_nice_to_have"] == ["jest"]
    assert "Potential gap: graphql" in r["reasons"]


def test_description_only_scan_counts_for_half_weight_and_ignores_ambiguous_words():
    job = {"title": "Dev", "description": "We use ReactJS and TypeScript. Time to go home."}
    r = score_job(job, {"skills": ["React", "TS", "Go"]}, {})
    skills = factor(r, "skills")
    assert skills["details"]["basis"] == "description"
    assert skills["details"]["matched"] == ["react", "typescript"]  # "go" is too ambiguous to scan
    assert skills["weight"] == 25


def test_role_fit_handles_spelling_and_level_words():
    good = score_job({"title": "Senior Frontend Engineer", "skills": ["x"]}, PROFILE, {})
    bad = score_job({"title": "Accountant", "skills": ["x"]}, PROFILE, {})
    assert factor(good, "role")["status"] == "good"
    assert factor(bad, "role")["status"] == "poor"


def test_single_letter_title_is_not_judged_for_role_fit():
    r = score_job({"title": "R", "skills": ["x"]}, PROFILE, {})
    assert factor(r, "role")["status"] == "unknown"


def test_remote_job_ignores_city_but_checks_work_style():
    prefs = {"locations": ["Pune"], "work_arrangements": ["remote"]}
    ok = score_job({"title": "Dev", "skills": ["x"], "location": "Remote"}, PROFILE, prefs)
    no = score_job({"title": "Dev", "skills": ["x"], "location": "Remote"}, PROFILE,
                   {"locations": ["Pune"], "work_arrangements": ["onsite"]})
    assert factor(ok, "location")["score"] == 100.0
    assert factor(no, "location")["score"] == 0.0


def test_city_aliases_match():
    r = score_job({"title": "Dev", "skills": ["x"], "location": "Bengaluru, India"}, PROFILE,
                  {"locations": ["Bangalore"]})
    assert factor(r, "location")["score"] == 100.0


@pytest.mark.parametrize("job_level,expected", [("mid", 100.0), ("senior", 50.0), ("lead", 0.0)])
def test_experience_distance(job_level, expected):
    r = score_job({"title": "Dev", "skills": ["x"], "experience_level": job_level}, PROFILE,
                  {"experience_level": "mid"})
    assert factor(r, "experience")["score"] == expected


def test_experience_level_is_guessed_from_title_and_says_so():
    r = score_job({"title": "Junior Developer", "skills": ["x"]}, PROFILE, {"experience_level": "entry"})
    assert factor(r, "experience")["score"] == 100.0
    assert "guessed from the title" in factor(r, "experience")["reason"]


def test_salary_converts_period_and_skips_other_currencies():
    prefs = {"salary_min": 1_200_000, "salary_currency": "INR", "salary_period": "year"}
    monthly = score_job({"title": "D", "skills": ["x"], "salary_max": 120_000, "salary_currency": "INR",
                         "salary_period": "month"}, PROFILE, prefs)
    low = score_job({"title": "D", "skills": ["x"], "salary_max": 400_000, "salary_currency": "INR"},
                    PROFILE, prefs)
    usd = score_job({"title": "D", "skills": ["x"], "salary_max": 90_000, "salary_currency": "USD"},
                    PROFILE, prefs)
    assert factor(monthly, "salary")["score"] == 100.0
    assert factor(low, "salary")["score"] == 0.0
    assert factor(usd, "salary")["status"] == "unknown"
    assert "different currencies" in factor(usd, "salary")["reason"]


def test_full_picture_gives_high_confidence_and_weighted_score():
    job = {"title": "Front-End Developer", "skills": ["React", "TypeScript"], "location": "Pune",
           "work_arrangement": "hybrid", "experience_level": "mid",
           "salary_max": 100, "salary_currency": "USD"}
    prefs = {"locations": ["Pune"], "work_arrangements": ["hybrid"], "experience_level": "mid",
             "salary_min": 100, "salary_currency": "USD"}
    r = score_job(job, PROFILE, prefs)
    assert r["score"] == 100.0 and r["confidence"] == "high"
    assert sum(f["weight"] for f in r["breakdown"]) == 100


def test_nothing_to_compare_scores_zero_with_a_message():
    r = score_job({"title": "", "skills": []}, {}, {})
    assert r["score"] == 0.0 and r["confidence"] == "low"


# --- API wiring -------------------------------------------------------------

_db = mongomock.MongoClient(tz_aware=True)["matching"]


@pytest.fixture()
def client():
    _db.client.drop_database("matching")
    app.dependency_overrides[get_db] = lambda: _db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def profile(**kw):
    p = {"full_name": "J", "email": "j@example.com", "phone": "+91 98765 43210", "location": "Pune",
         "target_roles": ["Front-End Developer"], "skills": ["React"], "experience_summary": "x",
         "education": [{"institution": "ABC", "degree": "BCA"}],
         "projects": [{"name": "P", "description": "d"}]}
    p.update(kw)
    return p


def test_new_job_gets_breakdown_and_changes_rescore_it(client):
    client.put("/api/profile", json=profile())
    job = client.post("/api/jobs", json={"title": "Front-End Developer", "company": "A",
                                         "skills": ["React", "Vue"], "salary_min": 5,
                                         "salary_max": 9, "salary_currency": "usd"}).json()
    assert job["match_score"] == 61.5  # (50*0.5 + 15*1.0) / 65
    assert job["match_confidence"] == "medium"
    assert {f["key"] for f in job["match_breakdown"]} == {"skills", "role", "location", "experience", "salary"}
    assert job["salary_currency"] == "USD"

    client.put("/api/profile", json=profile(skills=["React", "Vue"]))
    after = client.get("/api/jobs").json()[0]
    assert after["match_score"] == 100.0


def test_salary_input_is_validated(client):
    base = {"title": "D", "company": "A"}
    assert client.post("/api/jobs", json={**base, "salary_min": 9, "salary_max": 5,
                                          "salary_currency": "USD"}).status_code == 422
    assert client.post("/api/jobs", json={**base, "salary_max": 5}).status_code == 422


def test_rescore_endpoint_fills_in_old_jobs(client):
    _db.jobs.insert_one({"title": "Dev", "company": "Old", "skills": ["react"],
                         "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc)})
    client.put("/api/profile", json=profile())
    _db.jobs.update_one({}, {"$unset": {"match_breakdown": ""}})
    assert client.post("/api/jobs/rescore").json() == {"rescored": 1}
    assert client.get("/api/jobs").json()[0]["match_breakdown"]