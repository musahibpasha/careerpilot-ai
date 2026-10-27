"""Stage 4: safely read a public job-posting web page and turn it into plain text.

The text this returns is fed to the same rule-based parser as pasted text
(app.job_import.extract_job), so a link and a paste give the same kind of draft.
Nothing here saves anything.

Safety rules (the page is a stranger's server, so we treat it as hostile):
  * only http / https, only ports 80 / 443, no user:password@ in the link
  * we look up the address ourselves, refuse anything that is not a public
    internet address (localhost, private, link-local, cloud metadata, multicast...)
    and then connect to THAT checked address, so the name cannot be switched to an
    internal address after the check (DNS rebinding)
  * every redirect is followed by hand, with the same checks, at most 5 times
  * hard limits: total time, bytes read (counted while reading), content type
  * no cookies, no login, no CAPTCHA or anti-bot workarounds: if the site refuses us
    we say so and the user pastes the text instead
  * page text is only ever data. We never run or obey anything written in it.

Only the standard library is used (no new dependency).
"""

from __future__ import annotations

import html
import ipaddress
import json
import logging
import re
import socket
import ssl
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from http.client import HTTPConnection, HTTPException
from typing import Callable
from urllib.parse import parse_qsl, urljoin, urlsplit

from app.job_import import MAX_IMPORT_CHARS

logger = logging.getLogger(__name__)

TOTAL_TIMEOUT = 8.0  # seconds for the whole fetch, redirects included
CONNECT_TIMEOUT = 4.0
MAX_BYTES = 1_000_000  # 1 MB of page is far more than any job posting
MAX_REDIRECTS = 5
MAX_URL_CHARS = 2000
ALLOWED_PORTS = (80, 443)
ALLOWED_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
MIN_READABLE_CHARS = 40
USER_AGENT = "CareerPilotAI/1.0 (personal job-search assistant; reads one page you asked for)"

PASTE_HINT = " You can paste the job description into the box instead."


class JobFetchError(Exception):
    """A problem we can explain to the user in one plain sentence."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class FetchedPage:
    text: str
    final_url: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class RawResponse:
    """What an opener hands back: the status, lower-cased headers, and a reader."""

    status: int
    headers: dict[str, str]
    read: Callable[[int], bytes]
    close: Callable[[], None]


# --------------------------------------------------------------------------
# 1. Is this address allowed?
# --------------------------------------------------------------------------
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def is_blocked_ip(value) -> bool:
    """True for anything that is not an ordinary public internet address."""
    try:
        ip = ipaddress.ip_address(value.split("%")[0] if isinstance(value, str) else value)
    except ValueError:
        return True
    if ip.version == 6:
        # IPv6 forms that wrap an IPv4 address: judge the address inside.
        if ip.ipv4_mapped is not None:
            return is_blocked_ip(ip.ipv4_mapped)
        if ip.sixtofour is not None:
            return is_blocked_ip(ip.sixtofour)
        if ip.teredo is not None:
            return True
        if ip in _NAT64:
            return is_blocked_ip(ipaddress.ip_address(int(ip) & 0xFFFFFFFF))
    return (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


# --------------------------------------------------------------------------
# 2. Is this link allowed?
# --------------------------------------------------------------------------
def parse_public_url(raw: str, allowed_ports=ALLOWED_PORTS) -> tuple[str, str, int, str, str]:
    """Check a link and return (scheme, host, port, path_and_query, clean_url)."""
    link = (raw or "").strip()
    if not link:
        raise JobFetchError("Enter the job posting link." + PASTE_HINT)
    if len(link) > MAX_URL_CHARS:
        raise JobFetchError("That link is too long." + PASTE_HINT)
    if re.search(r"[\s\x00-\x1f\x7f]", link):
        raise JobFetchError("That link has spaces or hidden characters in it." + PASTE_HINT)
    if "://" not in link:
        raise JobFetchError("The link must start with http:// or https://." + PASTE_HINT)

    try:
        parts = urlsplit(link)
        port = parts.port
    except ValueError:
        raise JobFetchError("That does not look like a valid link." + PASTE_HINT) from None

    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise JobFetchError("Only http:// and https:// links can be opened." + PASTE_HINT)
    if parts.username is not None or parts.password is not None:
        raise JobFetchError("Links that contain a username or password are not allowed." + PASTE_HINT)

    host = (parts.hostname or "").rstrip(".").lower()
    if not host or "%" in host:
        raise JobFetchError("That link has no website name in it." + PASTE_HINT)
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise JobFetchError("That website name is not valid." + PASTE_HINT) from None

    port = port or (443 if scheme == "https" else 80)
    if port not in allowed_ports:
        raise JobFetchError("Only the normal web ports (80 and 443) are allowed." + PASTE_HINT)

    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    shown_host = f"[{host}]" if ":" in host else host
    default_port = 443 if scheme == "https" else 80
    netloc = shown_host if port == default_port else f"{shown_host}:{port}"
    clean = f"{scheme}://{netloc}{target}"  # fragment dropped on purpose
    return scheme, host, port, target, clean


def is_search_results_url(raw: str) -> bool:
    """Recognize common search/listing routes unless they identify one posting."""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return False
    path = parts.path.rstrip("/").lower()
    if not re.search(r"/(?:careers|jobs|search|openings)$", path):
        return False
    query = {key.lower(): value for key, value in parse_qsl(parts.query) if value}
    job_ids = ("pid", "jobid", "job_id", "gh_jid", "requisitionid", "reqid")
    return not any(query.get(key) for key in job_ids)


def resolve_host(host: str, port: int) -> list[str]:
    """Look the name up ourselves. Returns every address it points to."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        raise JobFetchError("I could not find that website. Check the link." + PASTE_HINT) from None
    addresses: list[str] = []
    for info in infos:
        address = info[4][0]
        if address not in addresses:
            addresses.append(address)
    return addresses


