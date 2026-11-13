"""URL policy for browser-assisted job listings and form filling.

Pure functions, no network. The backend route calls check_fill_url(),
check_job_listing_url(), or check_automate_url() before any browser is
launched, so the rules hold no matter what the frontend does.

Rules:
  * Job listings may open on LinkedIn, Naukri, Indeed, or Glassdoor.
  * Direct employer form filling supports Greenhouse, Lever, and Glassdoor pages.
  * Full automation (/automate) supports LinkedIn, Naukri, Indeed, Glassdoor,
    Greenhouse, Lever, and common external ATS redirects (Workday, Ashby,
    SmartRecruiters, BambooHR, iCIMS).
  * The local test form is available only through the opt-in development flag.
"""

from dataclasses import dataclass
import ipaddress
from urllib.parse import urlsplit

from app.job_fetch import JobFetchError, is_blocked_ip, resolve_host

BLOCKED_DOMAINS = ("linkedin.com", "lnkd.in", "licdn.com")

# (domain, adapter name). A host matches the domain itself or any subdomain.
SUPPORTED_DOMAINS = (
    ("greenhouse.io", "greenhouse"),
    ("lever.co", "lever"),
    ("glassdoor.com", "glassdoor"),
    ("glassdoor.co.in", "glassdoor"),
)

# Extended ATS + job board domains permitted during multi-step automation runs
AUTOMATION_DOMAINS = (
    ("linkedin.com", "linkedin"),
    ("naukri.com", "naukri"),
    ("indeed.com", "indeed"),
    ("indeed.co.in", "indeed"),
    ("indeed.co.uk", "indeed"),
    ("indeed.ca", "indeed"),
    ("indeed.com.au", "indeed"),
    ("smartapply.indeed.com", "indeed"),
    ("glassdoor.com", "glassdoor"),
    ("glassdoor.co.in", "glassdoor"),
    ("glassdoor.co.uk", "glassdoor"),
    ("glassdoor.ca", "glassdoor"),
    ("glassdoor.com.au", "glassdoor"),
    ("greenhouse.io", "greenhouse"),
    ("lever.co", "lever"),
    ("myworkdayjobs.com", "workday"),
    ("workday.com", "workday"),
    ("smartrecruiters.com", "smartrecruiters"),
    ("ashbyhq.com", "ashby"),
    ("bamboohr.com", "bamboohr"),
    ("icims.com", "icims"),
)

JOB_BOARD_DOMAINS = {
    "linkedin": ("linkedin.com",),
    "naukri": ("naukri.com",),
    "indeed": (
        "indeed.com",
        "indeed.co.in",
        "indeed.co.uk",
        "indeed.ca",
        "indeed.com.au",
        "indeed.fr",
        "indeed.de",
        "indeed.nl",
        "indeed.es",
        "indeed.com.br",
        "indeed.com.sg",
        "indeed.com.mx",
        "indeed.com.hk",
        "indeed.co.jp",
    ),
    "glassdoor": (
        "glassdoor.com",
        "glassdoor.co.in",
        "glassdoor.co.uk",
        "glassdoor.ca",
        "glassdoor.com.au",
        "glassdoor.de",
        "glassdoor.fr",
        "glassdoor.nl",
        "glassdoor.es",
    ),
}

JOB_SOURCE_PLATFORMS = {
    "linkedin": "linkedin",
    "naukri": "naukri",
    "indeed": "indeed",
    "glassdoor": "glassdoor",
}

LOCAL_FORM_PATH = "/assistant-test-form"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


