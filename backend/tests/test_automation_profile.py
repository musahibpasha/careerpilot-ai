import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import assistant_fill
from app.assistant_urls import check_fill_url, check_job_listing_url


def _cdp_session(profile, product="Chrome/156.0.0.0"):
    class Session:
        def send(self, command):
            if command == "Browser.getVersion":
                return {"product": product}
            if command == "Browser.getBrowserCommandLine":
                return {"arguments": [f"--user-data-dir={profile}"]}
            raise AssertionError(f"Unexpected CDP command: {command}")

        def detach(self):
            pass

    return Session()


@pytest.fixture(autouse=True)
def installed_chrome_stub(monkeypatch, tmp_path):
    monkeypatch.setattr(
        assistant_fill,
        "_installed_chrome_executable",
        lambda: tmp_path / "chrome.exe",
    )


def test_default_profile_is_dedicated_outside_the_repository(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    profile = Path(assistant_fill._automation_profile_path())

    assert profile == (tmp_path / "CareerPilot AI" / "AutomationChromeProfile").resolve()
    assert profile.is_dir()
    assert not profile.is_relative_to(Path(assistant_fill.__file__).resolve().parents[2])


def test_profile_resolver_rejects_normal_chrome_and_edge_profiles(tmp_path, monkeypatch):
    local_app_data = tmp_path / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    for browser_profile in (
        local_app_data / "Google" / "Chrome" / "User Data",
        local_app_data / "Microsoft" / "Edge" / "User Data",
    ):
        with pytest.raises(ValueError, match="regular browser profile"):
            assistant_fill._automation_profile_path(str(browser_profile))


def test_profile_lock_is_exclusive_in_process_and_released(tmp_path):
    first = assistant_fill.AutomationProfileLock(str(tmp_path))
    second = assistant_fill.AutomationProfileLock(str(tmp_path))

    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()


def test_profile_lock_excludes_other_processes(tmp_path):
    parent_lock = assistant_fill.AutomationProfileLock(str(tmp_path))
    assert parent_lock.acquire()
    script = (
        "import sys; from app.assistant_fill import AutomationProfileLock; "
        "lock = AutomationProfileLock(sys.argv[1]); print(lock.acquire())"
    )
    backend_dir = Path(__file__).resolve().parents[1]
    try:
        blocked = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path)],
            cwd=backend_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        assert blocked.stdout.strip() == "False"
    finally:
        parent_lock.release()

    acquired = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=backend_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    assert acquired.stdout.strip() == "True"


def test_missing_google_chrome_returns_clear_form_browser_error(tmp_path, monkeypatch):
    class Chromium:
        def launch_persistent_context(self, **_kwargs):
            raise AssertionError("Chrome must be checked before launch.")

    class Playwright:
        chromium = Chromium()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def missing_chrome():
        raise FileNotFoundError("Google Chrome Stable was not found.")

    monkeypatch.setitem(
        sys.modules,
        "playwright.sync_api",
        SimpleNamespace(sync_playwright=lambda: Playwright()),
    )
    monkeypatch.setattr(assistant_fill, "_installed_chrome_executable", missing_chrome)
    monkeypatch.setattr(assistant_fill, "check_public_host", lambda _host: None)
    monkeypatch.setattr(assistant_fill, "_automation_profile_path", lambda _path: str(tmp_path))

    result = assistant_fill.run_assistance(
        check_fill_url("https://boards.greenhouse.io/acme/jobs/1"),
        {},
    )

    assert result["status"] == assistant_fill.RUN_FAILED
    assert result["pause_reason"] == "chrome_unavailable"
    assert result["error_category"] == "chrome_unavailable"


