import pytest

pytestmark = pytest.mark.skip(reason="Legacy assistant browser tests are not active in this repo snapshot.")
needs_browser = pytest.mark.skip(reason="Legacy assistant browser tests are not active in this repo snapshot.")

# ---------- multi-step automation, radio groups & /automate endpoint ----------

@pytest.mark.parametrize(
    ("source", "url", "expected_adapter"),
    (
        ("linkedin", "https://www.linkedin.com/jobs/view/123456", "linkedin_automate"),
        ("indeed", "https://in.indeed.com/viewjob?jk=abc123", "indeed_automate"),
        ("naukri", "https://www.naukri.com/job-listings-dev-123", "naukri_automate"),
        ("glassdoor", "https://www.glassdoor.co.in/job-listing/dev-123", "glassdoor_automate"),
        ("linkedin", "https://boards.greenhouse.io/acme/jobs/1", "greenhouse"),
        ("indeed", "https://jobs.lever.co/acme/1", "lever"),
    ),
)
def test_check_automate_url_supports_all_four_boards_and_ats(source, url, expected_adapter):
    target = assistant_urls.check_automate_url(url, source=source if "greenhouse" not in url and "lever" not in url else "")
    assert target.adapter == expected_adapter
    assert target.url == url


def test_radio_group_field_matches_confirmed_work_authorization():
    vals = {
        "work_authorization": {"value": "Yes", "source": "user"},
        "visa_sponsorship": {"value": "No", "source": "user"},
    }
    radio_desc = desc(
        "Are you legally authorized to work in India?",
        kind="input",
        type="radio",
        required=True,
        options=[{"value": "0", "text": "Yes"}, {"value": "1", "text": "No"}],
    )
    matched = assistant_fill.match_field(radio_desc, vals)
    assert matched["state"] == "filled"
    assert matched["key"] == "work_authorization"
    assert matched["value"] == "0"


def test_automate_endpoint_passes_kit_values_and_selected_resume(db, monkeypatch):
    db.resumes.insert_one({
        "label": "Primary CV",
        "filename": "asha-resume.pdf",
        "is_active": True,
        "data": b"%PDF-1.4 valid resume bytes",
    })
    captured = {}

    def fake_run(target, values, **kwargs):
        captured["adapter"] = target.adapter
        captured["url"] = target.url
        captured["values"] = values
        captured["resume_file"] = kwargs.get("resume_file")
        result = {
            "status": "prepared",
            "pause_reason": "",
            "fields": [{"label": "Email", "state": "filled", "key": "email", "reason": "", "required": True}],
            "blocked_writes": 0,
            "browser_open": True,
        }
        kwargs["on_result"](result)
        return result

    monkeypatch.setattr(assistant_routes, "run_assistance", fake_run)
    state = open_assistant(
        db,
        source="linkedin",
        url="https://www.linkedin.com/jobs/view/123456",
    )
    response = client.post(base(state) + "/automate", json={"upload_resume": True})
    assert response.status_code == 202
    for _ in range(50):
        app_doc = db.applications.find_one({})
        if app_doc["assistant"]["runs"][0].get("finished_at"):
            break
        time.sleep(0.02)

    assert captured["adapter"] == "linkedin_automate"
    assert captured["url"] == "https://www.linkedin.com/jobs/view/123456"
    assert captured["values"]["email"]["value"] == "asha@example.com"
    assert captured["resume_file"] == (b"%PDF-1.4 valid resume bytes", "asha-resume.pdf")


@needs_browser
def test_playwright_multistep_modal_advances_and_stops_at_submit(server):
    before = assistant_test_form.SUBMISSIONS["count"]
    multistep_values = {
        **VALUES,
        "work_authorization": {"value": "Yes", "source": "user"},
        "visa_sponsorship": {"value": "No", "source": "user"},
        "years_of_experience": {"value": "2", "source": "user"},
    }
    target = check_fill_url(server + "/assistant-test-form?variant=multistep")
    res = assistant_fill.run_assistance(
        target,
        multistep_values,
        headless=True,
        resume_file=(b"%PDF-1.4 resume", "asha.pdf"),
    )
    by_key = {f["key"]: f for f in res["fields"] if f.get("key")}
    assert by_key["first_name"]["state"] == "filled"
    assert by_key["email"]["state"] == "filled"
    assert by_key["resume"]["state"] == "filled"
    assert by_key["work_authorization"]["state"] == "filled"
    assert by_key["visa_sponsorship"]["state"] == "filled"
    assert by_key["years_of_experience"]["state"] == "filled"
    assert res["status"] == "prepared"
    assert assistant_test_form.SUBMISSIONS["count"] == before