def _public_addresses(host, port, resolver, is_blocked) -> list[str]:
    addresses = resolver(host, port)
    if not addresses:
        raise JobFetchError("I could not find that website. Check the link." + PASTE_HINT)
    # One internal address among the answers is enough to refuse the whole name.
    if any(is_blocked(a) for a in addresses):
        raise JobFetchError(
            "That link points to a private or internal address, so I will not open it." + PASTE_HINT
        )
    return addresses


# --------------------------------------------------------------------------
# 3. Talk to the server (connects to the address we already checked)
# --------------------------------------------------------------------------
def _time_left(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise JobFetchError("That website took too long to answer." + PASTE_HINT, 504)
    return left


def _open_pinned(scheme, host, port, target, addresses, deadline) -> RawResponse:
    sock = None
    for address in addresses[:3]:
        try:
            sock = socket.create_connection(
                (address, port), timeout=min(_time_left(deadline), CONNECT_TIMEOUT)
            )
            break
        except JobFetchError:
            raise
        except OSError:
            sock = None
    if sock is None:
        raise JobFetchError("I could not connect to that website." + PASTE_HINT, 502)

    try:
        if scheme == "https":
            # Certificate and name are verified against the real host name,
            # while the connection itself goes to the checked address.
            sock.settimeout(_time_left(deadline))
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)

        default_port = 443 if scheme == "https" else 80
        host_header = host if port == default_port else f"{host}:{port}"
        connection = HTTPConnection(host, port)
        connection.sock = sock
        connection.putrequest("GET", target, skip_host=True, skip_accept_encoding=True)
        connection.putheader("Host", host_header)
        connection.putheader("User-Agent", USER_AGENT)
        connection.putheader("Accept", "text/html,application/xhtml+xml,text/plain;q=0.9")
        connection.putheader("Accept-Language", "en")
        connection.putheader("Accept-Encoding", "identity")  # no compressed (bomb) bodies
        connection.putheader("Connection", "close")
        sock.settimeout(_time_left(deadline))
        connection.endheaders()
        response = connection.getresponse()
    except JobFetchError:
        sock.close()
        raise
    except (socket.timeout, TimeoutError):
        sock.close()
        raise JobFetchError("That website took too long to answer." + PASTE_HINT, 504) from None
    except ssl.SSLError:
        sock.close()
        raise JobFetchError(
            "That website's security certificate could not be verified." + PASTE_HINT, 502
        ) from None
    except (OSError, HTTPException):
        sock.close()
        raise JobFetchError("I could not read an answer from that website." + PASTE_HINT, 502) from None

    def read(size: int) -> bytes:
        remaining = _time_left(deadline)
        try:
            try:
                sock.settimeout(remaining)
            except OSError:
                pass  # the socket is already closed because the whole page has been read
            return response.read1(size)  # one read at a time, so the deadline is re-checked
        except (socket.timeout, TimeoutError):
            raise JobFetchError("That website took too long to answer." + PASTE_HINT, 504) from None
        except (OSError, HTTPException, ValueError):
            raise JobFetchError("The connection to that website was interrupted." + PASTE_HINT, 502) from None

    def close() -> None:
        try:
            response.close()
        finally:
            connection.close()

    headers = {k.lower(): v for k, v in response.getheaders()}
    return RawResponse(response.status, headers, read, close)