def test_installed_chrome_launch_failure_does_not_fall_back(tmp_path, monkeypatch):
    launches = []

    class Chromium:
        def launch_persistent_context(self, **kwargs):
            launches.append(kwargs)
            raise RuntimeError("Chrome could not launch.")

    class Playwright:
        chromium = Chromium()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setitem(
        sys.modules,
        "playwright.sync_api",
        SimpleNamespace(sync_playwright=lambda: Playwright()),
    )
    monkeypatch.setattr(
        assistant_fill,
        "_installed_chrome_executable",
        lambda: tmp_path / "chrome.exe",
    )
    monkeypatch.setattr(assistant_fill, "check_public_host", lambda _host: None)
    monkeypatch.setattr(assistant_fill, "_automation_profile_path", lambda _path: str(tmp_path))

    result = assistant_fill.run_assistance(
        check_fill_url("https://boards.greenhouse.io/acme/jobs/1"),
        {},
    )

    assert result["pause_reason"] == "chrome_launch_failed"
    assert result["error_category"] == "chrome_launch_failed"
    assert len(launches) == 1
    assert launches[0]["channel"] == "chrome"
    assert launches[0]["headless"] is False


@pytest.mark.parametrize(
    ("adapter", "url", "host"),
    (
        ("greenhouse", "https://boards.greenhouse.io/acme/jobs/1", "boards.greenhouse.io"),
        ("lever", "https://jobs.lever.co/acme/1", "jobs.lever.co"),
    ),
)
def test_supported_assistant_runs_launch_with_the_shared_persistent_profile(
    adapter, url, host, tmp_path, monkeypatch
):
    calls = []

    class Tracing:
        def start(self, **_kwargs):
            raise AssertionError("Tracing is not enabled without an artifact directory.")

        def stop(self, **_kwargs):
            pass

    class Page:
        frames = []

        def __init__(self):
            self.url = url

        def goto(self, *_args, **_kwargs):
            pass

        def wait_for_load_state(self, *_args, **_kwargs):
            pass

        def evaluate(self, script, *_args):
            if script == assistant_fill.FLAGS_JS:
                return {"password": False, "login": False, "captcha": False}
            return []

        def is_closed(self):
            return False

    class Context:
        pages = [Page()]
        tracing = Tracing()

        def route(self, *_args):
            pass

        def close(self):
            pass

    class Chromium:
        def launch_persistent_context(self, **kwargs):
            calls.append(kwargs)
            return Context()

        def launch(self, **_kwargs):
            raise AssertionError("Persistent targets must not launch an ephemeral browser.")

    class Playwright:
        chromium = Chromium()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setitem(
        sys.modules,
        "playwright.sync_api",
        SimpleNamespace(sync_playwright=lambda: Playwright()),
    )
    monkeypatch.setattr(assistant_fill, "check_public_host", lambda _host: None)
    monkeypatch.setattr(assistant_fill, "_automation_profile_path", lambda _path: str(tmp_path))

    result = assistant_fill.run_assistance(
        assistant_fill.FillTarget(url=url, host=host, adapter=adapter),
        {},
        headless=True,
    )

    assert result["status"] == assistant_fill.NEEDS_YOU
    assert calls == [{
        "user_data_dir": str(tmp_path),
        "headless": False,
        "channel": "chrome",
    }]


def test_persistent_profile_lock_is_released_when_run_is_cancelled(tmp_path, monkeypatch):
    class SimulatedCancellation(BaseException):
        pass

    class Page:
        frames = []
        url = "https://boards.greenhouse.io/acme/jobs/1"

        def goto(self, *_args, **_kwargs):
            raise SimulatedCancellation()

        def is_closed(self):
            return False

    class Context:
        pages = [Page()]

        def route(self, *_args):
            pass

        def close(self):
            self.closed = True

    context = Context()

    class Chromium:
        def launch_persistent_context(self, **_kwargs):
            return context

    class Playwright:
        chromium = Chromium()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setitem(
        sys.modules,
        "playwright.sync_api",
        SimpleNamespace(sync_playwright=lambda: Playwright()),
    )
    monkeypatch.setattr(assistant_fill, "check_public_host", lambda _host: None)
    monkeypatch.setattr(assistant_fill, "_automation_profile_path", lambda _path: str(tmp_path))

    with pytest.raises(SimulatedCancellation):
        assistant_fill.run_assistance(
            assistant_fill.FillTarget(
                url="https://boards.greenhouse.io/acme/jobs/1",
                host="boards.greenhouse.io",
                adapter="greenhouse",
            ),
            {},
            headless=True,
        )

    assert context.closed
    lock = assistant_fill.AutomationProfileLock(str(tmp_path))
    assert lock.acquire()
    lock.release()


