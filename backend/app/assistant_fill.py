"""Browser-assisted form filling (Playwright).

What this module does:   open an allowed application page in a fresh browser
                         profile, find the form fields, fill the ones that match
                         a confirmed Application Kit value, report every field.
What it never does:      click anything, press Enter, tick consent boxes,
                         solve CAPTCHAs, log in, or submit. Resume upload is
                         available only after explicit user consent. The
                         browser never clicks Submit. Non-read requests are
                         blocked until a separate post-review authorization.

Field outcomes: filled | skipped | needs_input | failed.
Run outcomes:   prepared | needs_you | failed  (never "submitted").
"""

import logging
import re
import time
from typing import Callable
from urllib.parse import urlsplit

from app.assistant_kit import ANSWER_LABELS, DRAFT_LABELS, FACT_LABELS
from app.assistant_urls import (
    FillTarget,
    adapter_for_host,
    check_public_host,
    is_blocked_host,
    is_non_public_host,
)

logger = logging.getLogger(__name__)

FILLED, SKIPPED, NEEDS_INPUT, FAILED = "filled", "skipped", "needs_input", "failed"
PREPARED, NEEDS_YOU, RUN_FAILED = "prepared", "needs_you", "failed"

# ---------------- pure matching (no browser) ----------------

_NOT_PERSON = r"company|employer|user|manager|referr|reference|school|university|emergency|parent|spouse"

# key -> (positive regex, negative regex or None). Applied to the field's
# normalised label/name/placeholder text.
RULES: dict[str, tuple[str, str | None]] = {
    "first_name": (r"first.?name|given.?name|\bfname\b", None),
    "last_name": (r"last.?name|family.?name|surname|\blname\b", None),
    "full_name": (r"full.?name|^name$|your name|legal name|candidate name|applicant name",
                  r"first|last|" + _NOT_PERSON),
    "email": (r"e-?mail", None),
    "phone": (r"phone|mobile|telephone|contact number|\btel\b", None),
    "location": (r"\blocation\b|\bcity\b|where are you (based|located)|current location",
                 r"relocat|office|preferred|work location"),
    "linkedin": (r"linkedin", None),
    "github": (r"github", None),
    "portfolio": (r"portfolio|personal (web)?site|^website$|your website|personal url", None),
    "current_company": (r"current.*(company|employer)|present employer", None),
    "current_title": (r"current.*(title|role|position|job)", None),
    "notice_period": (r"notice.?period|how soon.*(join|start)|earliest.*start|available to start", None),
    "expected_salary": (r"(expected|desired|target).*(salary|compensation|ctc|pay)|salary expectation|compensation expectation",
                        None),
    "work_authorization": (r"authori[sz]ed to work|work authori[sz]ation|legally.*(authori[sz]ed|permitted)|right to work|eligible to work",
                           None),
    "visa_sponsorship": (r"sponsor|\bvisa\b", None),
    # long-form drafts (textarea only)
    "tell_me_about_yourself": (r"about yourself|introduce yourself|tell us about you|short bio", None),
    "why_role": (r"why.*(role|position|job)|interested in this (role|position)|why do you want", None),
    "why_company": (r"why.*(company|join|work (at|for|with)|\bus\b)|interest in (the )?company", None),
    "strengths": (r"strength|why should we hire|what makes you", None),
    "project_example": (r"\bproject\b|example of (your )?work|relevant experience", None),
}
_DRAFT_KEYS = set(DRAFT_LABELS)

# Demographic / sensitive questions: never filled, always the user's call.
SENSITIVE_RE = re.compile(
    r"gender|\brace\b|ethnic|veteran|disabilit|sexual|orientation|pronoun|religio|marital|"
    r"date of birth|\bdob\b|nationality|citizenship|criminal|conviction|hispanic|lgbt|caste|"
    r"salary history|current (salary|ctc|compensation)",
    re.I,
)
_SALARY_VAGUE_RE = re.compile(r"salary|compensation|\bctc\b", re.I)