# --------------------------------------------------------------------------
# 4. Fetch with redirects, limits and content checks
# --------------------------------------------------------------------------
_REDIRECTS = (301, 302, 303, 307, 308)


def _check_status(status: int) -> None:
    if status == 200:
        return
    if status in (401, 403, 407, 451):
        raise JobFetchError(
            "That website asked for a login or blocked automatic reading. I do not get around that."
            + PASTE_HINT
        )
    if status in (404, 410):
        raise JobFetchError("That page was not found. The job may have been taken down." + PASTE_HINT)
    if status == 429:
        raise JobFetchError("That website says there were too many requests. Try again later." + PASTE_HINT)
    raise JobFetchError(f"That website answered with an error (code {status})." + PASTE_HINT, 502)


def _read_limited(response: RawResponse, max_bytes: int) -> bytes:
    declared = response.headers.get("content-length", "").strip()
    if declared.isdigit() and int(declared) > max_bytes:
        raise JobFetchError("That page is too large to read safely." + PASTE_HINT)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(65_536, max_bytes + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise JobFetchError("That page is too large to read safely." + PASTE_HINT)
        chunks.append(chunk)
    return b"".join(chunks)


def _decode(body: bytes, content_type: str) -> str:
    charset = ""
    m = re.search(r"charset\s*=\s*[\"']?([\w.:-]+)", content_type, re.I)
    if m:
        charset = m.group(1)
    else:
        m = re.search(rb"<meta[^>]+charset\s*=\s*[\"']?([\w.:-]+)", body[:4096], re.I)
        if m:
            charset = m.group(1).decode("ascii", "ignore")
    for name in (charset, "utf-8"):
        if not name:
            continue
        try:
            return body.decode(name, errors="replace").lstrip("\ufeff")
        except LookupError:
            continue
    return body.decode("utf-8", errors="replace")


def fetch_job_text(
    url: str,
    *,
    resolver: Callable[[str, int], list[str]] = resolve_host,
    opener: Callable[..., RawResponse] = _open_pinned,
    is_blocked: Callable[[str], bool] = is_blocked_ip,
    allowed_ports=ALLOWED_PORTS,
    timeout: float = TOTAL_TIMEOUT,
    max_bytes: int = MAX_BYTES,
) -> FetchedPage:
    """Fetch a public job page and return plain text ready for extract_job().

    resolver / opener / is_blocked / allowed_ports / timeout / max_bytes exist so
    the tests can swap the network out. The app never changes them.
    """
    deadline = time.monotonic() + timeout
    current = url
    for hop in range(MAX_REDIRECTS + 1):
        scheme, host, port, target, clean = parse_public_url(current, allowed_ports)
        addresses = _public_addresses(host, port, resolver, is_blocked)
        _time_left(deadline)
        response = opener(scheme, host, port, target, addresses, deadline)
        try:
            if response.status in _REDIRECTS:
                location = response.headers.get("location", "").strip()
                if not location:
                    raise JobFetchError("That website sent me somewhere without saying where." + PASTE_HINT, 502)
                if hop == MAX_REDIRECTS:
                    raise JobFetchError("That link redirects too many times." + PASTE_HINT, 502)
                current = urljoin(clean, location)  # checked again at the top of the loop
                continue

            _check_status(response.status)

            content_type = response.headers.get("content-type", "")
            main_type = content_type.split(";")[0].strip().lower()
            if main_type not in ALLOWED_TYPES:
                raise JobFetchError(
                    "That link is not a normal web page (it is "
                    + (main_type or "an unknown type")
                    + ")."
                    + PASTE_HINT
                )
            encoding = response.headers.get("content-encoding", "identity").strip().lower()
            if encoding not in ("", "identity"):
                raise JobFetchError("That website sent the page in a format I cannot read safely." + PASTE_HINT)

            body = _read_limited(response, max_bytes)
        finally:
            response.close()

        logger.info("job link fetched: host=%s bytes=%d", host, len(body))
        return page_to_text(_decode(body, content_type), main_type, clean)

    raise JobFetchError("That link redirects too many times." + PASTE_HINT, 502)  # pragma: no cover


# --------------------------------------------------------------------------
# 5. Page -> readable text
# --------------------------------------------------------------------------
_VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
_SKIP = {
    "script", "style", "noscript", "template", "svg", "iframe", "object",
    "canvas", "select", "option", "nav", "footer",
}
_BLOCK = {
    "p", "div", "section", "article", "main", "header", "aside", "h1", "h2", "h3",
    "h4", "h5", "h6", "ul", "ol", "li", "tr", "table", "dl", "dt", "dd",
    "blockquote", "pre", "br", "hr", "form", "fieldset", "details", "summary",
}
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?:px|pt|em|rem|%)?\s*(?:;|$)"
    r"|opacity\s*:\s*0(?:\.0+)?\s*(?:;|$)",
    re.I,
)
_MAX_DEPTH = 400
_INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\u00ad]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_INJECTION = re.compile(
    r"ignore (?:all |any |the )*(?:previous|prior|above|earlier) (?:instructions?|prompts?|messages?)"
    r"|disregard (?:all |any |the )*(?:previous|prior|above|earlier)"
    r"|(?:reveal|show|print) (?:your |the )?system prompt"
    r"|you are (?:now )?(?:an? )?(?:ai|chatgpt|language model|assistant)\b"
    r"|as an ai (?:model|assistant)",
    re.I,
)
_WALL = re.compile(
    r"captcha|verify (?:that )?you(?:'re| are) (?:a )?human|are you a robot|unusual traffic"
    r"|(?:sign|log) ?in to (?:view|continue|see|apply)|enable javascript|access denied"
    r"|just a moment|checking your browser",
    re.I,
)