def test_four_job_boards_attach_to_existing_profile_without_launching_chrome(tmp_path, monkeypatch):
    connections = []
    navigations = []
    contexts = []

    class Tracing:
        def start(self, **_kwargs):
            raise AssertionError("Listing navigation should not capture artifacts without an artifact directory.")

        def stop(self, **_kwargs):
            pass

    class Page:
        frames = []

        def __init__(self, url):
            self.url = url

        def goto(self, url, **_kwargs):
            self.url = url
            navigations.append(url)

        def route(self, *_args):
            pass

        def wait_for_load_state(self, *_args, **_kwargs):
            pass

        def evaluate(self, script, *_args):
            if script == assistant_fill.FLAGS_JS:
                return {"password": False, "login": False, "captcha": False}
            raise AssertionError("Listing-only runs must not inspect or fill application fields.")

        def is_closed(self):
            return False

    class Context:
        def __init__(self, page):
            self.pages = [page]
            self.tracing = Tracing()
            self.closed = False

        def route(self, *_args):
            pass

        def close(self):
            self.closed = True

        def new_page(self):
            page = Page("about:blank")
            self.pages.append(page)
            return page

        def new_cdp_session(self, _page):
            return _cdp_session(tmp_path)

    class Browser:
        def __init__(self):
            self.context = Context(Page("https://www.linkedin.com/"))
            self.contexts = [self.context]
            self.disconnected = False

        def close(self):
            self.disconnected = True

    class Chromium:
        def __init__(self):
            self.browser = Browser()

        def connect_over_cdp(self, endpoint, **kwargs):
            connections.append((endpoint, kwargs))
            contexts.append(self.browser.contexts[0])
            return self.browser

    class Playwright:
        chromium = Chromium()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setitem(
        sys.modules,
        "playwright.sync_api",
        SimpleNamespace(sync_playwright=lambda: Playwright()),
    )
    monkeypatch.setattr(assistant_fill, "check_public_host", lambda _host: None)
    monkeypatch.setattr(assistant_fill, "_automation_profile_path", lambda _path: str(tmp_path))

    playwright = Playwright()
    listings = (
        ("linkedin", "https://www.linkedin.com/jobs/view/123"),
        ("naukri", "https://www.naukri.com/job-listings-backend-dev"),
        ("indeed", "https://in.indeed.com/viewjob?jk=abc123"),
        ("glassdoor", "https://www.glassdoor.co.in/job-listing/backend-dev"),
    )
    for source, url in listings:
        result = assistant_fill.run_assistance(
            check_job_listing_url(url, source),
            {},
            headless=True,
            browser_cdp_endpoint="http://127.0.0.1:9222",
        )
        assert result["status"] == assistant_fill.NEEDS_YOU
        assert result["pause_reason"] == "listing_opened"
        assert result["fields"] == []

    assert navigations == [url for _, url in listings]
    assert connections == [
        ("http://127.0.0.1:9222", {"timeout": 5000})
    ] * 4
    assert len(contexts) == 4
    assert all(not context.closed for context in contexts)
    assert playwright.chromium.browser.disconnected


@pytest.mark.parametrize(
    "endpoint",
    (
        None,
        "",
        "http://example.com:9222",
        "http://192.168.1.10:9222",
        "http://[::1]:9222",
        "https://127.0.0.1:9222",
        "http://127.0.0.1:9222/json",
        "http://user:password@127.0.0.1:9222",
    ),
)
def test_cdp_endpoint_must_be_explicit_localhost(endpoint):
    with pytest.raises(assistant_fill.BrowserAttachmentUnavailable):
        assistant_fill._validate_cdp_endpoint(endpoint)


