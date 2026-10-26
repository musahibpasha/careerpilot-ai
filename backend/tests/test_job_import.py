import mongomock
import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.job_import import extract_job
from app.main import app

INDIA = """Senior Frontend Developer at Acme Technologies
Location: Bengaluru, India (Hybrid)
Salary: ₹18 - 24 LPA

About Acme Technologies
We build tools for logistics teams.

Requirements:
- 5+ years of experience with React and TypeScript
- Strong HTML5, CSS3 and REST API knowledge
- Experience with Git and CI/CD

Nice to have:
- GraphQL
- Docker
Apply: https://acme.example/jobs/123."""

US = """Backend Engineer
Company: Globex
Remote (US)
Compensation: $120,000 - $150,000 per year
You'll work with Python, Django, PostgreSQL and AWS. Kubernetes experience is a plus.
We offer free lunch and a $5000 learning budget."""

JUNIOR = """Junior Data Analyst - Initech
Location: Pune
Stipend: Rs. 25,000 per month
Skills: SQL, Excel, Tableau, Python, R
Work type: On-site"""


def draft(text, **kw):
    return extract_job(text, **kw)["draft"]


def test_indian_style_posting_is_read_fully():
    r = extract_job(INDIA)
    d = r["draft"]
    assert (d["title"], d["company"]) == ("Senior Frontend Developer", "Acme Technologies")
    assert d["location"] == "Bengaluru, India (Hybrid)"
    assert d["work_arrangement"] == "hybrid" and d["experience_level"] == "senior"
    assert (d["salary_min"], d["salary_max"], d["salary_currency"], d["salary_period"]) == (
        1_800_000, 2_400_000, "INR", "year")
    assert {"react", "typescript", "html", "css", "rest", "git", "ci/cd"} <= set(d["skills"])
    assert d["nice_to_have_skills"] == ["graphql", "docker"]
    assert d["url"] == "https://acme.example/jobs/123"  # trailing full stop is not part of the link
    assert r["missing"] == []


def test_us_style_posting_salary_range_and_sentence_level_plus():
    d = draft(US)
    assert (d["title"], d["company"]) == ("Backend Engineer", "Globex")
    assert (d["salary_min"], d["salary_max"], d["salary_currency"]) == (120_000, 150_000, "USD")
    assert d["work_arrangement"] == "remote" and d["location"] == "Remote"
    assert d["skills"] == ["python", "django", "postgresql", "aws"]
    assert d["nice_to_have_skills"] == ["kubernetes"]  # only the "is a plus" sentence


def test_monthly_stipend_company_in_title_and_list_only_skills():
    d = draft(JUNIOR)
    assert (d["title"], d["company"]) == ("Junior Data Analyst", "Initech")
    assert (d["salary_min"], d["salary_max"], d["salary_currency"], d["salary_period"]) == (
        25_000, None, "INR", "month")
    assert d["experience_level"] == "entry" and d["work_arrangement"] == "onsite"
    assert {"sql", "excel", "tableau", "python", "r"} <= set(d["skills"])


def test_plain_english_words_are_not_mistaken_for_skills():
    d = draft("Office Manager\nWe go the extra mile. Next steps: express your interest and excel at "
              "the rest of the work. A swift start is needed in the east of the city.")
    assert d["skills"] == []
    assert set(draft("Mobile Developer\nSkills: Kotlin, Swift, Go")["skills"]) == {"kotlin", "swift", "go"}


def test_lone_amounts_without_pay_words_are_not_salary():
    d = draft("Engineer\nWe raised $5,000,000 in funding and have 500 employees. Hourly bonus $25 per hour.")
    assert d["salary_min"] is None and d["salary_max"] is None and d["salary_currency"] == ""


def test_up_to_means_a_maximum_only():
    d = draft("Designer\nSalary up to $90,000 per year depending on experience")
    assert d["salary_min"] is None and d["salary_max"] == 90_000


def test_years_of_experience_set_the_level_when_the_title_does_not():
    assert draft("Developer\n3 years of experience required")["experience_level"] == "mid"
    assert draft("Developer\n10+ years of experience")["experience_level"] == "lead"
    assert draft("Intern Developer\n2 years")["experience_level"] == "intern"


def test_a_sentence_is_not_used_as_the_title_and_missing_fields_are_listed():
    r = extract_job("We are looking for someone who loves cooking and good coffee, lots of it.")
    assert r["draft"]["title"] == ""
    assert "title" in r["missing"] and r["found"] == []


def test_given_url_wins_over_a_link_in_the_text():
    d = draft(INDIA, url="https://jobs.example/mine")
    assert d["url"] == "https://jobs.example/mine"


def test_odd_text_never_crashes():
    for text in ["\n\n   \n" + "x" * 30, "🔥" * 50, "₹" * 40, "$" + "9" * 40 + " per year salary", "-" * 100]:
        extract_job(text)


# --- API ---------------------------------------------------------------------

_db = mongomock.MongoClient(tz_aware=True)["import"]


@pytest.fixture()
def client():
    _db.client.drop_database("import")
    app.dependency_overrides[get_db] = lambda: _db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def test_preview_returns_a_draft_and_saves_nothing(client):
    r = client.post("/api/jobs/import-preview", json={"text": INDIA})
    assert r.status_code == 200
    body = r.json()
    assert body["draft"]["title"] == "Senior Frontend Developer"
    assert "salary" in body["found"] and body["missing"] == []
    assert _db.jobs.count_documents({}) == 0


def test_preview_rejects_empty_short_and_huge_text(client):
    assert client.post("/api/jobs/import-preview", json={"text": ""}).status_code == 422
    assert client.post("/api/jobs/import-preview", json={"text": "too short"}).status_code == 422
    assert client.post("/api/jobs/import-preview", json={"text": " " * 40}).status_code == 422
    assert client.post("/api/jobs/import-preview", json={"text": "a" * 20_001}).status_code == 422


def test_a_previewed_draft_can_be_saved_as_a_scored_job(client):
    client.put("/api/profile", json={
        "full_name": "J", "email": "j@example.com", "phone": "+91 98765 43210", "location": "Pune",
        "target_roles": ["Front-End Developer"], "skills": ["React", "TypeScript"],
        "experience_summary": "x", "education": [{"institution": "A", "degree": "BCA"}],
        "projects": [{"name": "P", "description": "d"}]})
    d = client.post("/api/jobs/import-preview", json={"text": INDIA}).json()["draft"]
    d["url"] = None if not d["url"] else d["url"]
    r = client.post("/api/jobs", json=d)
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["salary_currency"] == "INR" and job["work_arrangement"] == "hybrid"
    assert job["match_score"] > 0 and job["match_breakdown"]