def _is_hidden(attrs: dict[str, str]) -> bool:
    return (
        "hidden" in attrs
        or attrs.get("aria-hidden", "").strip().lower() == "true"
        or bool(_HIDDEN_STYLE.search(attrs.get("style", "")))
    )


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[tuple[str, bool]] = []
        self.skip = 0
        self.hidden_elements = 0
        self.title = ""
        self.h1 = ""
        self.meta: dict[str, str] = {}
        self.json_ld: list[str] = []
        self._in_title = False
        self._in_h1 = False
        self._ld: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in _VOID:
            if tag == "meta":
                key = (a.get("property") or a.get("name") or "").lower()
                if key and "content" in a and key not in self.meta:
                    self.meta[key] = a["content"]
            elif tag in _BLOCK and not self.skip:
                self.parts.append("\n")
            return
        concealed = _is_hidden(a)
        if concealed:
            self.hidden_elements += 1
        skipped = concealed or tag in _SKIP
        if len(self.stack) < _MAX_DEPTH:
            self.stack.append((tag, skipped))
            if skipped:
                self.skip += 1
        if tag == "script" and a.get("type", "").split(";")[0].strip().lower() == "application/ld+json":
            self._ld = []
        elif tag == "title":
            self._in_title = True
        elif tag == "h1" and not self.skip and not self.h1:
            self._in_h1 = True
        elif not self.skip:
            if tag == "li":
                self.parts.append("\n- ")
            elif tag in ("td", "th"):
                self.parts.append(" ")
            elif tag in _BLOCK:
                self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        if tag == "script" and self._ld is not None:
            self.json_ld.append("".join(self._ld))
            self._ld = None
        if tag == "title":
            self._in_title = False
        if tag == "h1":
            self._in_h1 = False
        # Close back to the matching open tag; a stray end tag with no match is ignored.
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                for _, skipped in self.stack[index:]:
                    if skipped:
                        self.skip -= 1
                del self.stack[index:]
                if not self.skip and tag in _BLOCK:
                    self.parts.append("\n")
                break

    def handle_data(self, data):
        if self._ld is not None:
            self._ld.append(data)
        elif self._in_title:
            self.title += data
        elif self._in_h1:
            self.h1 += data
            if not self.skip:
                self.parts.append(data)
        elif not self.skip:
            self.parts.append(data)