def test_valid_cdp_endpoint_is_normalized():
    assert (
        assistant_fill._validate_cdp_endpoint("http://localhost:9222/")
        == "http://localhost:9222"
    )


def test_listing_without_cdp_endpoint_fails_without_launching_browser(tmp_path, monkeypatch):
    class Chromium:
        def launch_persistent_context(self, **_kwargs):
            raise AssertionError("Automate must not start a second Chrome.")

        def connect_over_cdp(self, *_args, **_kwargs):
            raise AssertionError("No endpoint was configured.")

    class Playwright:
        chromium = Chromium()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setitem(
        sys.modules,
        "playwright.sync_api",
        SimpleNamespace(sync_playwright=lambda: Playwright()),
    )
    monkeypatch.setattr(assistant_fill, "check_public_host", lambda _host: None)
    monkeypatch.setattr(assistant_fill, "_automation_profile_path", lambda _path: str(tmp_path))
    result = assistant_fill.run_assistance(
        check_job_listing_url("https://www.linkedin.com/jobs/view/123", "linkedin"),
        {},
    )

    assert result["status"] == assistant_fill.RUN_FAILED
    assert result["pause_reason"] == "browser_attachment_unavailable"
    assert result["error_category"] == "browser_attachment_unavailable"


def test_listing_rejects_a_different_chrome_profile_before_navigating(tmp_path, monkeypatch):
    navigations = []

    class Page:
        frames = []
        url = "about:blank"

        def goto(self, url, **_kwargs):
            navigations.append(url)

        def route(self, *_args):
            pass

    class Context:
        pages = [Page()]

        def new_page(self):
            raise AssertionError("Profile verification must happen before opening a new tab.")

        def new_cdp_session(self, _page):
            return _cdp_session(str(tmp_path / "another-profile"))

    class Browser:
        contexts = [Context()]

        def close(self):
            self.disconnected = True

    browser = Browser()

    class Chromium:
        def connect_over_cdp(self, *_args, **_kwargs):
            return browser

        def launch_persistent_context(self, **_kwargs):
            raise AssertionError("Automate must not start another browser.")

    class Playwright:
        chromium = Chromium()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setitem(
        sys.modules,
        "playwright.sync_api",
        SimpleNamespace(sync_playwright=lambda: Playwright()),
    )
    monkeypatch.setattr(assistant_fill, "check_public_host", lambda _host: None)
    monkeypatch.setattr(assistant_fill, "_automation_profile_path", lambda _path: str(tmp_path))

    result = assistant_fill.run_assistance(
        check_job_listing_url("https://www.linkedin.com/jobs/view/123", "linkedin"),
        {},
        browser_cdp_endpoint="http://127.0.0.1:9222",
    )

    assert result["status"] == assistant_fill.RUN_FAILED
    assert result["pause_reason"] == "browser_profile_mismatch"
    assert result["error_category"] == "browser_profile_mismatch"
    assert navigations == []
    assert browser.disconnected


