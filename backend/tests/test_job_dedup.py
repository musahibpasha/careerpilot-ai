from datetime import datetime, timedelta, timezone

import mongomock
from bson import ObjectId
from fastapi.testclient import TestClient

from app import main
from app.database import UserScopedDatabase, get_db
from app.job_dedup import compare_listings, track_and_deduplicate
from app.main import app


_DESCRIPTION = (
    "We are looking for an experienced software engineer to join our product team. "
    "You will build reliable services, work closely with designers, and collaborate "
    "across teams to deliver secure features for customers. This role works with "
    "cloud infrastructure, automated testing, and modern development practices."
)
_CANDIDATE_ID = "64f000000000000000000001"


def _listing(source, identifier, **overrides):
    domain = {
        "linkedin": "https://www.linkedin.com/jobs/view/",
        "indeed": "https://www.indeed.com/viewjob?jk=",
        "glassdoor": "https://www.glassdoor.com/job-listing/",
    }[source]
    listing = {
        "source": source,
        "source_job_id": identifier,
        "title": "React Developer",
        "company": "ABC Technologies Ltd",
        "location": "Bengaluru, India",
        "url": f"{domain}{identifier}",
        "description": _DESCRIPTION,
        "employment_types": ["Full-time"],
        "published_at": "2026-10-25T00:00:00+00:00",
    }
    listing.update(overrides)
    return listing


def test_linkedin_indeed_and_glassdoor_pairs_group_as_one_job():
    pairs = [
        ("linkedin", "indeed"),
        ("linkedin", "glassdoor"),
        ("indeed", "glassdoor"),
    ]
    for first, second in pairs:
        db = mongomock.MongoClient(tz_aware=True).db
        one = _listing(first, f"{first}-job")
        two = _listing(
            second,
            f"{second}-job",
            company="ABC Technologies, Inc.",
            title=" React Developer! ",
        )

        assert track_and_deduplicate(db, first, [one])[0]["duplicate_status"] == "new"
        results = track_and_deduplicate(db, second, [two])

        assert len(results) == 1
        assert results[0]["duplicate_status"] == "cross_board_duplicate"
        assert set(results[0]["available_sources"]) == {first, second}
        assert {item["url"] for item in results[0]["source_listings"]} == {
            one["url"], two["url"],
        }
        assert db.job_duplicate_groups.count_documents({}) == 1


def test_all_three_boards_share_one_group_and_keep_all_source_links():
    db = mongomock.MongoClient(tz_aware=True).db
    results = []
    for source in ("linkedin", "indeed", "glassdoor"):
        results.append(track_and_deduplicate(
            db, source, [_listing(source, f"{source}-job")]
        )[0])

    assert results[-1]["duplicate_status"] == "cross_board_duplicate"
    assert set(results[-1]["available_sources"]) == {
        "linkedin", "indeed", "glassdoor",
    }
    assert len(results[-1]["source_listings"]) == 3
    assert db.job_duplicate_groups.count_documents({}) == 1


def test_same_board_repost_preserves_original_and_tracks_latest_date():
    db = mongomock.MongoClient(tz_aware=True).db
    original_date = datetime(2026, 10, 1, tzinfo=timezone.utc)
    repost_date = original_date + timedelta(days=14)
    original = _listing("linkedin", "first", published_at=original_date.isoformat())
    repost = _listing("linkedin", "repost", published_at=repost_date.isoformat())

    track_and_deduplicate(db, "linkedin", [original])
    results = track_and_deduplicate(db, "linkedin", [repost])

    assert results[0]["duplicate_status"] == "reposted"
    assert results[0]["original_published_at"] == original_date
    assert results[0]["latest_repost_at"] == repost_date
    assert results[0]["repost_count"] == 1
    assert len(results[0]["source_listings"]) == 2


def test_repost_windows_use_nearest_prior_posting_date():
    db = mongomock.MongoClient(tz_aware=True).db
    dates = [
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 6, 15, tzinfo=timezone.utc),
    ]
    for index, posted in enumerate(dates):
        result = track_and_deduplicate(
            db,
            "linkedin",
            [_listing("linkedin", f"post-{index}", published_at=posted.isoformat())],
        )[0]
    assert result["duplicate_status"] == "reposted"
    assert result["repost_count"] == 2
    assert result["latest_repost_at"] == dates[-1]