def _tidy(text: str) -> str:
    text = text.replace("\xa0", " ")
    lines: list[str] = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
        elif lines and lines[-1] != "":
            lines.append("")
    return "\n".join(lines).strip()


def _scrub(text: str) -> tuple[str, int]:
    """Remove invisible and control characters; return the text and how many went."""
    removed = len(_INVISIBLE.findall(text)) + len(_CONTROL.findall(text))
    return _CONTROL.sub("", _INVISIBLE.sub("", text)), removed


def _html_to_text(markup: str) -> tuple[_Extractor, str]:
    extractor = _Extractor()
    try:
        extractor.feed(markup)
        extractor.close()
    except Exception:  # broken markup must never crash an import
        logger.info("job page markup could not be fully parsed")
    return extractor, _tidy("".join(extractor.parts))


def _plain(value) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip() if isinstance(value, str) else ""


def _walk(data, limit: int = 300):
    stack, seen = [data], 0
    while stack and seen < limit:
        node = stack.pop()
        seen += 1
        if isinstance(node, dict):
            yield node
            stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            stack.extend(node)


def _job_posting(blocks: list[str]) -> dict | None:
    for raw in blocks:
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for node in _walk(data):
            kind = node.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if "JobPosting" in kinds:
                return node
    return None


def _first(value):
    return value[0] if isinstance(value, list) and value else value


def _posting_header(jp: dict) -> list[str]:
    """Turn a JobPosting record into 'Label: value' lines the parser already reads."""
    lines: list[str] = []
    if title := _plain(jp.get("title")):
        lines.append(f"Title: {title}")
    org = jp.get("hiringOrganization")
    company = _plain(org.get("name")) if isinstance(org, dict) else _plain(org)
    if company:
        lines.append(f"Company: {company}")

    place = _first(jp.get("jobLocation"))
    address = place.get("address") if isinstance(place, dict) else None
    if isinstance(address, dict):
        country = address.get("addressCountry")
        if isinstance(country, dict):
            country = country.get("name")
        bits = [_plain(address.get("addressLocality")), _plain(address.get("addressRegion")), _plain(country)]
        if location := ", ".join(b for b in bits if b):
            lines.append(f"Location: {location}")
    if str(jp.get("jobLocationType", "")).upper() == "TELECOMMUTE":
        lines.append("Work type: remote")

    pay = jp.get("baseSalary")
    if isinstance(pay, dict):
        value = pay.get("value") if isinstance(pay.get("value"), dict) else pay
        low, high = value.get("minValue"), value.get("maxValue")
        single = value.get("value") if not isinstance(value.get("value"), dict) else None
        currency = str(pay.get("currency", "")).upper()
        unit = str(value.get("unitText", "")).upper()
        period = {"YEAR": "per year", "MONTH": "per month"}.get(unit)
        numbers = [n for n in (low, high or single) if isinstance(n, (int, float)) and not isinstance(n, bool)]
        if numbers and period and currency in ("INR", "USD", "EUR", "GBP"):
            amount = " - ".join(f"{int(n):,}" for n in numbers)
            lines.append(f"Salary: {currency} {amount} {period}")
    return lines


