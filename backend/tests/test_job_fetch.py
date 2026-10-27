import json

import pytest

from app.job_fetch import (
    JobFetchError,
    RawResponse,
    _check_status,
    fetch_job_text,
    is_search_results_url,
    page_to_text,
)
from app.job_import import extract_job


JOB_URL = "https://jobs.example.com/careers/job/123"


def _read_once(content: bytes):
    offset = 0

    def read(size: int) -> bytes:
        nonlocal offset
        chunk = content[offset:offset + size]
        offset += len(chunk)
        return chunk

    return read


def _fetch_markup(markup: str, *, content_type: str = "text/html; charset=utf-8"):
    body = markup.encode("utf-8")
    response = RawResponse(
        status=200,
        headers={"content-type": content_type},
        read=_read_once(body),
        close=lambda: None,
    )
    return fetch_job_text(
        JOB_URL,
        resolver=lambda _host, _port: ["93.184.216.34"],
        opener=lambda *_args: response,
        is_blocked=lambda _address: False,
    )


def test_html_metadata_and_visible_content_create_a_useful_draft():
    markup = """
    <html><head>
      <title>Open Roles | Acme</title>
      <meta property="og:title" content="Senior Platform Engineer">
      <meta property="og:site_name" content="Acme">
      <meta property="og:description" content="Build reliable platform services with Python and cloud infrastructure.">
    </head><body>
      <p>Location: Toronto, Canada</p>
      <p>Join our platform group to build reliable services, improve developer tools, and operate production systems.</p>
    </body></html>
    """

    page = _fetch_markup(markup)
    result = extract_job(page.text, JOB_URL)["draft"]

    assert result["title"] == "Senior Platform Engineer"
    assert result["company"] == "Acme"
    assert result["location"] == "Toronto, Canada"
    assert "Build reliable platform services" in result["description"]


def test_json_ld_job_posting_is_extracted():
    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Data Engineer",
        "hiringOrganization": {"@type": "Organization", "name": "Northwind"},
        "jobLocation": {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": "Seattle",
                "addressRegion": "Washington",
                "addressCountry": "US",
            },
        },
        "description": "<p>Build and maintain data pipelines using Python, SQL, and modern cloud platforms.</p>",
    }
    markup = (
        '<html><head><script type="application/ld+json">'
        + json.dumps(posting)
        + "</script></head><body><main>Open role details</main></body></html>"
    )

    page = _fetch_markup(markup)
    result = extract_job(page.text, JOB_URL)["draft"]

    assert result["title"] == "Data Engineer"
    assert result["company"] == "Northwind"
    assert result["location"] == "Seattle, Washington, US"
    assert {"python", "sql"} <= set(result["skills"])


def test_h1_with_incomplete_job_details_keeps_title_and_missing_fields():
    page = _fetch_markup(
        "<html><body><h1>Platform Engineer</h1>"
        "<p>Help us build dependable internal tools for engineering teams.</p></body></html>"
    )

    result = extract_job(page.text, JOB_URL)

    assert result["draft"]["title"] == "Platform Engineer"
    assert result["draft"]["company"] == ""
    assert result["draft"]["location"] == ""
    assert "company" in result["missing"]
    assert "location" in result["missing"]


def test_javascript_shell_without_readable_job_content_is_actionable():
    with pytest.raises(JobFetchError, match="may need JavaScript or a login"):
        page_to_text(
            '<html><head><title>Careers</title></head>'
            '<body><div id="app"></div><script src="/app.js"></script></body></html>',
            "text/html",
            JOB_URL,
        )


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://jobs.example.com/careers?start=0&sort_by=relevance", True),
        ("https://apply.careers.microsoft.com/careers?pid=12345", False),
        ("https://apply.careers.microsoft.com/careers/job/12345", False),
    ],
)
def test_search_results_url_detection(url, expected):
    assert is_search_results_url(url) is expected


def test_blocked_http_status_is_not_silently_converted():
    with pytest.raises(JobFetchError, match="blocked automatic reading"):
        _check_status(403)


def test_fetch_timeout_remains_an_explicit_error():
    def timeout(*_args):
        raise JobFetchError("That website took too long to answer.", 504)

    with pytest.raises(JobFetchError, match="took too long") as error:
        fetch_job_text(
            JOB_URL,
            resolver=lambda _host, _port: ["93.184.216.34"],
            opener=timeout,
            is_blocked=lambda _address: False,
        )
    assert error.value.status_code == 504


def test_private_destination_is_rejected_before_opening():
    def unexpected_open(*_args):
        pytest.fail("private address should be rejected before connecting")

    with pytest.raises(JobFetchError, match="private or internal address"):
        fetch_job_text("http://127.0.0.1/careers/job/123", opener=unexpected_open)


def test_redirect_to_private_address_is_rejected_before_following():
    opened_hosts = []
    closed = []

    def redirect(scheme, host, port, target, addresses, deadline):
        opened_hosts.append(host)
        return RawResponse(
            status=302,
            headers={"location": "http://internal.example/admin"},
            read=_read_once(b""),
            close=lambda: closed.append(True),
        )

    with pytest.raises(JobFetchError, match="private or internal address"):
        fetch_job_text(
            JOB_URL,
            resolver=lambda host, _port: (
                ["93.184.216.34"] if host == "jobs.example.com" else ["10.0.0.1"]
            ),
            opener=redirect,
        )

    assert opened_hosts == ["jobs.example.com"]
    assert closed == [True]


def test_response_body_limit_is_enforced_while_reading():
    response = RawResponse(
        status=200,
        headers={"content-type": "text/plain"},
        read=_read_once(b"12345"),
        close=lambda: None,
    )

    with pytest.raises(JobFetchError, match="page is too large"):
        fetch_job_text(
            JOB_URL,
            resolver=lambda _host, _port: ["93.184.216.34"],
            opener=lambda *_args: response,
            is_blocked=lambda _address: False,
            max_bytes=4,
        )