@pytest.mark.parametrize(
    ("flags", "fail_navigation", "expected_status", "expected_pause"),
    (
        (
            {"password": False, "login": False, "captcha": True},
            False,
            assistant_fill.NEEDS_YOU,
            "access_challenge",
        ),
        (
            {"password": False, "login": False, "captcha": False},
            True,
            assistant_fill.RUN_FAILED,
            "navigation_failed",
        ),
    ),
)
def test_listing_challenges_and_navigation_failures_close_browser_safely(
    tmp_path, monkeypatch, flags, fail_navigation, expected_status, expected_pause
):
    class Page:
        frames = []
        url = "https://www.linkedin.com/jobs/view/123"

        def goto(self, *_args, **_kwargs):
            if fail_navigation:
                raise RuntimeError("simulated navigation failure")

        def route(self, *_args):
            pass

        def wait_for_load_state(self, *_args, **_kwargs):
            pass

        def evaluate(self, script, *_args):
            if script == assistant_fill.FLAGS_JS:
                return flags
            raise AssertionError("Listing-only runs must not inspect or fill application fields.")

        def is_closed(self):
            return False

    class Context:
        pages = [Page()]

        def new_page(self):
            return self.pages[0]

        def new_cdp_session(self, _page):
            return _cdp_session(tmp_path)

        def route(self, *_args):
            pass

    context = Context()

    class Browser:
        contexts = [context]

        def close(self):
            self.disconnected = True

    browser = Browser()

    class Chromium:
        def connect_over_cdp(self, *_args, **_kwargs):
            return browser

    class Playwright:
        chromium = Chromium()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setitem(
        sys.modules,
        "playwright.sync_api",
        SimpleNamespace(sync_playwright=lambda: Playwright()),
    )
    monkeypatch.setattr(assistant_fill, "check_public_host", lambda _host: None)
    monkeypatch.setattr(assistant_fill, "_automation_profile_path", lambda _path: str(tmp_path))

    result = assistant_fill.run_assistance(
        check_job_listing_url("https://www.linkedin.com/jobs/view/123", "linkedin"),
        {},
        headless=True,
        browser_cdp_endpoint="http://127.0.0.1:9222",
    )

    assert result["status"] == expected_status
    assert result["pause_reason"] == expected_pause
    assert browser.disconnected
    lock = assistant_fill.AutomationProfileLock(str(tmp_path))
    assert lock.acquire()
    lock.release()


def test_cloudflare_challenge_captures_private_diagnostics_without_retrying(
    tmp_path, monkeypatch
):
    urls = []

    class Tracing:
        def start(self, **_kwargs):
            self.started = True

        def stop(self, *, path):
            Path(path).write_bytes(b"trace")

    class Page:
        frames = []
        url = "https://www.glassdoor.co.in/job-listing/backend-dev"

        def goto(self, url, **_kwargs):
            urls.append(url)

        def route(self, *_args):
            pass

        def wait_for_load_state(self, *_args, **_kwargs):
            pass

        def evaluate(self, script, *_args):
            if script == assistant_fill.FLAGS_JS:
                return {"password": False, "login": False, "captcha": True}
            raise AssertionError("Challenge runs must not inspect application fields.")

        def screenshot(self, *, path, **_kwargs):
            Path(path).write_bytes(b"challenge screenshot")

        def is_closed(self):
            return False

    class Context:
        def __init__(self):
            self.pages = [Page()]
            self.tracing = Tracing()
            self.closed = False

        def route(self, *_args):
            pass

        def new_page(self):
            return self.pages[0]

        def new_cdp_session(self, _page):
            return _cdp_session(tmp_path)

    context = Context()

    class Browser:
        contexts = [context]

        def close(self):
            self.disconnected = True

    browser = Browser()

    class Chromium:
        def connect_over_cdp(self, *_args, **_kwargs):
            return browser

    class Playwright:
        chromium = Chromium()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setitem(
        sys.modules,
        "playwright.sync_api",
        SimpleNamespace(sync_playwright=lambda: Playwright()),
    )
    monkeypatch.setattr(assistant_fill, "check_public_host", lambda _host: None)
    monkeypatch.setattr(assistant_fill, "_automation_profile_path", lambda _path: str(tmp_path))
    artifact_dir = tmp_path / "private-artifacts"
    artifact_dir.mkdir()
    result = assistant_fill.run_assistance(
        check_job_listing_url(
            "https://www.glassdoor.co.in/job-listing/backend-dev",
            "glassdoor",
        ),
        {},
        browser_cdp_endpoint="http://127.0.0.1:9222",
        artifact_dir=str(artifact_dir),
    )

    assert result["pause_reason"] == "access_challenge"
    assert urls == ["https://www.glassdoor.co.in/job-listing/backend-dev"]
    assert Path(result["trace_path"]).read_bytes() == b"trace"
    assert Path(result["screenshot_path"]).read_bytes() == b"challenge screenshot"
    assert browser.disconnected