_LOGIN_OR_EMPTY = (
    "I could not read any job text from that page. It may need JavaScript or a login, "
    "or it may block automatic reading." + PASTE_HINT
)


def page_to_text(markup: str, content_type: str, url: str) -> FetchedPage:
    """Turn a downloaded page into draft-ready text plus any warnings."""
    warnings: list[str] = []
    removed = 0

    if content_type == "text/plain":
        visible = _tidy(markup)
        header: list[str] = []
        description = ""
        hidden = 0
    else:
        extractor, visible = _html_to_text(markup)
        hidden = extractor.hidden_elements
        posting = _job_posting(extractor.json_ld)
        header, description = [], ""
        if posting:
            header = _posting_header(posting)
            raw_description = posting.get("description")
            if isinstance(raw_description, str):
                if "<" not in raw_description and "&lt;" in raw_description:
                    raw_description = html.unescape(raw_description)
                description = _html_to_text(raw_description)[1]
            for label in ("skills", "qualifications"):
                extra = _plain(posting.get(label))
                if extra:
                    description += f"\n{label.capitalize()}: {extra}"
        else:
            # Prefer the visible job heading when the page has one. Otherwise use
            # Open Graph or document title metadata. Label it so extract_job() can
            # reliably distinguish it from ordinary description text.
            heading = _plain(extractor.h1)
            page_title = _plain(
                extractor.meta.get("og:title")
                or extractor.meta.get("twitter:title")
                or extractor.meta.get("title")
                or extractor.title
            )
            fallback_title = heading or page_title
            if fallback_title:
                header = [f"Page title: {fallback_title}"]

        if not any(line.startswith("Title: ") or line.startswith("Page title: ") for line in header):
            fallback_title = _plain(extractor.h1 or extractor.title)
            if fallback_title:
                header.append(f"Page title: {fallback_title}")
        if not any(line.startswith("Company: ") for line in header):
            site_name = _plain(extractor.meta.get("og:site_name"))
            if site_name:
                header.append(f"Site name: {site_name}")
        if not description:
            metadata_description = (
                extractor.meta.get("og:description")
                or extractor.meta.get("twitter:description")
                or extractor.meta.get("description")
                or ""
            )
            if metadata_description:
                metadata_description = html.unescape(metadata_description)
                description = (
                    _html_to_text(metadata_description)[1]
                    if "<" in metadata_description
                    else _plain(metadata_description)
                )

    body = description
    if visible and visible not in description:
        body = f"{description}\n\n{visible}".strip() if description else visible
    text = "\n".join(header + ([""] if header else []) + [body]).strip()

    text, removed = _scrub(text)
    text = _tidy(text)

    if len(_tidy(body)) < MIN_READABLE_CHARS:
        raise JobFetchError(_LOGIN_OR_EMPTY)
    if len(text) < 800 and _WALL.search(text):
        raise JobFetchError(
            "That page looks like a login or verification page, which I do not get around." + PASTE_HINT
        )

    if removed:
        warnings.append("Hidden characters were removed from the page text.")
    if hidden:
        warnings.append("Parts of the page that are hidden from view were ignored.")
    if _INJECTION.search(text):
        warnings.append(
            "The page contains text that looks like instructions to an AI. "
            "It was treated as plain text only and not followed."
        )
    if len(text) > MAX_IMPORT_CHARS:
        text = text[:MAX_IMPORT_CHARS].rsplit("\n", 1)[0].rstrip()
        warnings.append("The page was very long, so only the first part was read.")

    return FetchedPage(text=text, final_url=url, warnings=warnings)
