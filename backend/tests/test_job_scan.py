import mongomock

from app import job_scan
from app.jobspy_search import SUPPORTED_SITES


def test_scan_queries_and_saves_only_supported_job_boards(monkeypatch):
    db = mongomock.MongoClient(tz_aware=True)["test"]
    db.profiles.insert_one({"target_roles": ["Software Engineer"], "skills": []})
    db.preferences.insert_one({"locations": ["Bengaluru, India"]})
    searched_sites = []

    def fake_search(*, site, what, where):
        searched_sites.append((site, what, where))
        return {
            "source": site,
            "results": [{
                "source_job_id": f"{site}-1",
                "title": "Software Engineer",
                "company": "Example Ltd",
                "url": f"https://{site}.example/jobs/1",
                "description": "Build software.",
            }],
        }

    monkeypatch.setattr(job_scan, "search_jobspy_jobs", fake_search)

    result = job_scan.scan_and_save(db)

    assert [site for site, _, _ in searched_sites] == list(SUPPORTED_SITES)
    assert all(
        (what, where) == ("Software Engineer", "Bengaluru, India")
        for _, what, where in searched_sites
    )
    assert result == {
        "saved": len(SUPPORTED_SITES),
        "skipped": 0,
        "sources": {site: 1 for site in SUPPORTED_SITES},
    }
    assert {job["source"] for job in db.jobs.find()} == set(SUPPORTED_SITES)


def test_scan_defaults_to_belgium_when_no_preferred_location_exists(monkeypatch):
    db = mongomock.MongoClient(tz_aware=True)["test"]
    db.profiles.insert_one({"target_roles": ["Software Engineer"], "skills": []})
    locations = []

    def fake_search(*, site, what, where):
        locations.append(where)
        return {"source": site, "results": []}

    monkeypatch.setattr(job_scan, "search_jobspy_jobs", fake_search)

    job_scan.scan_and_save(db)

    assert locations == ["Belgium"] * len(SUPPORTED_SITES)