def test_repeat_source_identity_is_idempotent():
    db = mongomock.MongoClient(tz_aware=True).db
    listing = _listing("indeed", "same-id")
    track_and_deduplicate(db, "indeed", [listing])

    results = track_and_deduplicate(db, "indeed", [listing])

    assert results[0]["duplicate_status"] == "known"
    assert len(results[0]["source_listings"]) == 1
    assert db.job_duplicate_groups.count_documents({}) == 1


def test_same_board_repost_with_reused_identifier_is_detected():
    db = mongomock.MongoClient(tz_aware=True).db
    initial = _listing(
        "linkedin",
        "reused-id",
        published_at="2026-10-01T00:00:00+00:00",
    )
    repost = _listing(
        "linkedin",
        "reused-id",
        published_at="2026-10-15T00:00:00+00:00",
    )
    track_and_deduplicate(db, "linkedin", [initial])

    results = track_and_deduplicate(db, "linkedin", [repost])

    assert results[0]["duplicate_status"] == "reposted"
    assert results[0]["repost_count"] == 1
    assert results[0]["latest_repost_at"] == datetime(2026, 10, 15, tzinfo=timezone.utc)
    assert results[0]["source_listings"][0]["published_at"] == initial["published_at"]
    assert results[0]["source_listings"][0]["repost_dates"] == [
        datetime(2026, 10, 15, tzinfo=timezone.utc),
    ]


def test_different_titles_and_locations_are_not_merged():
    original = _listing("linkedin", "base")
    different_title = _listing("indeed", "senior", title="Senior React Developer")
    different_location = _listing("glassdoor", "hyd", location="Hyderabad, India")

    assert compare_listings(different_title, original)[0] == "low"
    assert compare_listings(different_location, original)[0] == "low"

    db = mongomock.MongoClient(tz_aware=True).db
    track_and_deduplicate(db, "linkedin", [original])
    assert track_and_deduplicate(db, "indeed", [different_title])[0]["duplicate_status"] == "new"
    assert track_and_deduplicate(db, "glassdoor", [different_location])[0]["duplicate_status"] == "new"
    assert db.job_duplicate_groups.count_documents({}) == 3


def test_similar_new_vacancy_is_flagged_but_never_hidden():
    db = mongomock.MongoClient(tz_aware=True).db
    first_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    new_date = first_date + timedelta(days=200)
    original = _listing("linkedin", "original", published_at=first_date.isoformat())
    new_vacancy = _listing("linkedin", "new-vacancy", published_at=new_date.isoformat())

    track_and_deduplicate(db, "linkedin", [original])
    results = track_and_deduplicate(db, "linkedin", [new_vacancy])

    assert len(results) == 1
    assert results[0]["duplicate_status"] == "potential_duplicate"
    assert results[0]["duplicate_confidence"] == "medium"
    assert db.job_duplicate_groups.count_documents({}) == 2


def test_missing_company_description_or_dates_are_safe_and_uncertain():
    db = mongomock.MongoClient(tz_aware=True).db
    no_company = _listing("linkedin", "no-company", company="", description="")
    original = _listing("indeed", "dated", published_at=None)
    similar_without_date = _listing(
        "glassdoor",
        "undated",
        published_at=None,
        company="ABC Technologies Inc",
    )

    assert track_and_deduplicate(db, "linkedin", [no_company])[0]["duplicate_status"] == "new"
    track_and_deduplicate(db, "indeed", [original])
    result = track_and_deduplicate(db, "glassdoor", [similar_without_date])[0]

    assert result["duplicate_status"] == "potential_duplicate"
    assert result["duplicate_confidence"] == "medium"


def test_old_or_inaccurate_dates_do_not_force_a_repost_match():
    original = _listing(
        "linkedin",
        "old",
        published_at="2020-01-01T00:00:00+00:00",
    )
    new = _listing(
        "linkedin",
        "fresh",
        published_at="2026-10-25T00:00:00+00:00",
    )

    assert compare_listings(new, original)[0] == "medium"