class UrlRejected(Exception):
    """The URL may not be used. `code` is stable for tests and the UI."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class FillTarget:
    url: str
    host: str
    adapter: str


def _domain_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def is_blocked_host(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(_domain_matches(host, d) for d in BLOCKED_DOMAINS)


def is_supported_host(host: str) -> bool:
    return adapter_for_host(host) is not None


def adapter_for_host(host: str) -> str | None:
    host = (host or "").lower().rstrip(".")
    return next(
        (adapter for domain, adapter in SUPPORTED_DOMAINS if _domain_matches(host, domain)),
        None,
    )


def automation_adapter_for_host(host: str) -> str | None:
    host = (host or "").lower().rstrip(".")
    return next(
        (adapter for domain, adapter in AUTOMATION_DOMAINS if _domain_matches(host, domain)),
        None,
    )


def platform_for_host(host: str) -> str | None:
    host = (host or "").lower().rstrip(".")
    return next(
        (
            platform
            for platform, domains in JOB_BOARD_DOMAINS.items()
            if any(_domain_matches(host, domain) for domain in domains)
        ),
        None,
    )


def check_job_listing_url(raw: str, source: str = "") -> FillTarget:
    """Validate a saved job URL for listing-only persistent-browser navigation."""
    raw = (raw or "").strip()
    if not raw or len(raw) > 2000:
        raise UrlRejected("invalid_url", "This saved job has no valid listing URL.")
    if any(ch in raw for ch in "\r\n\t\x00") or " " in raw:
        raise UrlRejected("invalid_url", "The saved job URL contains characters that are not allowed.")

    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        raise UrlRejected("invalid_url", "The saved job URL is malformed.") from None

    host = (parts.hostname or "").lower().rstrip(".")
    if (
        parts.scheme.lower() != "https"
        or not host
        or parts.username
        or parts.password
        or port not in (None, 443)
        or parts.path in ("", "/")
    ):
        raise UrlRejected(
            "invalid_url",
            "The saved job must have a valid HTTPS listing URL.",
        )

    platform = platform_for_host(host)
    if platform is None:
        raise UrlRejected(
            "unsupported_host",
            "Only LinkedIn, Naukri, Indeed, and Glassdoor job listings can open in the ThoughtLabs CoPilotbrowser.",
        )
    expected_platform = JOB_SOURCE_PLATFORMS.get((source or "").strip().lower())
    if expected_platform and expected_platform != platform:
        raise UrlRejected(
            "platform_mismatch",
            "The saved job's source does not match its listing URL.",
        )
    if is_non_public_host(host):
        raise UrlRejected("non_public_host", "The saved listing must use a public internet address.")

    return FillTarget(url=raw, host=host, adapter=f"{platform}_listing")


def check_automate_url(raw: str, source: str = "", allow_local_form: bool = True) -> FillTarget:
    """Validate a job URL or application URL for interactive multi-step automation."""
    raw = (raw or "").strip()
    if not raw or len(raw) > 2000:
        raise UrlRejected("invalid_url", "This job has no valid application or listing URL.")
    if any(ch in raw for ch in "\r\n\t\x00") or " " in raw:
        raise UrlRejected("invalid_url", "That URL contains characters that are not allowed.")

    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        raise UrlRejected("invalid_url", "That does not look like a valid URL.") from None

    if parts.scheme.lower() not in ("http", "https"):
        raise UrlRejected("invalid_scheme", "Only http:// and https:// addresses are allowed.")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or parts.username or parts.password:
        raise UrlRejected("invalid_url", "That does not look like a valid URL.")

    if host in LOOPBACK_HOSTS:
        if allow_local_form and parts.path == LOCAL_FORM_PATH:
            return FillTarget(url=raw, host=f"{host}:{port}" if port else host, adapter="local_test")
        raise UrlRejected("unsupported_host", "Local addresses are only allowed for the built-in test form.")

    if parts.scheme.lower() != "https":
        raise UrlRejected("insecure_url", "Application pages must use https://.")
    if port not in (None, 443):
        raise UrlRejected("unsupported_port", "Application pages must use the standard HTTPS port.")
    if parts.path in ("", "/"):
        raise UrlRejected("invalid_url", "Enter a specific job listing or application URL, not a homepage.")
    if is_non_public_host(host):
        raise UrlRejected("non_public_host", "Application pages must use a public internet address.")

    board_platform = platform_for_host(host)
    expected_platform = JOB_SOURCE_PLATFORMS.get((source or "").strip().lower())
    if expected_platform and board_platform and expected_platform != board_platform:
        raise UrlRejected(
            "platform_mismatch",
            "The saved job's source does not match its listing URL.",
        )

    adapter = (
        f"{board_platform}_automate"
        if board_platform
        else automation_adapter_for_host(host)
    )
    if not adapter:
        raise UrlRejected(
            "unsupported_host",
            "Only LinkedIn, Indeed, Naukri, Glassdoor, Greenhouse, Lever, and supported ATS pages can be automated.",
        )
    return FillTarget(url=raw, host=host, adapter=adapter)


def is_non_public_host(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    if host in LOOPBACK_HOSTS or host.endswith((".localhost", ".local", ".internal")):
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False


def check_fill_url(raw: str, allow_local_form: bool = True) -> FillTarget:
    """Validate URL syntax and destination class without making a network request."""
    raw = (raw or "").strip()
    if not raw or len(raw) > 2000:
        raise UrlRejected("invalid_url", "Enter the employer application page URL.")
    if any(ch in raw for ch in "\r\n\t\x00") or " " in raw:
        raise UrlRejected("invalid_url", "That URL contains characters that are not allowed.")

    try:
        parts = urlsplit(raw)
        port = parts.port  # raises ValueError on a bad port
    except ValueError:
        raise UrlRejected("invalid_url", "That does not look like a valid URL.") from None

    if parts.scheme.lower() not in ("http", "https"):
        raise UrlRejected("invalid_scheme", "Only http:// and https:// addresses are allowed.")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise UrlRejected("invalid_url", "That does not look like a valid URL.")
    if parts.username or parts.password:
        raise UrlRejected("invalid_url", "URLs containing a username or password are not allowed.")

    if is_blocked_host(host):
        raise UrlRejected(
            "linkedin_blocked",
            "LinkedIn pages are never opened or filled through the legacy direct-URL endpoint. "
            "Use the Automate Job Application button to run LinkedIn Easy Apply in the persistent browser.",
        )

    if host in LOOPBACK_HOSTS:
        if allow_local_form and parts.path == LOCAL_FORM_PATH:
            return FillTarget(url=raw, host=f"{host}:{port}" if port else host, adapter="local_test")
        raise UrlRejected(
            "unsupported_host",
            "Local addresses are only allowed for the built-in test form.",
        )

    if parts.scheme.lower() != "https":
        raise UrlRejected("insecure_url", "Employer application pages must use https://.")
    if port not in (None, 443):
        raise UrlRejected("unsupported_port", "Employer application pages must use the standard HTTPS port.")
    if is_non_public_host(host):
        raise UrlRejected(
            "non_public_host",
            "Application forms must use a public internet address.",
        )

    adapter = adapter_for_host(host)
    if adapter:
        if adapter == "glassdoor" and not any(
            segment in parts.path.lower() for segment in ("/job-listing/", "/job/")
        ):
            raise UrlRejected(
                "unsupported_host",
                "For Glassdoor, enter a job listing URL.",
            )
        return FillTarget(url=raw, host=host, adapter=adapter)

    raise UrlRejected(
        "unsupported_host",
        "Automatic form filling currently supports Greenhouse, Lever, and Glassdoor pages. "
        "For LinkedIn, Indeed, or Naukri listings, use the Automate Job Application button.",
    )


def check_public_host(host: str) -> None:
    """Refuse a browser request unless every resolved IP address is public."""
    try:
        addresses = resolve_host(host, 443)
    except JobFetchError as exc:
        raise UrlRejected("unresolvable_host", exc.message) from None
    if not addresses or any(is_blocked_ip(address) for address in addresses):
        raise UrlRejected(
            "non_public_host",
            "This application page resolves to a private or internal address and cannot be opened.",
        )