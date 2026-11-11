"""Browser-assisted form filling and multi-step application automation (Playwright).

What this module does:   open an allowed job listing or application page,
                         allow manual sign-in when a login wall appears,
                         open the application modal/form (LinkedIn Easy Apply,
                         Indeed SmartApply, Naukri Apply/Chatbot, Glassdoor/ATS),
                         fill fields matching confirmed Application Kit values
                         across multi-step forms, and stop at the final review
                         screen while keeping the browser open for manual submit.
What it never does:      enter credentials, click Submit, tick arbitrary consent
                         boxes, invent unconfirmed answers, or solve CAPTCHAs.

Field outcomes: filled | skipped | needs_input | failed.
Run outcomes:   prepared | needs_you | failed  (never "submitted").

Diagnostic artifacts (traces, screenshots) may contain sensitive page content.
The caller stores them in a private per-user directory and serves them only to
the authenticated owner. This module never logs field values or credentials.
"""

import errno
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

from app.assistant_kit import ANSWER_LABELS, DRAFT_LABELS, FACT_LABELS
from app.assistant_urls import (
    FillTarget,
    UrlRejected,
    adapter_for_host,
    check_public_host,
    is_blocked_host,
    is_non_public_host,
    platform_for_host,
)

logger = logging.getLogger(__name__)
_PROFILE_LOCKS_GUARD = threading.Lock()
_PROFILE_THREAD_LOCKS: dict[str, threading.Lock] = {}
_EXTERNAL_CHROME_LOCK = threading.Lock()
_EXTERNAL_CHROME_PROCESSES: dict[str, tuple[subprocess.Popen, str]] = {}
_CDP_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1"})

FILLED, SKIPPED, NEEDS_INPUT, FAILED = "filled", "skipped", "needs_input", "failed"
PREPARED, NEEDS_YOU, RUN_FAILED = "prepared", "needs_you", "failed"

# ---------------- pure matching (no browser) ----------------

_NOT_PERSON = r"company|employer|user|manager|referr|reference|school|university|emergency|parent|spouse"

# key -> (positive regex, negative regex or None). Applied to the field's
# normalised label/name/placeholder text.
RULES: dict[str, tuple[str, str | None]] = {
    "first_name": (r"first.?name|given.?name|\bfname\b", None),
    "last_name": (r"last.?name|family.?name|surname|\blname\b", None),
    "full_name": (
        r"full.?name|^name$|your name|legal name|candidate name|applicant name",
        r"first|last|" + _NOT_PERSON,
    ),
    "email": (r"e-?mail", None),
    "phone": (r"phone|mobile|telephone|contact number|\btel\b", None),
    "location": (
        r"\blocation\b|\bcity\b|where are you (based|located)|current location|residing",
        r"relocat|office|preferred|work location",
    ),
    "linkedin": (r"linkedin", None),
    "github": (r"github", None),
    "portfolio": (r"portfolio|personal (web)?site|^website$|your website|personal url", None),
    "current_company": (r"current.*(company|employer)|present employer|most recent employer", None),
    "current_title": (r"current.*(title|role|position|job)|most recent (title|role)", None),
    "notice_period": (
        r"notice.?period|how soon.*(join|start)|earliest.*start|available to start|days to join",
        None,
    ),
    "expected_salary": (
        r"(expected|desired|target).*(salary|compensation|ctc|pay)|salary expectation|compensation expectation",
        None,
    ),
    "years_of_experience": (
        r"total.*(experience|exp)|years of.*experience|how many years.*experience|work experience.*years",
        None,
    ),
    "willing_to_relocate": (
        r"relocat|willing to move|comfortable commuting|commute",
        None,
    ),
    "work_authorization": (
        r"authori[sz]ed to work|work authori[sz]ation|legally.*(authori[sz]ed|permitted)|right to work|eligible to work",
        None,
    ),
    "visa_sponsorship": (r"sponsor|\bvisa\b", None),
    # long-form drafts (textarea only)
    "tell_me_about_yourself": (r"about yourself|introduce yourself|tell us about you|short bio", None),
    "why_role": (r"why.*(role|position|job)|interested in this (role|position)|why do you want", None),
    "why_company": (r"why.*(company|join|work (at|for|with)|\bus\b)|interest in (the )?company", None),
    "strengths": (r"strength|why should we hire|what makes you", None),
    "project_example": (r"\bproject\b|example of (your )?work|relevant experience", None),
}
_DRAFT_KEYS = set(DRAFT_LABELS)
_RADIO_SAFE_KEYS = frozenset({"work_authorization", "visa_sponsorship", "willing_to_relocate"})

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