def test_user_scoped_duplicate_groups_do_not_leak_between_accounts():
    raw = mongomock.MongoClient(tz_aware=True).db
    alice = UserScopedDatabase(raw, "alice")
    bob = UserScopedDatabase(raw, "bob")
    listing = _listing("linkedin", "private-listing")

    track_and_deduplicate(alice, "linkedin", [listing])

    assert alice.job_duplicate_groups.count_documents({}) == 1
    assert bob.job_duplicate_groups.count_documents({}) == 0


def test_board_discovery_endpoint_groups_listings_across_requests(monkeypatch):
    mongo = mongomock.MongoClient(tz_aware=True)
    db = mongo["discovery_endpoint_test"]
    db.candidates.insert_one({
        "_id": ObjectId(_CANDIDATE_ID),
        "full_name": "Test Candidate",
        "target_roles": ["Software Engineer"],
        "skills": ["React"],
        "location": "Bengaluru, India",
        "archived": False,
    })
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = lambda: db
    active_source = {"value": "linkedin"}

    def fake_search(**_kwargs):
        source = active_source["value"]
        return {
            "source": source,
            "results": [_listing(source, f"{source}-endpoint")],
        }

    monkeypatch.setattr(main, "search_jobspy_jobs", fake_search)
    client = TestClient(app)
    try:
        responses = []
        for source in ("linkedin", "indeed", "glassdoor"):
            active_source["value"] = source
            response = client.get(
                "/api/jobs/discover/board",
                params={
                    "site": source,
                    "candidate_id": _CANDIDATE_ID,
                    "what": "React Developer",
                },
            )
            assert response.status_code == 200
            responses.append(response.json()["results"][0])

        saved = client.post(
            "/api/jobs",
            json={
                "title": responses[0]["title"],
                "company": responses[0]["company"],
                "location": responses[0]["location"],
                "source": "linkedin",
                "source_job_id": responses[0]["source_job_id"],
                "url": responses[0]["url"],
                "description": responses[0]["description"],
                "duplicate_group_id": responses[0]["duplicate_group_id"],
                "duplicate_status": responses[0]["duplicate_status"],
                "duplicate_confidence": responses[0]["duplicate_confidence"],
                "available_sources": responses[0]["available_sources"],
                "source_listings": responses[0]["source_listings"],
                "original_published_at": responses[0]["original_published_at"],
                "latest_repost_at": responses[0]["latest_repost_at"],
                "repost_count": responses[0]["repost_count"],
            },
        )
        assert saved.status_code == 201
        assert saved.json()["source"] == "linkedin"

        created_application = client.post(
            "/api/applications",
            json={
                "job_id": saved.json()["id"],
                "candidate_id": _CANDIDATE_ID,
                "status": "saved",
            },
        )
        assert created_application.status_code == 201
        application_id = created_application.json()["id"]
        marked_applied = client.patch(
            f"/api/applications/{application_id}/apply",
            json={"status": "applied"},
        )
        assert marked_applied.status_code == 200
        assert marked_applied.json()["apply_status"] == "applied"
        applied_at = marked_applied.json()["applied_at"]

        active_source["value"] = "indeed"
        applied_response = client.get(
            "/api/jobs/discover/board",
            params={
                "site": "indeed",
                "candidate_id": _CANDIDATE_ID,
                "what": "React Developer",
            },
        )
        assert applied_response.status_code == 200
        applied_result = applied_response.json()["results"][0]
        assert applied_result["already_applied"] is True
        assert applied_result["applied_application_id"] == application_id
        assert applied_result["applied_at"] == applied_at

        other_candidate_id = ObjectId()
        db.candidates.insert_one({
            "_id": other_candidate_id,
            "full_name": "Other Candidate",
            "target_roles": ["Software Engineer"],
            "skills": ["React"],
            "location": "Bengaluru, India",
            "archived": False,
        })
        other_candidate_response = client.get(
            "/api/jobs/discover/board",
            params={
                "site": "indeed",
                "candidate_id": str(other_candidate_id),
                "what": "React Developer",
            },
        )
        assert other_candidate_response.status_code == 200
        assert other_candidate_response.json()["results"][0]["already_applied"] is False
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous

    assert responses[0]["duplicate_status"] == "new"
    assert responses[1]["duplicate_status"] == "cross_board_duplicate"
    assert responses[2]["available_sources"] == ["glassdoor", "indeed", "linkedin"]
    assert len(responses[2]["source_listings"]) == 3
    assert db.job_duplicate_groups.count_documents({}) == 1