def normalise(*parts: str) -> str:
    text = " ".join(p for p in parts if p)
    text = re.sub(r"[_\-\*:]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _hit(key: str, text: str, kind: str) -> bool:
    pos, neg = RULES[key]
    if key in _DRAFT_KEYS and kind != "textarea":
        return False
    if neg and re.search(neg, text):
        return False
    return bool(re.search(pos, text))


def match_field(desc: dict, values: dict[str, dict]) -> dict:
    """Decide what to do with one field, without touching the browser.

    `desc`   {kind, type, label, name, placeholder, autocomplete, required, has_value, options}
    `values` {kit_key: {"value": str, "source": str}}  (only confirmed values)
    Returns  {state, key, reason, value, source}; `value` only when state == filled-to-be.
    """
    kind = desc.get("kind", "input")
    ftype = (desc.get("type") or "text").lower()
    label = (desc.get("label") or desc.get("placeholder") or desc.get("name") or "field")[:120]
    required = bool(desc.get("required"))
    text = normalise(desc.get("label", ""), desc.get("aria", ""), desc.get("placeholder", ""),
                     desc.get("name", ""), desc.get("autocomplete", ""))

    def out(state, reason, key="", value="", source=""):
        return {"label": label, "state": state, "key": key, "reason": reason,
                "required": required, "value": value, "source": source}

    unmatched_state = NEEDS_INPUT if required else SKIPPED

    if ftype == "file":
        return out(NEEDS_INPUT, "File upload: attach your resume yourself.")
    if ftype in ("checkbox", "radio"):
        return out(unmatched_state, "Choice and consent fields are never ticked automatically.")
    if desc.get("has_value"):
        return out(SKIPPED, "Already has a value; left unchanged.")

    if SENSITIVE_RE.search(text):
        return out(NEEDS_INPUT, "Sensitive question: answer it yourself on the form.")

    # email/tel/url inputs advertise their meaning even with odd labels
    hits = [k for k in RULES if _hit(k, text, kind)]
    if not hits and ftype == "email":
        hits = ["email"]
    if not hits and ftype == "tel":
        hits = ["phone"]
    if not hits and _SALARY_VAGUE_RE.search(text):
        return out(unmatched_state, "Unclear which salary is asked; answer it yourself.")

    if len(hits) > 1:
        return out(unmatched_state, "Ambiguous: matches more than one kit item (" + ", ".join(hits) + ").")
    if not hits:
        return out(unmatched_state, "No confirmed kit answer matches this field.")

    key = hits[0]
    entry = values.get(key)
    nice = {**FACT_LABELS, **ANSWER_LABELS, **DRAFT_LABELS}[key]
    if not entry or not entry.get("value"):
        return out(NEEDS_INPUT, f"Needs your input: {nice} is not in your kit.", key=key)

    value = entry["value"]
    if kind == "select":
        opt = pick_option(desc.get("options") or [], value)
        if opt is None:
            return out(NEEDS_INPUT, f"No dropdown option clearly matches your {nice.lower()}.", key=key)
        value = opt
    return out(FILLED, "", key=key, value=value, source=entry.get("source", ""))


def pick_option(options: list[dict], answer: str) -> str | None:
    """Option value matching the answer: exact (case-insensitive), else a single prefix match."""
    a = answer.strip().casefold()
    if not a:
        return None
    for o in options:
        if a in (str(o.get("text", "")).strip().casefold(), str(o.get("value", "")).strip().casefold()):
            return str(o.get("value"))
    pref = [o for o in options if str(o.get("text", "")).strip().casefold().startswith(a) and o.get("value") != ""]
    return str(pref[0].get("value")) if len(pref) == 1 else None


def summarise(fields: list[dict], page_flags: dict) -> tuple[str, str]:
    """(run status, pause reason). Never returns 'submitted'."""
    if page_flags.get("login"):
        return NEEDS_YOU, "login_required"
    if page_flags.get("captcha"):
        return NEEDS_YOU, "captcha"
    if not fields:
        return NEEDS_YOU, "unsupported_form"
    if any(f["state"] == FAILED for f in fields):
        return NEEDS_YOU, "field_failed"
    if any(f["state"] == NEEDS_INPUT and f.get("required") for f in fields):
        return NEEDS_YOU, "required_fields_need_input"
    return PREPARED, ""


# ---------------- browser side ----------------

COLLECT_JS = """
() => {
  const out = [];
  let n = 0;
  const vis = (el) => {
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    return s.visibility !== 'hidden' && s.display !== 'none' && (r.width > 0 || r.height > 0);
  };
  const labelOf = (el) => {
    const bits = [];
    if (el.id) document.querySelectorAll('label[for="' + CSS.escape(el.id) + '"]').forEach(l => bits.push(l.innerText));
    const wrap = el.closest('label'); if (wrap) bits.push(wrap.innerText);
    const lb = el.getAttribute('aria-labelledby');
    if (lb) lb.split(/\\s+/).forEach(id => { const e = document.getElementById(id); if (e) bits.push(e.innerText); });
    if (!bits.length) {
      const grp = el.closest('.field, .form-group, .application-question, .application-field, li, fieldset, div');
      const l = grp && grp.querySelector('label, legend'); if (l) bits.push(l.innerText);
    }
    return bits.join(' ').trim();
  };
  document.querySelectorAll('input, textarea, select').forEach(el => {
    const type = (el.getAttribute('type') || (el.tagName === 'SELECT' ? 'select' : 'text')).toLowerCase();
    if (['hidden','submit','button','image','reset'].includes(type)) return;
    if (!vis(el)) return;
    if (el.disabled || el.readOnly) return;
    el.setAttribute('data-cp-idx', String(n));
    out.push({
      idx: n++, kind: el.tagName.toLowerCase(), type,
      label: labelOf(el), aria: el.getAttribute('aria-label') || '',
      placeholder: el.getAttribute('placeholder') || '', name: el.getAttribute('name') || el.id || '',
      autocomplete: el.getAttribute('autocomplete') || '',
      required: el.required || el.getAttribute('aria-required') === 'true',
      has_value: (type === 'file' || type === 'checkbox' || type === 'radio') ? false
                 : (el.tagName === 'SELECT' ? (el.value !== '' && el.selectedIndex > 0) : (el.value || '').trim() !== ''),
      options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => ({value: o.value, text: o.text})) : [],
    });
  });
  return out;
}
"""

FLAGS_JS = """
() => {
  const q = (s) => !!document.querySelector(s);
  const pw = Array.from(document.querySelectorAll('input[type=password]')).some(e => e.offsetParent !== null);
  const captcha = q('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="turnstile"], .g-recaptcha, .h-captcha, .cf-turnstile, [data-sitekey]')
    || /verify (that )?you are (a )?human|i'm not a robot/i.test(document.body ? document.body.innerText : '');
  return {password: pw, captcha: captcha};
}
"""

APPLY_LINK_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && (r.width > 0 || r.height > 0);
  };
  const link = Array.from(document.querySelectorAll('a[href]')).find((el) => {
    const text = (el.innerText || el.getAttribute('aria-label') || '').trim();
    return visible(el) && /^apply(?:\\s|$)|apply for (this|the) job/i.test(text);
  });
  return link ? link.href : '';
}
"""

_LOGIN_URL_RE = re.compile(r"/(log-?in|sign-?in|sso|auth)(/|$|\?)", re.I)


def _same_origin_application_link(href: str, target: FillTarget) -> bool:
    try:
        parts = urlsplit(href)
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme.lower() == "https"
        and adapter_for_host(parts.hostname or "") == target.adapter
        and port in (None, 443)
        and not parts.username
        and not parts.password
    )


def _is_safe_main_frame_navigation(url: str, target: FillTarget) -> bool:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.username or parts.password or is_blocked_host(host):
        return False
    if target.adapter == "local_test":
        target_parts = urlsplit(target.url)
        return (
            parts.scheme.lower() == "http"
            and host == (target_parts.hostname or "").lower().rstrip(".")
            and port == target_parts.port
            and parts.path == "/assistant-test-form"
        )
    return (
        parts.scheme.lower() == "https"
        and port in (None, 443)
        and adapter_for_host(host) == target.adapter
    )


def _route_guard(
    counter: dict,
    target: FillTarget | None = None,
    submission_authorized=None,
):
    """Block writes for the whole browser session and reject unsafe navigation."""
    def handler(route):
        req = route.request
        parts = urlsplit(req.url)
        host = (parts.hostname or "").lower().rstrip(".")
        local_target = target is not None and target.adapter == "local_test"
        target_parts = urlsplit(target.url) if local_target else None
        is_target_local_origin = bool(
            target_parts
            and host == (target_parts.hostname or "").lower().rstrip(".")
            and parts.port == target_parts.port
            and parts.path == "/assistant-test-form"
        )
        try:
            is_top_level_navigation = (
                target is not None
                and req.is_navigation_request()
                and req.frame.parent_frame is None
            )
        except Exception:  # noqa: BLE001 - a detached frame is not a trusted navigation
            is_top_level_navigation = True
        if is_top_level_navigation and not _is_safe_main_frame_navigation(req.url, target):
            counter["blocked_navigations"] += 1
            counter["blocked_top_level_navigations"] = (
                counter.get("blocked_top_level_navigations", 0) + 1
            )
            route.abort()
            return
        if req.method not in ("GET", "HEAD", "OPTIONS"):
            allowed_manual_write = bool(
                submission_authorized
                and submission_authorized.is_set()
                and target
                and target.adapter != "local_test"
                and host == target.host
                and parts.scheme.lower() == "https"
                and parts.port in (None, 443)
                and not parts.username
                and not parts.password
            )
            if allowed_manual_write:
                try:
                    check_public_host(host)
                    route.continue_()
                except Exception as exc:
                    logger.warning("Authorized browser request rejected category=%s", type(exc).__name__)
                    counter["blocked_navigations"] += 1
                    counter["blocked_writes"] += 1
                    route.abort()
            else:
                counter["blocked_writes"] += 1
                route.abort()
        elif parts.scheme.lower() not in ("http", "https", "data", "blob", "about"):
            counter["blocked_navigations"] += 1
            route.abort()
        elif not local_target and parts.port not in (None, 80, 443):
            counter["blocked_navigations"] += 1
            route.abort()
        elif parts.username or parts.password or is_blocked_host(host) or (
            is_non_public_host(host) and not is_target_local_origin
        ):
            counter["blocked_navigations"] += 1
            route.abort()
        elif not local_target and parts.scheme.lower() == "http":
            counter["blocked_navigations"] += 1
            route.abort()
        elif not local_target and parts.port not in (None, 443):
            counter["blocked_navigations"] += 1
            route.abort()
        elif local_target and not is_target_local_origin:
            counter["blocked_navigations"] += 1
            route.abort()
        else:
            try:
                if not is_target_local_origin and parts.scheme.lower() in ("http", "https"):
                    check_public_host(host)
                route.continue_()
            except Exception as exc:
                logger.warning("Browser request rejected category=%s", type(exc).__name__)
                counter["blocked_navigations"] += 1
                route.abort()
    return handler


def _fill_frame(
    frame,
    values: dict[str, dict],
    resume_file: tuple[bytes, str] | None = None,
) -> list[dict]:
    fields: list[dict] = []
    for desc in frame.evaluate(COLLECT_JS):
        result = match_field(desc, values)
        if (
            desc.get("type") == "file"
            and resume_file
            and re.search(r"\b(resume|cv)\b|curriculum vitae", result["label"], re.I)
        ):
            try:
                frame.locator(f'[data-cp-idx="{desc["idx"]}"]').set_input_files(
                    {
                        "name": resume_file[1],
                        "mimeType": "application/pdf",
                        "buffer": resume_file[0],
                    }
                )
                result.update(
                    state=FILLED,
                    reason="Selected resume attached. Review it before submission.",
                    key="resume",
                    source="user_selected",
                )
            except Exception:
                result.update(state=FAILED, reason="Could not attach the selected resume.")
        if result["state"] == FILLED:
            loc = frame.locator(f'[data-cp-idx="{desc["idx"]}"]')
            try:
                if desc.get("type") == "file":
                    pass
                elif desc["kind"] == "select":
                    loc.select_option(result["value"], timeout=4000)
                    ok = loc.input_value(timeout=2000) == result["value"]
                else:
                    loc.fill(result["value"], timeout=4000)
                    ok = loc.input_value(timeout=2000) == result["value"]
                valid = loc.evaluate("el => el.checkValidity()") if ok else True
                if not ok:
                    result.update(state=FAILED, reason="The page did not keep the value.")
                elif not valid:
                    result.update(state=FAILED, reason="The page reports this value as invalid.")
            except Exception:  # noqa: BLE001 - report, never crash the run
                result.update(state=FAILED, reason="Could not fill this field.")
        result.pop("value", None)  # never keep personal values in results
        fields.append(result)
    return fields


def run_assistance(
    target: FillTarget,
    values: dict[str, dict],
    *,
    headless: bool = False,
    keep_open_seconds: int = 0,
    on_result: Callable[[dict], None] | None = None,
    browser_path: str | None = None,
    resume_file: tuple[bytes, str] | None = None,
    submission_authorized=None,
) -> dict:
    """Open the page, fill matched fields, report. Blocking: run it in a thread.

    `on_result` is called as soon as filling finishes, before the optional
    keep-open wait, so the UI can show results while the user's browser is open.
    """
    started = time.time()
    counter = {
        "blocked_writes": 0,
        "blocked_navigations": 0,
        "blocked_top_level_navigations": 0,
    }
    result = {"status": RUN_FAILED, "pause_reason": "", "fields": [], "host": target.host}

    try:
        from playwright.sync_api import sync_playwright  # imported lazily: optional dependency

        if target.adapter != "local_test":
            check_public_host(target.host)
        with sync_playwright() as pw:
            launch = {"headless": headless}
            if browser_path:
                launch["executable_path"] = browser_path
            browser = pw.chromium.launch(**launch)
            context = browser.new_context()  # fresh profile: no cookies in, none persisted out
            context.route(
                "**/*",
                _route_guard(counter, target, submission_authorized),
            )
            page = context.new_page()
            try:
                page.goto(target.url, wait_until="domcontentloaded", timeout=30000)
                try:
                    page.wait_for_load_state("networkidle", timeout=6000)
                except Exception:  # noqa: BLE001 - slow pages are fine
                    pass
                if counter["blocked_top_level_navigations"]:
                    raise RuntimeError("Unsupported top-level redirect blocked.")

                flags_raw = page.evaluate(FLAGS_JS)
                flags = {
                    "captcha": bool(flags_raw.get("captcha")),
                    "login": bool(flags_raw.get("password")) or bool(_LOGIN_URL_RE.search(urlsplit(page.url).path)),
                }

                fields: list[dict] = []
                if not flags["login"]:
                    visible_fields = any(
                        frame.evaluate(COLLECT_JS) for frame in page.frames
                    )
                    if not visible_fields and target.adapter in ("greenhouse", "lever"):
                        application_link = page.evaluate(APPLY_LINK_JS)
                        if application_link and _same_origin_application_link(application_link, target):
                            page.goto(application_link, wait_until="domcontentloaded", timeout=30000)
                            try:
                                page.wait_for_load_state("networkidle", timeout=6000)
                            except Exception:  # noqa: BLE001 - slow application pages are fine
                                pass
                            if counter["blocked_top_level_navigations"]:
                                raise RuntimeError("Unsupported top-level redirect blocked.")
                            flags_raw = page.evaluate(FLAGS_JS)
                            flags = {
                                "captcha": bool(flags_raw.get("captcha")),
                                "login": bool(flags_raw.get("password"))
                                or bool(_LOGIN_URL_RE.search(urlsplit(page.url).path)),
                            }
                    if not flags["login"]:
                        per_frame = [
                            _fill_frame(fr, values, resume_file) for fr in page.frames
                        ]
                        fields = max(per_frame, key=len, default=[])

                status, pause = summarise(fields, flags)
                result.update(status=status, pause_reason=pause, fields=fields)
                if pause == "unsupported_form":
                    result["error_category"] = "unsupported_form"
            except Exception as exc:  # noqa: BLE001
                logger.warning("Assistance run failed category=%s", type(exc).__name__)
                if counter["blocked_top_level_navigations"]:
                    pause, category = "unsafe_redirect", "unsupported_redirect"
                elif type(exc).__name__ in ("TimeoutError", "TimeoutErrorImpl"):
                    pause, category = "navigation_timeout", "navigation_timeout"
                else:
                    pause, category = "navigation_failed", "navigation_failed"
                result.update(status=RUN_FAILED, pause_reason=pause, error_category=category)

            result["blocked_writes"] = counter["blocked_writes"]
            result["duration_ms"] = int((time.time() - started) * 1000)
            # Keep the network guard active while the visible page remains open;
            # page scripts must not gain a chance to submit after autofill.
            if not headless and keep_open_seconds > 0 and result["status"] != RUN_FAILED:
                result["browser_open"] = True
                if on_result:
                    on_result(result)
                deadline = time.time() + keep_open_seconds
                while time.time() < deadline and not page.is_closed():
                    try:
                        page.wait_for_timeout(1000)
                    except Exception:  # noqa: BLE001 - window closed
                        break
                result["blocked_writes"] = counter["blocked_writes"]
                result["browser_open"] = False
            elif on_result:
                on_result(result)
            browser.close()
    except UrlRejected as exc:
        logger.warning("Application destination rejected code=%s", exc.code)
        result.update(
            status=RUN_FAILED,
            pause_reason="unsafe_destination",
            error_category=exc.code,
            fields=[],
        )
        if on_result:
            on_result(result)
    except Exception as exc:  # noqa: BLE001
        category = "playwright_unavailable" if isinstance(exc, ImportError) else "browser_launch_failed"
        logger.warning("Browser could not start category=%s", type(exc).__name__)
        result.update(
            status=RUN_FAILED,
            pause_reason="browser_unavailable",
            error_category=category,
            fields=[],
        )
        if on_result:
            on_result(result)
    return result
