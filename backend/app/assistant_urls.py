"""URL policy for browser-assisted form filling.

Pure functions, no network. The backend route calls check_fill_url() before
any browser is launched, so the rules hold no matter what the frontend does.

Rules:
  * Only http/https, no credentials in the URL.
  * LinkedIn (and every subdomain, plus its lnkd.in short links) is always
    blocked: LinkedIn jobs get a manual-fill checklist instead.
  * Automated form filling is limited to Greenhouse and Lever pages.
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
    adapter: str  # "greenhouse" | "lever" | "local_test"


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
            "LinkedIn pages are never opened or filled automatically. "
            "Use the Application Kit and the manual-fill checklist instead.",
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
        return FillTarget(url=raw, host=host, adapter=adapter)

    raise UrlRejected(
        "unsupported_host",
        "Automatic form filling currently supports Greenhouse and Lever application pages. "
        "Open the saved listing and paste its employer's Greenhouse or Lever application URL.",
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