def pick_option(options: list[dict], answer: str) -> str | None:
    """Option value matching the answer: exact (case-insensitive), single prefix, or single substring."""
    a = answer.strip().casefold()
    if not a:
        return None
    for o in options:
        if a in (str(o.get("text", "")).strip().casefold(), str(o.get("value", "")).strip().casefold()):
            return str(o.get("value"))
    pref = [
        o for o in options
        if str(o.get("text", "")).strip().casefold().startswith(a) and o.get("value") != ""
    ]
    if len(pref) == 1:
        return str(pref[0].get("value"))
    sub = [
        o for o in options
        if a in str(o.get("text", "")).strip().casefold() and o.get("value") != ""
    ]
    return str(sub[0].get("value")) if len(sub) == 1 else None


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
    text = normalise(
        desc.get("label", ""),
        desc.get("aria", ""),
        desc.get("placeholder", ""),
        desc.get("name", ""),
        desc.get("autocomplete", ""),
    )

    def out(state, reason, key="", value="", source=""):
        return {
            "label": label,
            "state": state,
            "key": key,
            "reason": reason,
            "required": required,
            "value": value,
            "source": source,
        }

    unmatched_state = NEEDS_INPUT if required else SKIPPED

    if ftype == "file":
        return out(NEEDS_INPUT, "File upload: attach your resume yourself.")
    if ftype == "checkbox":
        return out(unmatched_state, "Choice and consent fields are never ticked automatically.")
    if ftype == "radio":
        radio_options = desc.get("options") or []
        hits = [k for k in _RADIO_SAFE_KEYS if _hit(k, text, kind)]
        if len(hits) == 1 and radio_options:
            key = hits[0]
            entry = values.get(key)
            nice = {**FACT_LABELS, **ANSWER_LABELS, **DRAFT_LABELS}.get(key, key)
            if not entry or not entry.get("value"):
                return out(NEEDS_INPUT, f"Needs your input: {nice} is not in your kit.", key=key)
            opt = pick_option(radio_options, entry["value"])
            if opt is not None:
                return out(FILLED, "", key=key, value=opt, source=entry.get("source", ""))
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
    nice = {**FACT_LABELS, **ANSWER_LABELS, **DRAFT_LABELS}.get(key, key)
    if not entry or not entry.get("value"):
        return out(NEEDS_INPUT, f"Needs your input: {nice} is not in your kit.", key=key)

    value = entry["value"]
    if kind == "select":
        opt = pick_option(desc.get("options") or [], value)
        if opt is None:
            return out(NEEDS_INPUT, f"No dropdown option clearly matches your {nice.lower()}.", key=key)
        value = opt
    return out(FILLED, "", key=key, value=value, source=entry.get("source", ""))


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
(options) => {
  const out = [];
  let n = 0;
  const glassdoor = !!(options && options.glassdoor);
  const vis = (el) => {
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    return s.visibility !== 'hidden' && s.display !== 'none' && (r.width > 0 || r.height > 0);
  };
  const modalRoot = document.querySelector(
    '.jobs-easy-apply-modal, [data-test-modal-id="easy-apply-modal"], .chatbot_DrawerContentWrapper, .ia-BasePage'
  );
  const root = (modalRoot && vis(modalRoot)) ? modalRoot : document;

  const labelOf = (el) => {
    const bits = [];
    if (el.id) document.querySelectorAll('label[for="' + CSS.escape(el.id) + '"]').forEach(l => bits.push(l.innerText));
    const wrap = el.closest('label'); if (wrap) bits.push(wrap.innerText);
    const lb = el.getAttribute('aria-labelledby');
    if (lb) lb.split(/\\s+/).forEach(id => { const e = document.getElementById(id); if (e) bits.push(e.innerText); });
    const fs = el.closest('fieldset');
    if (fs) {
      const leg = fs.querySelector('legend, [data-test-form-element-label], .fb-dash-form-element__label');
      if (leg) bits.unshift(leg.innerText);
    }
    if (!bits.length) {
      const grp = el.closest('.field, .form-group, .application-question, .application-field, .jobs-easy-apply-form-section__grouping, .fb-dash-form-element, li, fieldset, div');
      const l = grp && grp.querySelector('label, legend, [data-test-form-element-label]');
      if (l) bits.push(l.innerText);
    }
    return bits.join(' ').trim();
  };

  const seenRadioGroups = new Set();
  root.querySelectorAll('input, textarea, select').forEach(el => {
    if (glassdoor && !el.closest('[data-cp-glassdoor-application-form="true"]')) return;
    const type = (el.getAttribute('type') || (el.tagName === 'SELECT' ? 'select' : 'text')).toLowerCase();
    if (['hidden','submit','button','image','reset'].includes(type)) return;
    if (!vis(el) && type !== 'file') return;
    if (el.disabled || el.readOnly) return;

    if (type === 'radio') {
      const fs = el.closest('fieldset');
      const groupKey = el.name || (fs ? 'fs-' + Array.from(root.querySelectorAll('fieldset')).indexOf(fs) : '');
      if (groupKey && seenRadioGroups.has(groupKey)) return;
      if (groupKey) seenRadioGroups.add(groupKey);
      const radios = fs
        ? Array.from(fs.querySelectorAll('input[type="radio"]'))
        : (el.name ? Array.from(root.querySelectorAll('input[type="radio"][name="' + CSS.escape(el.name) + '"]')) : [el]);
      const radioOpts = radios.map((r, rIdx) => {
        r.setAttribute('data-cp-radio-idx', String(n) + '-' + String(rIdx));
        let txt = '';
        if (r.id) {
          const lbl = document.querySelector('label[for="' + CSS.escape(r.id) + '"]');
          if (lbl) txt = lbl.innerText.trim();
        }
        if (!txt && r.closest('label')) txt = r.closest('label').innerText.trim();
        return { value: String(rIdx), text: txt || r.value || '' };
      });
      const questionEl = fs ? fs.querySelector('legend, [data-test-form-element-label], .fb-dash-form-element__label') : null;
      const questionText = questionEl ? questionEl.innerText.trim() : labelOf(el);
      el.setAttribute('data-cp-idx', String(n));
      out.push({
        idx: n++, kind: 'input', type: 'radio',
        label: questionText, aria: el.getAttribute('aria-label') || '',
        placeholder: '', name: el.getAttribute('name') || el.id || '',
        autocomplete: '',
        required: el.required || el.getAttribute('aria-required') === 'true' || !!(fs && fs.querySelector('[aria-required="true"], .fb-dash-form-element__label-title--is-required')),
        has_value: radios.some(r => r.checked),
        options: radioOpts,
      });
      return;
    }

    const isCombobox = el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete') === 'list' || el.getAttribute('aria-autocomplete') === 'both';
    el.setAttribute('data-cp-idx', String(n));
    out.push({
      idx: n++, kind: el.tagName.toLowerCase(), type,
      is_combobox: isCombobox,
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
  const visible = (el) => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && (r.width > 0 || r.height > 0);
  };
  const fields = Array.from(document.querySelectorAll('input, textarea')).filter(visible);
  const pw = fields.some(e => (e.getAttribute('type') || '').toLowerCase() === 'password');
  const loginPath = /\\/(log-?in|sign-?in|sso|auth|checkpoint|uas\\/login)(\\/|$|\\?)/i.test(location.pathname);
  const text = document.body ? document.body.innerText : '';
  const loginText = /sign in|log in|welcome back/i.test(text);
  const hasIdentity = fields.some(e =>
    /email|user.?name|login|session_key/i.test([
      e.getAttribute('type'), e.getAttribute('name'), e.id,
      e.getAttribute('placeholder'), e.getAttribute('aria-label')
    ].join(' '))
  );
  const login = loginPath || pw || (loginText && hasIdentity);
  const captcha = q('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="turnstile"], .g-recaptcha, .h-captcha, .cf-turnstile, [data-sitekey]')
    || /verify (that )?you are (a )?human|i'm not a robot|captcha|cloudflare|checking your browser|unusual activity|account (is )?restricted|access denied/i.test(text);
  return {password: pw, login: login, captcha: captcha};
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

TRIGGER_APPLY_JS = """
(platform) => {
  const vis = (el) => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && !el.disabled && (r.width > 0 || r.height > 0);
  };
  let selectors = [];
  if (platform === 'linkedin') {
    if (document.querySelector('.jobs-easy-apply-modal, [data-test-modal-id="easy-apply-modal"]')) return 'modal_already_open';
    selectors = [
      'button.jobs-apply-button',
      'button[aria-label*="Easy Apply"]',
      '.jobs-apply-button--top-card button',
      'button[data-control-name*="jobdetails_topcard_inapply"]'
    ];
  } else if (platform === 'indeed') {
    if (document.querySelector('.ia-BasePage, [id*="ia-"]')) return 'modal_already_open';
    selectors = [
      '#indeedApplyButton',
      '[data-testid="indeedApplyButton"]',
      'button[id*="indeedApply"]',
      'a[id*="indeedApply"]',
      'button[aria-label*="Apply now"]'
    ];
  } else if (platform === 'naukri') {
    if (document.querySelector('.chatbot_DrawerContentWrapper')) return 'modal_already_open';
    selectors = [
      '#login-apply-button',
      '.apply-button',
      'button[id*="apply-button"]',
      '#company-site-button'
    ];
  } else if (platform === 'glassdoor') {
    selectors = [
      'button[data-test="applyButton"]',
      'a[data-test="applyButton"]',
      'button[aria-label*="Easy Apply"]',
      'button[aria-label*="Apply"]'
    ];
  }
  for (const sel of selectors) {
    const btn = Array.from(document.querySelectorAll(sel)).find(vis);
    if (btn) {
      btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
      return 'triggered';
    }
  }
  return 'not_found';
}
"""

STEP_BUTTON_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && !el.disabled && (r.width > 0 || r.height > 0);
  };
  const modalRoot = document.querySelector(
    '.jobs-easy-apply-modal, [data-test-modal-id="easy-apply-modal"], .chatbot_DrawerContentWrapper, .ia-BasePage'
  );
  const root = (modalRoot && visible(modalRoot)) ? modalRoot : document;
  const buttons = Array.from(root.querySelectorAll('button, input[type="button"], input[type="submit"], [role="button"]')).filter(visible);

  const submitRe = /\\b(submit application|submit|send application|finish|complete application|apply now)\\b/i;
  const reviewRe = /\\b(review your application|review application|review)\\b/i;
  const nextRe = /\\b(continue to next step|next|continue|save and continue|proceed)\\b/i;

  for (const b of buttons) {
    const label = ((b.innerText || b.value || '') + ' ' + (b.getAttribute('aria-label') || '')).trim();
    if (submitRe.test(label) && !/continue|next/i.test(label)) {
      return { action: 'submit_ready', label };
    }
  }
  for (const b of buttons) {
    const label = ((b.innerText || b.value || '') + ' ' + (b.getAttribute('aria-label') || '')).trim();
    if (reviewRe.test(label)) {
      b.setAttribute('data-cp-step-btn', 'true');
      return { action: 'advance', label };
    }
  }
  for (const b of buttons) {
    const label = ((b.innerText || b.value || '') + ' ' + (b.getAttribute('aria-label') || '')).trim();
    if (nextRe.test(label) && !submitRe.test(label)) {
      b.setAttribute('data-cp-step-btn', 'true');
      return { action: 'advance', label };
    }
  }
  return { action: 'none', label: '' };
}
"""

RADIO_SELECT_JS = """
(arg) => {
  const el = document.querySelector('[data-cp-radio-idx="' + arg.idx + '-' + arg.opt + '"]');
  if (!el) return false;
  if (el.id) {
    const lbl = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
    if (lbl) {
      lbl.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    }
  }
  if (!el.checked && el.closest('label')) {
    el.closest('label').dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
  }
  if (!el.checked) {
    el.checked = true;
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }
  return !!el.checked;
}
"""

COMBOBOX_PICK_JS = """
(val) => {
  const vis = (el) => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && (r.width > 0 || r.height > 0);
  };
  const opts = Array.from(document.querySelectorAll('[role="listbox"] [role="option"], .basic-typeahead__selectable, .artdeco-typeahead__result')).filter(vis);
  if (!opts.length) return false;
  const target = (val || '').trim().toLowerCase();
  const match = opts.find(o => (o.innerText || '').trim().toLowerCase().includes(target)) || opts[0];
  match.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
  return true;
}
"""

_LOGIN_URL_RE = re.compile(r"/(log-?in|sign-?in|sso|auth|checkpoint|uas/login)(/|$|\?)", re.I)
_LINKEDIN_GOOGLE_AUTH_HOST = "accounts.google.com"
_GOOGLE_AUTH_PATH_RE = re.compile(
    r"^/(?:gsi/|o/oauth2/|signin/|v\d+/signin/|ServiceLogin|AccountChooser)",
    re.I,
)


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


def _listing_platform(target: FillTarget | None) -> str | None:
    if target is None or not target.adapter.endswith("_listing"):
        return None
    return target.adapter.removesuffix("_listing")


def _automate_platform(target: FillTarget | None) -> str | None:
    if target is None or not target.adapter.endswith("_automate"):
        return None
    return target.adapter.removesuffix("_automate")


def _board_platform(target: FillTarget | None) -> str | None:
    if target is None:
        return None
    if target.adapter.endswith("_listing"):
        return target.adapter.removesuffix("_listing")
    if target.adapter.endswith("_automate"):
        return target.adapter.removesuffix("_automate")
    if target.adapter in ("linkedin", "indeed", "naukri", "glassdoor"):
        return target.adapter
    return None


def _is_safe_main_frame_navigation(url: str, target: FillTarget) -> bool:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    board_platform = _board_platform(target)
    if parts.username or parts.password or (is_blocked_host(host) and platform_for_host(host) != board_platform):
        return False
    if (
        board_platform == "linkedin"
        and host == _LINKEDIN_GOOGLE_AUTH_HOST
        and parts.scheme.lower() == "https"
        and port in (None, 443)
        and _GOOGLE_AUTH_PATH_RE.match(parts.path)
    ):
        return True
    if target.adapter == "local_test":
        target_parts = urlsplit(target.url)
        return (
            parts.scheme.lower() == "http"
            and host == (target_parts.hostname or "").lower().rstrip(".")
            and port == target_parts.port
            and parts.path == "/assistant-test-form"
        )
    if parts.scheme.lower() != "https" or port not in (None, 443):
        return False
    if board_platform:
        if platform_for_host(host) == board_platform:
            return True
        if _automate_platform(target) in ("indeed", "naukri", "glassdoor") and adapter_for_host(host) is not None:
            return True
        return False
    return adapter_for_host(host) == target.adapter


def _is_listing_platform_request_host(host: str, platform: str) -> bool:
    if platform_for_host(host) == platform:
        return True
    if platform == "linkedin" and (host == "licdn.com" or host.endswith(".licdn.com")):
        return True
    if platform == "indeed" and (host.endswith(".indeed.com") or host.endswith(".indeed.net")):
        return True
    if platform == "naukri" and (host.endswith(".naukri.com") or host.endswith(".naukrimg.com")):
        return True
    return False


def _route_guard(
    counter: dict,
    target: FillTarget | None = None,
    submission_authorized=None,
    manual_login_authorized=None,
    step_navigation_authorized=None,
):
    """Block writes except detected user sign-in, step progression, and explicitly authorized submits."""
    def handler(route):
        req = route.request
        parts = urlsplit(req.url)
        host = (parts.hostname or "").lower().rstrip(".")
        board_platform = _board_platform(target)
        is_linkedin_google_auth = (
            board_platform == "linkedin"
            and host == _LINKEDIN_GOOGLE_AUTH_HOST
        )
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
        if is_top_level_navigation and is_linkedin_google_auth and manual_login_authorized:
            manual_login_authorized.set()
        if req.method not in ("GET", "HEAD", "OPTIONS"):
            manual_login_write = bool(
                manual_login_authorized
                and manual_login_authorized.is_set()
                and target
                and target.adapter != "local_test"
                and (
                    adapter_for_host(host) == target.adapter
                    or (
                        board_platform is not None
                        and _is_listing_platform_request_host(host, board_platform)
                    )
                    or is_linkedin_google_auth
                )
                and parts.scheme.lower() == "https"
                and parts.port in (None, 443)
                and not parts.username
                and not parts.password
            )
            allowed_step_write = bool(
                step_navigation_authorized
                and step_navigation_authorized.is_set()
                and target
                and target.adapter != "local_test"
                and (
                    adapter_for_host(host) == target.adapter
                    or (
                        board_platform is not None
                        and _is_listing_platform_request_host(host, board_platform)
                    )
                )
                and parts.scheme.lower() == "https"
                and parts.port in (None, 443)
                and not parts.username
                and not parts.password
                and not re.search(r"/submit|/apply-submit|/complete", parts.path, re.I)
            )
            allowed_manual_write = bool(
                submission_authorized
                and submission_authorized.is_set()
                and target
                and target.adapter != "local_test"
                and _listing_platform(target) is None
                and (
                    host == target.host
                    or adapter_for_host(host) == target.adapter
                    or (
                        board_platform is not None
                        and _is_listing_platform_request_host(host, board_platform)
                    )
                )
                and parts.scheme.lower() == "https"
                and parts.port in (None, 443)
                and not parts.username
                and not parts.password
            )
            if manual_login_write or allowed_step_write or allowed_manual_write:
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
        elif parts.username or parts.password or (
            is_blocked_host(host)
            and not (
                board_platform
                and _is_listing_platform_request_host(host, board_platform)
            )
        ) or (
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


GLASSDOOR_FORM_JS = """
() => {
  const bodyText = document.body ? document.body.innerText : '';
  const blocked = /humans only|advanced security systems|security protections may mistakenly block|cloudflare ray id/i.test(bodyText);
  if (blocked) return {login: false, application: false, blocked: true};
  const visible = (el) => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && (r.width > 0 || r.height > 0);
  };
  const loginPath = /\\/(log-?in|sign-?in|sso|auth)(\\/|$|\\?)/i.test(location.pathname);
  const password = Array.from(document.querySelectorAll('input[type=password]')).some(visible);
  let login = loginPath || password;
  const forms = Array.from(document.querySelectorAll('form, [role="form"]')).filter(visible);
  let application = false;
  for (const form of forms) {
    const text = (form.innerText || '').toLowerCase();
    const fields = Array.from(form.querySelectorAll('input, textarea, select')).filter(visible);
    const fieldText = fields.map(el => [
      el.getAttribute('type'), el.getAttribute('name'), el.id,
      el.getAttribute('placeholder'), el.getAttribute('aria-label')
    ].join(' ')).join(' ').toLowerCase();
    if (/sign in|log in|create account/.test(text) && /email|password/.test(fieldText)) {
      login = true;
    }
    const hasContactField = fields.some(el =>
      /^(email|tel|file)$/i.test(el.getAttribute('type') || '') ||
      /first.?name|last.?name|full.?name|resume|phone|email/i.test(
        [el.getAttribute('name'), el.id, el.getAttribute('placeholder'), el.getAttribute('aria-label')].join(' ')
      )
    );
    if (!login && fields.length >= 2 && hasContactField &&
        /apply|application|resume|work experience|candidate/i.test(text)) {
      form.setAttribute('data-cp-glassdoor-application-form', 'true');
      application = true;
    } else {
      form.removeAttribute('data-cp-glassdoor-application-form');
    }
  }
  if (login) {
    application = false;
    forms.forEach(form => form.removeAttribute('data-cp-glassdoor-application-form'));
  }
  return {login, application, blocked};
}
"""


def _secure_profile_directory(path: Path) -> None:
    if os.name == "nt":
        executable = shutil.which("icacls")
        username = os.environ.get("USERNAME")
        domain = os.environ.get("USERDOMAIN")
        if not executable or not username:
            raise OSError("Could not establish a private browser profile directory.")
        account = f"{domain}\\{username}" if domain else username
        completed = subprocess.run(
            [
                executable,
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"{account}:(OI)(CI)F",
            ],
            capture_output=True,
            check=False,
            timeout=10,
        )
        if completed.returncode:
            raise OSError("Could not establish a private browser profile directory.")
        return
    path.chmod(0o700)


def _automation_profile_path(configured_path: str | None = None) -> str:
    """Resolve a private, dedicated CareerPilot profile outside the repository."""
    if configured_path:
        configured = Path(configured_path).expanduser()
        if configured.is_symlink() or bool(getattr(configured, "is_junction", lambda: False)()):
            raise ValueError("The automation browser profile cannot be a symbolic link.")
        profile = configured.resolve()
    else:
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / ".careerpilot"
        profile = (base / "ThoughtLabs CoPilot" / "AutomationChromeProfile").resolve()
    repository = Path(__file__).resolve().parents[2]
    try:
        profile.relative_to(repository)
    except ValueError:
        pass
    else:
        raise ValueError("The automation browser profile must be outside the repository.")
    app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")).expanduser()
    normal_chrome_profiles = (
        app_data / "Google" / "Chrome" / "User Data",
        app_data / "Microsoft" / "Edge" / "User Data",
    )
    for normal_profile in normal_chrome_profiles:
        try:
            profile.relative_to(normal_profile.resolve())
        except ValueError:
            continue
        raise ValueError("Use a separate CareerPilot profile, not a regular browser profile.")
    profile.mkdir(parents=True, exist_ok=True)
    if profile.is_symlink() or bool(getattr(profile, "is_junction", lambda: False)()):
        raise ValueError("The automation browser profile cannot be a symbolic link.")
    _secure_profile_directory(profile)
    return str(profile)


def _installed_chrome_executable() -> Path:
    """Find an installed Google Chrome binary without using Playwright Chromium."""
    candidates: list[Path] = []
    if os.name == "nt":
        for root in (
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        ):
            if root:
                candidates.append(
                    Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"
                )
        try:
            import winreg

            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(
                        hive,
                        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
                    ) as key:
                        value, _ = winreg.QueryValueEx(key, None)
                        candidates.append(Path(value))
                except OSError:
                    continue
        except ImportError:
            pass
        candidates.extend(
            Path(path)
            for path in (
                shutil.which("chrome.exe"),
                shutil.which("chrome"),
            )
            if path
        )
    else:
        candidates.extend(
            Path(path)
            for name in ("google-chrome-stable", "google-chrome", "chrome")
            if (path := shutil.which(name))
        )
        candidates.append(
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        )

    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if resolved.is_file():
            return resolved
    raise FileNotFoundError(
        "Google Chrome Stable was not found. Install Google Chrome to use browser automation."
    )


def _glassdoor_profile_path(configured_path: str | None = None) -> str:
    """Backward-compatible alias for the shared automation profile resolver."""
    return _automation_profile_path(configured_path)


class AutomationProfileLock:
    """Non-blocking thread and OS file lock for one persistent browser profile."""

    def __init__(self, profile_dir: str):
        profile = Path(profile_dir).resolve()
        self.lock_path = profile / ".careerpilot-profile.lock"
        self.key = os.path.normcase(str(profile))
        with _PROFILE_LOCKS_GUARD:
            self.thread_lock = _PROFILE_THREAD_LOCKS.setdefault(self.key, threading.Lock())
        self.handle = None
        self._file_locked = False
        self._thread_locked = False

    def acquire(self) -> bool:
        if not self.thread_lock.acquire(blocking=False):
            return False
        self._thread_locked = True
        try:
            if self.lock_path.is_symlink() or bool(
                getattr(self.lock_path, "is_junction", lambda: False)()
            ):
                raise OSError("The browser profile lock path is not a regular file.")
            self.handle = self.lock_path.open("a+b")
            self.handle.seek(0, os.SEEK_END)
            if self.handle.tell() == 0:
                self.handle.write(b"\0")
                self.handle.flush()
            self.handle.seek(0)
        except Exception:
            self._release()
            raise

        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._file_locked = True
            if os.name != "nt":
                os.chmod(self.lock_path, 0o600)
            return True
        except (BlockingIOError, OSError) as exc:
            self._release()
            if (
                isinstance(exc, BlockingIOError)
                or getattr(exc, "errno", None) in (errno.EAGAIN, errno.EACCES, errno.EDEADLK)
                or getattr(exc, "winerror", None) in (33, 36)
            ):
                return False
            raise
        except Exception:
            self._release()
            raise

    def _release(self) -> None:
        try:
            if self.handle is not None:
                try:
                    if self._file_locked:
                        if os.name == "nt":
                            import msvcrt

                            self.handle.seek(0)
                            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl

                            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
                finally:
                    self.handle.close()
                    self.handle = None
                    self._file_locked = False
        except OSError as exc:
            logger.warning("Browser profile lock cleanup failed category=%s", type(exc).__name__)
        finally:
            if self._thread_locked:
                self.thread_lock.release()
                self._thread_locked = False

    def release(self) -> None:
        self._release()


class BrowserAttachmentUnavailable(RuntimeError):
    """No explicitly configured local Chrome DevTools endpoint is available."""


class BrowserProfileMismatch(BrowserAttachmentUnavailable):
    """The connected Chrome does not expose the dedicated CareerPilot profile."""


def _validate_cdp_endpoint(endpoint: str | None) -> str:
    value = (endpoint or "").strip()
    if not value:
        raise BrowserAttachmentUnavailable(
            "No CareerPilot Chrome debugging endpoint is configured."
        )
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise BrowserAttachmentUnavailable(
            "The CareerPilot Chrome debugging endpoint is invalid."
        ) from None
    if (
        parts.scheme.lower() != "http"
        or (parts.hostname or "").lower().rstrip(".") not in _CDP_LOOPBACK_HOSTS
        or port is None
        or parts.username
        or parts.password
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise BrowserAttachmentUnavailable(
            "The Chrome debugging endpoint must be a local HTTP endpoint such as http://127.0.0.1:9222."
        )
    return f"http://{(parts.hostname or '').lower().rstrip('.')}:{port}"


def _verify_dedicated_chrome(
    context, page, expected_profile: str, *, verify_command_line: bool = True
) -> None:
    """Verify Chrome identity and, for external sessions, its dedicated profile."""
    session = context.new_cdp_session(page)
    try:
        try:
            product = session.send("Browser.getVersion").get("product", "")
            arguments = (
                session.send("Browser.getBrowserCommandLine").get("arguments", [])
                if verify_command_line
                else []
            )
        except Exception as exc:
            raise BrowserAttachmentUnavailable(
                "Could not verify the connected Google Chrome session."
            ) from exc
    finally:
        try:
            session.detach()
        except Exception as exc:
            logger.warning("Chrome CDP inspection cleanup failed category=%s", type(exc).__name__)

    if not isinstance(product, str) or not product.startswith("Chrome/"):
        raise BrowserProfileMismatch(
            "The local debugging endpoint is not connected to Google Chrome."
        )
    if not verify_command_line:
        return

    user_data_dir = None
    for index, argument in enumerate(arguments if isinstance(arguments, list) else []):
        if not isinstance(argument, str):
            continue
        if argument.startswith("--user-data-dir="):
            user_data_dir = argument.partition("=")[2].strip('"')
            break
        if (
            argument == "--user-data-dir"
            and index + 1 < len(arguments)
            and isinstance(arguments[index + 1], str)
        ):
            user_data_dir = arguments[index + 1].strip('"')
            break
    if not user_data_dir:
        raise BrowserProfileMismatch(
            "Chrome did not identify a dedicated user-data directory."
        )
    try:
        actual_profile = os.path.normcase(
            str(Path(os.path.expandvars(user_data_dir)).expanduser().resolve())
        )
        required_profile = os.path.normcase(str(Path(expected_profile).resolve()))
    except (OSError, RuntimeError, ValueError):
        raise BrowserProfileMismatch(
            "Chrome's user-data directory could not be verified."
        ) from None
    if actual_profile != required_profile:
        raise BrowserProfileMismatch(
            "The connected Chrome is not using CareerPilot's dedicated profile."
        )


def _launch_automation_context(chromium, profile_dir: str, launch: dict):
    """Launch headed, branded Google Chrome with the dedicated persistent profile."""
    _installed_chrome_executable()
    options = {
        key: value
        for key, value in launch.items()
        if key not in {"channel", "executable_path", "headless"}
    }
    options.update(headless=False, channel="chrome")
    return chromium.launch_persistent_context(user_data_dir=profile_dir, **options)


def _start_external_chrome(profile_dir: str) -> tuple[subprocess.Popen, str]:
    """Start ordinary Google Chrome with a dedicated profile and loopback CDP."""
    executable = _installed_chrome_executable()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    endpoint = f"http://127.0.0.1:{port}"
    command = [
        str(executable),
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ]
    creation_flags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    )
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=os.name != "nt",
        creationflags=creation_flags,
    )
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + 25
    last_error = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                "Google Chrome exited before its local debugging endpoint became ready."
            )
        try:
            with opener.open(f"{endpoint}/json/version", timeout=1) as response:
                details = json.loads(response.read(16384).decode("utf-8"))
            websocket_url = urlsplit(details.get("webSocketDebuggerUrl", ""))
            if (
                websocket_url.scheme == "ws"
                and websocket_url.hostname == "127.0.0.1"
                and websocket_url.port == port
            ):
                return process, endpoint
            last_error = RuntimeError(
                "Google Chrome returned an unexpected local debugging endpoint."
            )
        except Exception as exc:  # noqa: BLE001 - retry until Chrome's CDP endpoint is ready
            last_error = exc
        time.sleep(0.25)

    try:
        process.terminate()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Chrome startup cleanup failed category=%s", type(exc).__name__)
    raise RuntimeError(
        "Google Chrome started, but its local debugging endpoint did not become ready."
    ) from last_error


def _get_or_start_external_chrome(profile_dir: str) -> tuple[subprocess.Popen, str]:
    """Reuse the CareerPilot Chrome process for this profile while it remains open."""
    profile_key = os.path.normcase(str(Path(profile_dir).resolve()))
    with _EXTERNAL_CHROME_LOCK:
        existing = _EXTERNAL_CHROME_PROCESSES.get(profile_key)
        if existing and existing[0].poll() is None:
            return existing
        _EXTERNAL_CHROME_PROCESSES.pop(profile_key, None)
        launched = _start_external_chrome(profile_dir)
        _EXTERNAL_CHROME_PROCESSES[profile_key] = launched
        return launched


def _launch_glassdoor_context(chromium, profile_dir: str, launch: dict):
    """Backward-compatible alias for the shared persistent-context launcher."""
    return _launch_automation_context(chromium, profile_dir, launch)


def _fill_frame(
    frame,
    values: dict[str, dict],
    resume_file: tuple[bytes, str] | None = None,
    glassdoor: bool = False,
) -> list[dict]:
    fields: list[dict] = []
    for desc in frame.evaluate(COLLECT_JS, {"glassdoor": glassdoor}):
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
        if desc.get("type") == "file":
            result.pop("value", None)
            fields.append(result)
            continue
        if result["state"] == FILLED:
            loc = frame.locator(f'[data-cp-idx="{desc["idx"]}"]')
            try:
                if desc["type"] == "radio":
                    ok = bool(
                        frame.evaluate(
                            RADIO_SELECT_JS,
                            {"idx": desc["idx"], "opt": result["value"]},
                        )
                    )
                    valid = True
                elif desc["kind"] == "select":
                    loc.select_option(result["value"], timeout=4000)
                    ok = loc.input_value(timeout=2000) == result["value"]
                    valid = loc.evaluate("el => el.checkValidity()") if ok else True
                else:
                    loc.fill(result["value"], timeout=4000)
                    if desc.get("is_combobox"):
                        try:
                            frame.wait_for_timeout(350)
                            frame.evaluate(COMBOBOX_PICK_JS, result["value"])
                        except Exception:
                            pass
                    ok = bool(loc.input_value(timeout=2000).strip())
                    valid = loc.evaluate("el => el.checkValidity()") if ok else True
                if not ok:
                    result.update(state=FAILED, reason="The page did not keep the value.")
                elif not valid:
                    result.update(state=FAILED, reason="The page reports this value as invalid.")
                else:
                    result.update(
                        state=FILLED,
                        reason="Filled from a confirmed Application Kit value.",
                    )
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
    browser_cdp_endpoint: str | None = None,
    automation_profile_dir: str | None = None,
    glassdoor_profile_dir: str | None = None,
    resume_file: tuple[bytes, str] | None = None,
    submission_authorized=None,
    artifact_dir: str | None = None,
) -> dict:
    """Open the page, fill matched fields across steps, and report. Blocking: run it in a thread."""
    started = time.time()
    persistent_profile = target.adapter != "local_test"
    persistent_glassdoor = target.adapter == "glassdoor"
    listing_platform = _listing_platform(target)
    automate_platform = _automate_platform(target)
    attach_existing_browser = (
        listing_platform is not None
        or bool((browser_cdp_endpoint or "").strip())
        or automate_platform is not None
    )
    counter = {
        "blocked_writes": 0,
        "blocked_navigations": 0,
        "blocked_top_level_navigations": 0,
    }
    manual_login_authorized = threading.Event()
    step_navigation_authorized = threading.Event()
    keep_open_deadline = time.time() + max(0, keep_open_seconds)
    result = {"status": RUN_FAILED, "pause_reason": "", "fields": [], "host": target.host}
    profile_lock: AutomationProfileLock | None = None
    profile_lock_acquired = False
    browser = None
    launched_chrome_process = None
    internally_launched_chrome = False
    context = None
    page = None
    attached_browser = False
    tracing_started = False
    screenshot_finalized = False
    glassdoor_form = False
    safe_to_capture = False
    phase = "profile"

    _trace_path: str | None = None
    _screenshot_path: str | None = None
    if artifact_dir:
        _trace_path = str(Path(artifact_dir) / "trace.zip")
        _screenshot_path = str(Path(artifact_dir) / "screenshot.png")

    def finish_diagnostics() -> None:
        nonlocal tracing_started, screenshot_finalized
        if (
            _screenshot_path
            and page is not None
            and not screenshot_finalized
            and safe_to_capture
        ):
            screenshot_finalized = True
            try:
                if not page.is_closed():
                    page.screenshot(path=_screenshot_path, full_page=False, timeout=5000)
                    if Path(_screenshot_path).is_file():
                        if os.name != "nt":
                            os.chmod(_screenshot_path, 0o600)
                        result["screenshot_path"] = _screenshot_path
            except Exception as exc:  # noqa: BLE001
                logger.warning("Screenshot capture failed category=%s", type(exc).__name__)
        if tracing_started:
            tracing_started = False
            try:
                context.tracing.stop(path=_trace_path)
                if _trace_path and Path(_trace_path).is_file():
                    if os.name != "nt":
                        os.chmod(_trace_path, 0o600)
                    result["trace_path"] = _trace_path
            except Exception as exc:  # noqa: BLE001
                logger.warning("Playwright trace finalization failed category=%s", type(exc).__name__)

    def start_tracing() -> None:
        nonlocal tracing_started
        if not _trace_path or tracing_started:
            return
        try:
            context.tracing.start(screenshots=True, snapshots=True)
            tracing_started = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Playwright tracing could not start category=%s", type(exc).__name__)

    def close_browser_resources() -> None:
        finish_diagnostics()
        if attached_browser:
            if browser is not None:
                try:
                    browser.close()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Chrome CDP disconnect failed category=%s", type(exc).__name__)
            return
        if context is not None:
            try:
                context.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Browser context cleanup failed category=%s", type(exc).__name__)
        if browser is not None:
            try:
                browser.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Browser cleanup failed category=%s", type(exc).__name__)

    try:
        from playwright.sync_api import sync_playwright

        if target.adapter != "local_test":
            check_public_host(target.host)
        if persistent_profile:
            profile_path = _automation_profile_path(
                automation_profile_dir or glassdoor_profile_dir
            )
            profile_lock = AutomationProfileLock(profile_path)
            if not profile_lock.acquire():
                result.update(
                    status=NEEDS_YOU,
                    pause_reason="automation_profile_in_use",
                    error_category="automation_profile_in_use",
                )
                if on_result:
                    on_result(result)
                return result
            profile_lock_acquired = True
        with ExitStack() as cleanup_stack:
            pw = cleanup_stack.enter_context(sync_playwright())
            cleanup_stack.callback(close_browser_resources)
            launch = {"headless": headless}
            if attach_existing_browser:
                if (
                    automate_platform is not None
                    and listing_platform is None
                    and not (browser_cdp_endpoint or "").strip()
                ):
                    phase = "launch"
                    launched_chrome_process, endpoint = _get_or_start_external_chrome(
                        profile_path
                    )
                    internally_launched_chrome = True
                else:
                    endpoint = _validate_cdp_endpoint(browser_cdp_endpoint)
                phase = "connect"
                try:
                    browser = pw.chromium.connect_over_cdp(
                        endpoint,
                        timeout=15000 if internally_launched_chrome else 5000,
                    )
                except Exception as exc:
                    raise BrowserAttachmentUnavailable(
                        "Could not connect Playwright to the dedicated CareerPilot Chrome session."
                    ) from exc
                attached_browser = True
                contexts = browser.contexts
                if not contexts:
                    raise BrowserAttachmentUnavailable(
                        "The connected Chrome session has no available browser context."
                    )
                context = contexts[0]
                verification_pages = context.pages
                if not verification_pages:
                    raise BrowserAttachmentUnavailable(
                        "The connected Chrome session has no page available for verification."
                    )
                _verify_dedicated_chrome(
                    context,
                    verification_pages[0],
                    profile_path,
                    verify_command_line=not internally_launched_chrome,
                )
                page = context.new_page()
            elif persistent_profile:
                phase = "launch"
                context = _launch_automation_context(
                    pw.chromium,
                    profile_path,
                    launch,
                )
            else:
                browser = pw.chromium.launch(**launch)
                context = browser.new_context()
            phase = "navigation"

            route_guard = _route_guard(
                counter,
                target,
                submission_authorized,
                manual_login_authorized,
                step_navigation_authorized,
            )
            if attached_browser:
                page.route("**/*", route_guard)
            else:
                context.route("**/*", route_guard)
                page = context.pages[0] if persistent_profile and context.pages else context.new_page()
            if target.adapter == "local_test":
                safe_to_capture = True
                start_tracing()
            try:
                page.goto(target.url, wait_until="domcontentloaded", timeout=30000)
                try:
                    page.wait_for_load_state("networkidle", timeout=6000)
                except Exception:  # noqa: BLE001
                    pass
                if counter["blocked_top_level_navigations"]:
                    raise RuntimeError("Unsupported top-level redirect blocked.")

                flags_raw = page.evaluate(FLAGS_JS)
                flags = {
                    "captcha": bool(flags_raw.get("captcha")),
                    "login": (
                        bool(flags_raw.get("login"))
                        or bool(flags_raw.get("password"))
                        or bool(_LOGIN_URL_RE.search(urlsplit(page.url).path))
                    ),
                }
                if flags["login"] and not flags["captcha"]:
                    manual_login_authorized.set()

                if automate_platform:
                    # 1. Pre-apply manual login pause & auto-resume
                    if flags["login"] and not headless and keep_open_seconds > 0:
                        manual_login_authorized.set()
                        if on_result:
                            on_result({
                                **result,
                                "status": "running",
                                "pause_reason": "login_required",
                                "browser_open": True,
                            })
                        while time.time() < keep_open_deadline and not page.is_closed():
                            try:
                                pf = page.evaluate(FLAGS_JS)
                                if (
                                    not pf.get("login")
                                    and not pf.get("password")
                                    and not _LOGIN_URL_RE.search(urlsplit(page.url).path)
                                ):
                                    flags["login"] = False
                                    manual_login_authorized.clear()
                                    break
                                page.wait_for_timeout(1000)
                            except Exception:
                                break

                    # 2. Click platform Apply / Easy Apply button and handle post-click login wall or new tab
                    if not flags["login"] and not flags["captcha"]:
                        step_navigation_authorized.set()
                        pages_before = len(context.pages)
                        try:
                            page.evaluate(TRIGGER_APPLY_JS, automate_platform)
                            page.wait_for_timeout(1200)
                        except Exception:
                            pass
                        open_pages = [p for p in context.pages if not p.is_closed()]
                        if len(open_pages) > pages_before:
                            page = open_pages[-1]
                        flags_raw = page.evaluate(FLAGS_JS)
                        flags = {
                            "captcha": bool(flags_raw.get("captcha")),
                            "login": (
                                bool(flags_raw.get("login"))
                                or bool(flags_raw.get("password"))
                                or bool(_LOGIN_URL_RE.search(urlsplit(page.url).path))
                            ),
                        }
                        if flags["login"] and not headless and keep_open_seconds > 0:
                            manual_login_authorized.set()
                            if on_result:
                                on_result({
                                    **result,
                                    "status": "running",
                                    "pause_reason": "login_required",
                                    "browser_open": True,
                                })
                            while time.time() < keep_open_deadline and not page.is_closed():
                                try:
                                    pf = page.evaluate(FLAGS_JS)
                                    if (
                                        not pf.get("login")
                                        and not pf.get("password")
                                        and not _LOGIN_URL_RE.search(urlsplit(page.url).path)
                                    ):
                                        flags["login"] = False
                                        manual_login_authorized.clear()
                                        page.evaluate(TRIGGER_APPLY_JS, automate_platform)
                                        page.wait_for_timeout(1200)
                                        break
                                    page.wait_for_timeout(1000)
                                except Exception:
                                    break

                if listing_platform:
                    if flags["captcha"]:
                        pause_reason = "access_challenge"
                    elif flags["login"]:
                        pause_reason = "login_required"
                    else:
                        pause_reason = "listing_opened"

                    safe_to_capture = pause_reason in ("listing_opened", "access_challenge")
                    if safe_to_capture:
                        start_tracing()
                    result.update(
                        status=NEEDS_YOU,
                        pause_reason=pause_reason,
                        error_category=(
                            "access_challenge" if pause_reason == "access_challenge"
                            else "login_required" if pause_reason == "login_required"
                            else ""
                        ),
                        fields=[],
                        blocked_writes=counter["blocked_writes"],
                        duration_ms=int((time.time() - started) * 1000),
                    )
                    finish_diagnostics()

                    browser_open = not headless and keep_open_seconds > 0
                    result["browser_open"] = browser_open
                    if browser_open and on_result:
                        on_result({**result, "status": "running"})

                    if attached_browser:
                        result.update(
                            status=NEEDS_YOU,
                            pause_reason=pause_reason,
                            browser_open=True,
                            blocked_writes=counter["blocked_writes"],
                        )
                        if on_result:
                            on_result(result)
                        return result

                    while browser_open and time.time() < keep_open_deadline:
                        open_pages = [candidate for candidate in context.pages if not candidate.is_closed()]
                        if not open_pages:
                            break
                        page = open_pages[-1]
                        try:
                            page_flags = page.evaluate(FLAGS_JS)
                            if page_flags.get("captcha"):
                                next_pause = "access_challenge"
                                manual_login_authorized.clear()
                            elif (
                                page_flags.get("login")
                                or page_flags.get("password")
                                or _LOGIN_URL_RE.search(urlsplit(page.url).path)
                            ):
                                next_pause = "login_required"
                                manual_login_authorized.set()
                            else:
                                next_pause = "listing_opened"
                                manual_login_authorized.clear()
                            if next_pause != pause_reason:
                                pause_reason = next_pause
                                result.update(
                                    pause_reason=pause_reason,
                                    error_category=(
                                        "access_challenge" if pause_reason == "access_challenge"
                                        else "login_required" if pause_reason == "login_required"
                                        else ""
                                    ),
                                )
                                if on_result:
                                    on_result({**result, "status": "running", "browser_open": True})
                            page.wait_for_timeout(1000)
                        except Exception:  # noqa: BLE001
                            break

                    result.update(
                        status=NEEDS_YOU,
                        pause_reason=pause_reason,
                        browser_open=False,
                        blocked_writes=counter["blocked_writes"],
                        duration_ms=int((time.time() - started) * 1000),
                    )
                    if on_result:
                        on_result(result)
                    return result

                fields: list[dict] = []
                glassdoor_site_blocked = False
                if persistent_glassdoor and not headless and keep_open_seconds > 0:
                    while time.time() < keep_open_deadline:
                        glassdoor_form = False
                        login_detected = False
                        for candidate in context.pages:
                            if candidate.is_closed():
                                continue
                            for frame in candidate.frames:
                                if adapter_for_host(urlsplit(frame.url).hostname or "") != "glassdoor":
                                    continue
                                try:
                                    page_flags = frame.evaluate(GLASSDOOR_FORM_JS)
                                    login_detected = login_detected or bool(page_flags["login"])
                                    glassdoor_site_blocked = (
                                        glassdoor_site_blocked or bool(page_flags["blocked"])
                                    )
                                    if page_flags["application"]:
                                        page = candidate
                                        glassdoor_form = True
                                except Exception:  # noqa: BLE001
                                    continue
                        if glassdoor_site_blocked:
                            manual_login_authorized.clear()
                            result.update(
                                status=NEEDS_YOU,
                                pause_reason="glassdoor_site_blocked",
                                error_category="access_challenge",
                                fields=[],
                            )
                            break
                        if glassdoor_form:
                            manual_login_authorized.clear()
                            flags = {"login": False, "captcha": False}
                            flags_raw = page.evaluate(FLAGS_JS)
                            flags["captcha"] = bool(flags_raw.get("captcha"))
                            safe_to_capture = not flags["captcha"]
                            break
                        if counter["blocked_top_level_navigations"]:
                            raise RuntimeError("Unsupported top-level redirect blocked.")
                        if login_detected:
                            manual_login_authorized.set()
                        else:
                            manual_login_authorized.clear()
                        page.wait_for_timeout(500)
                    if not glassdoor_form and not glassdoor_site_blocked:
                        manual_login_authorized.clear()
                        result.update(
                            status=NEEDS_YOU,
                            pause_reason="glassdoor_wait_timeout",
                            fields=[],
                        )
                elif persistent_glassdoor:
                    result.update(
                        status=NEEDS_YOU,
                        pause_reason="glassdoor_interactive_required",
                        fields=[],
                    )

                if not persistent_glassdoor and not flags["login"]:
                    visible_fields = any(
                        frame.evaluate(COLLECT_JS) for frame in page.frames
                    )
                    if not visible_fields and target.adapter in ("greenhouse", "lever"):
                        application_link = page.evaluate(APPLY_LINK_JS)
                        if application_link and _same_origin_application_link(application_link, target):
                            page.goto(application_link, wait_until="domcontentloaded", timeout=30000)
                            try:
                                page.wait_for_load_state("networkidle", timeout=6000)
                            except Exception:  # noqa: BLE001
                                pass
                            if counter["blocked_top_level_navigations"]:
                                raise RuntimeError("Unsupported top-level redirect blocked.")
                            flags_raw = page.evaluate(FLAGS_JS)
                            flags = {
                                "captcha": bool(flags_raw.get("captcha")),
                                "login": (
                                    bool(flags_raw.get("login"))
                                    or bool(flags_raw.get("password"))
                                    or bool(_LOGIN_URL_RE.search(urlsplit(page.url).path))
                                ),
                            }
                    if not persistent_glassdoor and not flags["login"] and not flags["captcha"]:
                        safe_to_capture = True
                        start_tracing()
                    if not flags["login"]:
                        accumulated_fields: list[dict] = []
                        seen_labels: set[str] = set()
                        for _step_idx in range(8):
                            per_frame = [
                                _fill_frame(fr, values, resume_file) for fr in page.frames
                            ]
                            step_fields = max(per_frame, key=len, default=[])
                            for sf in step_fields:
                                key_id = f"{sf.get('label')}::{sf.get('key')}"
                                if key_id not in seen_labels:
                                    seen_labels.add(key_id)
                                    accumulated_fields.append(sf)
                            if any(
                                sf["state"] in (NEEDS_INPUT, FAILED) and sf.get("required")
                                for sf in step_fields
                            ):
                                break
                            try:
                                step_info = page.evaluate(STEP_BUTTON_JS)
                            except Exception:
                                break
                            if not isinstance(step_info, dict) or step_info.get("action") != "advance":
                                break
                            try:
                                step_navigation_authorized.set()
                                page.evaluate(
                                    """() => {
                                      const b = document.querySelector('[data-cp-step-btn="true"]');
                                      if (b) {
                                        b.removeAttribute('data-cp-step-btn');
                                        b.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                                      }
                                    }"""
                                )
                                page.wait_for_timeout(900)
                            except Exception:
                                break
                        fields = accumulated_fields
                elif persistent_glassdoor and glassdoor_form and not flags["captcha"]:
                    safe_to_capture = True
                    start_tracing()
                    per_frame = []
                    for frame in page.frames:
                        try:
                            if frame.evaluate(
                                "!!document.querySelector('[data-cp-glassdoor-application-form=\"true\"]')"
                            ):
                                per_frame.append(
                                    _fill_frame(frame, values, resume_file, glassdoor=True)
                                )
                        except Exception:  # noqa: BLE001
                            continue
                    fields = max(per_frame, key=len, default=[])

                if persistent_glassdoor and not glassdoor_form:
                    pass
                else:
                    status, pause = summarise(fields, flags)
                    result.update(status=status, pause_reason=pause, fields=fields)
                    if pause == "unsupported_form":
                        result["error_category"] = "unsupported_form"
                    elif pause == "captcha":
                        result["error_category"] = "captcha_detected"
                    elif pause == "login_required":
                        result["error_category"] = "login_required"
            except Exception as exc:  # noqa: BLE001
                logger.warning("Assistance run failed category=%s", type(exc).__name__)
                if counter["blocked_top_level_navigations"]:
                    pause, category = "unsafe_redirect", "unsupported_redirect"
                elif type(exc).__name__ in ("TimeoutError", "TimeoutErrorImpl"):
                    pause, category = "navigation_timeout", "navigation_timeout"
                else:
                    pause, category = "navigation_failed", "navigation_failed"
                result.update(status=RUN_FAILED, pause_reason=pause, error_category=category)

            finish_diagnostics()

            result["blocked_writes"] = counter["blocked_writes"]
            result["duration_ms"] = int((time.time() - started) * 1000)
            if not headless and keep_open_seconds > 0 and result["status"] != RUN_FAILED:
                result["browser_open"] = True
                if on_result:
                    on_result(result)
                while time.time() < keep_open_deadline and not page.is_closed():
                    try:
                        if persistent_profile and not persistent_glassdoor:
                            login_flags = page.evaluate(FLAGS_JS)
                            if login_flags.get("captcha"):
                                manual_login_authorized.clear()
                                result.update(
                                    status=NEEDS_YOU,
                                    pause_reason="captcha",
                                    error_category="captcha_detected",
                                )
                            elif (
                                login_flags.get("login")
                                or login_flags.get("password")
                                or _LOGIN_URL_RE.search(urlsplit(page.url).path)
                            ):
                                manual_login_authorized.set()
                                result.update(
                                    status=NEEDS_YOU,
                                    pause_reason="login_required",
                                    error_category="login_required",
                                )
                            else:
                                manual_login_authorized.clear()
                        page.wait_for_timeout(1000)
                    except Exception:  # noqa: BLE001
                        break
                result["blocked_writes"] = counter["blocked_writes"]
                result["browser_open"] = False
            elif on_result:
                on_result(result)
    except UrlRejected as exc:
        finish_diagnostics()
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
        finish_diagnostics()
        if isinstance(exc, BrowserProfileMismatch):
            category = "browser_profile_mismatch"
        elif isinstance(exc, BrowserAttachmentUnavailable):
            category = "browser_attachment_unavailable"
        elif isinstance(exc, ImportError):
            category = "playwright_unavailable"
        elif persistent_profile and not profile_lock_acquired:
            category = "automation_profile_unavailable"
        elif persistent_profile and phase == "launch":
            category = (
                "chrome_unavailable"
                if isinstance(exc, FileNotFoundError)
                else "chrome_launch_failed"
            )
        elif counter["blocked_top_level_navigations"]:
            category = "unsafe_redirect"
        elif "timeout" in type(exc).__name__.casefold() or "timeout" in str(exc).casefold():
            category = "navigation_timeout"
        else:
            network_error = re.search(r"net::(ERR_[A-Z0-9_]+)", str(exc))
            category = (
                f"network_{network_error.group(1).lower()}"
                if network_error
                else "navigation_failed"
            )
        pause_reason = (
            "browser_profile_mismatch"
            if category == "browser_profile_mismatch"
            else "browser_attachment_unavailable"
            if category == "browser_attachment_unavailable"
            else "automation_profile_unavailable"
            if category == "automation_profile_unavailable"
            else "chrome_unavailable"
            if category == "chrome_unavailable"
            else "chrome_launch_failed"
            if category == "chrome_launch_failed"
            else "unsafe_redirect"
            if category == "unsafe_redirect"
            else "navigation_timeout"
            if category == "navigation_timeout"
            else "navigation_failed"
            if category.startswith("network_") or category == "navigation_failed"
            else "browser_unavailable"
        )
        logger.warning("Browser automation failed category=%s", type(exc).__name__)
        result.update(
            status=RUN_FAILED,
            pause_reason=pause_reason,
            error_category=category,
            fields=[],
        )
        if on_result:
            on_result(result)
    finally:
        if profile_lock is not None:
            profile_lock.release()
    return result