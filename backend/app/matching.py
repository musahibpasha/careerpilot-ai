import re

# Common spellings that should count as one skill.
SKILL_ALIASES = {
    "js": "javascript",
    "ecmascript": "javascript",
    "ts": "typescript",
    "py": "python",
    "golang": "go",
    "postgres": "postgresql",
    "mongo": "mongodb",
    "k8s": "kubernetes",
    "html5": "html",
    "css3": "css",
    "reactjs": "react",
    "nodejs": "node",
    "vuejs": "vue",
    "nextjs": "next",
    "expressjs": "express",
    "angularjs": "angular",
    "c sharp": "c#",
    "tailwindcss": "tailwind",
}


def normalize_skill(skill: str) -> str:
    """Return one canonical lowercase form, so 'React', 'React.js' and
    'ReactJS' all become 'react'."""
    s = re.sub(r"\s+", " ", skill.strip().lower())
    s = s.strip(" ,;:()[]")
    if s.endswith(".") and len(s) > 1:
        s = s.rstrip(".")
    # 'react.js' / 'react js' -> 'react'
    s = re.sub(r"(?<=[a-z0-9])(\.| )js$", "", s)
    return SKILL_ALIASES.get(s, s)


def normalize_skills(skills: list[str]) -> set[str]:
    return {n for n in (normalize_skill(s) for s in skills) if n}


def tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[a-zA-Z0-9+#.]+", text)
        if len(token) > 1
    }


def calculate_match(profile_skills: list[str], job_skills: list[str], description: str = "") -> tuple[float, list[str]]:
    """Transparent starter score: matched listed skills / distinct listed job skills.
    Skill spellings are normalised first so React, React.js and ReactJS are one skill.
    Replace/extend this with an explainable weighted matcher in Stage 3.
    """
    expected = normalize_skills(job_skills)
    if not expected:
        expected = normalize_skills(list(tokens(description)))
    if not expected:
        return 0.0, ["No skills or description keywords provided yet"]
    owned = normalize_skills(profile_skills)
    matched = sorted(expected & owned)
    score = round(100 * len(matched) / len(expected), 1)
    reasons = [f"Matched skill: {skill}" for skill in matched[:8]]
    missing = sorted(expected - owned)
    reasons.extend(f"Potential gap: {skill}" for skill in missing[:5])
    return score, reasons


# ---------------------------------------------------------------------------
# Stage 3: explainable weighted matching
# ---------------------------------------------------------------------------
# Every factor returns a 0-1 score plus a plain-English reason, or None when it
# cannot be judged (the job or your preferences do not say enough). Factors that
# cannot be judged are left out and the remaining weights are rescaled, so a
# missing detail never counts as a bad match. "Confidence" tells you how much of
# the full picture was actually judged.

WEIGHTS = {"skills": 50, "role": 15, "location": 15, "experience": 10, "salary": 10}
LABELS = {
    "skills": "Skills",
    "role": "Role fit",
    "location": "Location & work style",
    "experience": "Experience level",
    "salary": "Salary",
}
LEVELS = ("intern", "entry", "mid", "senior", "lead")
NICE_SHARE = 0.2  # share of the skills score that comes from nice-to-have skills
MIN_DESCRIPTION_HITS = 4  # description-only skill hits needed for a full skills score
# Too short or too common to search for safely inside free text.
AMBIGUOUS_SKILLS = {"go", "r", "c", "d", "e", "swift"}

_REVERSE_ALIASES: dict[str, set[str]] = {}
for _alias, _canon in SKILL_ALIASES.items():
    _REVERSE_ALIASES.setdefault(_canon, set()).add(_alias)

ROLE_STOPWORDS = {"and", "of", "the", "a", "an", "for", "at", "in", "to", "i", "ii", "iii"}
ROLE_LEVEL_WORDS = {
    "senior", "sr", "junior", "jr", "lead", "principal", "staff", "intern",
    "internship", "trainee", "associate", "graduate", "entry", "level", "fresher",
}
ROLE_PHRASES = (
    ("front end", "frontend"), ("back end", "backend"), ("full stack", "fullstack"),
    ("ui/ux", "uiux"), ("machine learning", "ml"), ("data science", "datascience"),
)
ROLE_SAME = {"developer": "engineer", "programmer": "engineer", "dev": "engineer", "swe": "engineer"}

LEVEL_HINTS = (
    ("intern", ("intern", "internship", "trainee")),
    ("entry", ("junior", "jr", "fresher", "graduate", "entry")),
    ("lead", ("lead", "principal", "head", "architect")),
    ("senior", ("senior", "sr", "staff")),
)

LOCATION_ALIASES = {
    "bengaluru": "bangalore", "bombay": "mumbai", "gurugram": "gurgaon",
    "madras": "chennai", "calcutta": "kolkata", "new delhi": "delhi",
}


def _status(score: float) -> str:
    return "good" if score >= 0.75 else "partial" if score >= 0.4 else "poor"


def _pct(score: float) -> float:
    return round(100 * score, 1)


def _mentioned(skill: str, text: str) -> bool:
    """True when the skill (or a known spelling of it) appears as a whole word."""
    if skill in AMBIGUOUS_SKILLS or len(skill) < 2:
        return False
    variants = {skill, f"{skill}.js", f"{skill}js", f"{skill} js"} | _REVERSE_ALIASES.get(skill, set())
    return any(
        re.search(rf"(?<![a-z0-9+#]){re.escape(v)}(?![a-z0-9+#])", text) for v in variants
    )


def _skills_factor(job: dict, profile: dict) -> dict | None:
    must = normalize_skills(job.get("skills", []))
    nice = normalize_skills(job.get("nice_to_have_skills", [])) - must
    owned = normalize_skills(profile.get("skills", []))

    if must or nice:
        m_hit, n_hit = sorted(must & owned), sorted(nice & owned)
        m_miss, n_miss = sorted(must - owned), sorted(nice - owned)
        m_frac = len(m_hit) / len(must) if must else 0.0
        n_frac = len(n_hit) / len(nice) if nice else 0.0
        if must and nice:
            score = (1 - NICE_SHARE) * m_frac + NICE_SHARE * n_frac
        else:
            score = m_frac if must else n_frac
        parts = []
        if must:
            parts.append(f"You have {len(m_hit)} of {len(must)} must-have skills")
        if nice:
            parts.append(f"{len(n_hit)} of {len(nice)} nice-to-have")
        reason = ", ".join(parts)
        if m_miss:
            reason += f". Missing must-have: {', '.join(m_miss)}"
        if n_miss:
            reason += f". Missing nice-to-have: {', '.join(n_miss)}"
        return {
            "key": "skills", "score": score, "weight": WEIGHTS["skills"], "reason": reason + ".",
            "details": {
                "matched": m_hit + n_hit, "missing_must_have": m_miss, "missing_nice_to_have": n_miss,
                "basis": "listed",
            },
        }

    text = str(job.get("description", "")).lower()
    if not text.strip():
        return None
    found = sorted(s for s in owned if _mentioned(s, text))
    score = min(1.0, len(found) / MIN_DESCRIPTION_HITS)
    if found:
        reason = (f"The job lists no skills. {len(found)} of your skills appear in the "
                  f"description: {', '.join(found[:8])}.")
    else:
        reason = "The job lists no skills and none of your skills appear in its description."
    return {
        "key": "skills", "score": score,
        "weight": WEIGHTS["skills"] / 2,  # a keyword scan is a weaker signal than a listed skill set
        "reason": reason,
        "details": {"matched": found, "missing_must_have": [], "missing_nice_to_have": [],
                    "basis": "description"},
    }


def _role_tokens(text: str) -> set[str]:
    text = re.sub(r"[-/_,()]", " ", text.lower())
    text = " ".join(text.split())
    for old, new in ROLE_PHRASES:
        text = text.replace(old, new)
    out = set()
    for tok in re.findall(r"[a-z0-9+#]+", text):
        tok = ROLE_SAME.get(tok, tok)
        if tok not in ROLE_STOPWORDS and tok not in ROLE_LEVEL_WORDS:
            out.add(tok)
    return out


def _role_factor(job: dict, profile: dict) -> dict | None:
    roles = [r for r in profile.get("target_roles", []) if str(r).strip()]
    title = _role_tokens(job.get("title", ""))
    # A title with no real word in it (for example "R") says nothing about the role.
    if not roles or not title or all(len(token) <= 1 for token in title):
        return None
    best_role, best = roles[0], -1.0
    for role in roles:
        wanted = _role_tokens(role)
        if not wanted:
            continue
        frac = len(wanted & title) / len(wanted)
        if frac > best:
            best_role, best = role, frac
    if best < 0:
        return None
    reason = {
        "good": f"The title fits your target role '{best_role}'.",
        "partial": f"The title partly fits your target role '{best_role}'.",
        "poor": "The title does not resemble any of your target roles.",
    }[_status(best)]
    return {"key": "role", "score": best, "weight": WEIGHTS["role"], "reason": reason}


def _norm_location(text: str) -> str:
    text = " ".join(re.sub(r"[,/()]", " ", text.lower()).split())
    for old, new in LOCATION_ALIASES.items():
        text = text.replace(old, new)
    return text


def _job_arrangement(job: dict) -> str:
    value = job.get("work_arrangement", "")
    if value:
        return value
    loc = str(job.get("location") or "").lower()
    return "remote" if "remote" in loc else ""


def _location_factor(job: dict, prefs: dict) -> dict | None:
    wanted_locs = [_norm_location(l) for l in prefs.get("locations", []) if str(l).strip()]
    wanted_modes = prefs.get("work_arrangements", [])
    mode = _job_arrangement(job)
    job_loc = _norm_location(str(job.get("location") or ""))
    if job_loc in ("remote", ""):
        job_loc = ""

    parts: list[tuple[float, str]] = []
    if wanted_modes and mode:
        ok = mode in wanted_modes
        parts.append((1.0 if ok else 0.0,
                      f"{mode.capitalize()} work {'matches' if ok else 'is not among'} your preferred work style"))
    if wanted_locs and mode != "remote" and job_loc:
        ok = any(w in job_loc or job_loc in w for w in wanted_locs)
        parts.append((1.0 if ok else 0.0,
                      f"{job.get('location')} {'is' if ok else 'is not'} in your preferred locations"))
    if not parts:
        return None
    score = sum(p for p, _ in parts) / len(parts)
    return {"key": "location", "score": score, "weight": WEIGHTS["location"],
            "reason": "; ".join(r for _, r in parts) + "."}


def _job_level(job: dict) -> tuple[str, bool]:
    """(level, was_guessed_from_title)"""
    if job.get("experience_level") in LEVELS:
        return job["experience_level"], False
    words = set(re.findall(r"[a-z]+", str(job.get("title", "")).lower()))
    for level, hints in LEVEL_HINTS:
        if words & set(hints):
            return level, True
    return "", False


def _experience_factor(job: dict, prefs: dict) -> dict | None:
    wanted = prefs.get("experience_level", "")
    level, guessed = _job_level(job)
    if wanted not in LEVELS or not level:
        return None
    gap = abs(LEVELS.index(level) - LEVELS.index(wanted))
    score = {0: 1.0, 1: 0.5}.get(gap, 0.0)
    how = " (guessed from the title)" if guessed else ""
    if gap == 0:
        reason = f"The {level} level{how} matches yours."
    else:
        reason = f"The job looks {level}-level{how}; you are looking for {wanted}."
    return {"key": "experience", "score": score, "weight": WEIGHTS["experience"], "reason": reason}


def _annual(amount: int | float, period: str) -> float:
    return amount * 12 if period == "month" else amount


def _salary_factor(job: dict, prefs: dict) -> dict | None:
    want = prefs.get("salary_min")
    low, high = job.get("salary_min"), job.get("salary_max")
    offered = high if high is not None else low
    if not want or offered is None:
        return None
    cur_job = str(job.get("salary_currency", "")).upper()
    cur_pref = str(prefs.get("salary_currency", "")).upper()
    if cur_job != cur_pref:
        return None  # no exchange rates here, so different currencies are not compared
    offered_y = _annual(offered, job.get("salary_period", "year"))
    wanted_y = _annual(want, prefs.get("salary_period", "year"))
    ratio = offered_y / wanted_y
    score = 1.0 if ratio >= 1 else max(0.0, (ratio - 0.5) / 0.5)
    word = "up to" if high is not None else "from"
    verb = "meets" if ratio >= 1 else "is below"
    return {
        "key": "salary", "score": score, "weight": WEIGHTS["salary"],
        "reason": f"Pay {word} {offered_y:,.0f} {cur_job}/year {verb} your minimum of {wanted_y:,.0f}.",
    }


def score_job(job: dict, profile: dict | None, preferences: dict | None) -> dict:
    """Score one job against the profile and preferences.

    Returns score (0-100), confidence (low/medium/high), reasons (flat list of
    lines) and breakdown (one entry per factor, including factors that could
    not be judged, so the screen can say why).
    """
    profile, prefs = profile or {}, preferences or {}
    judged = [
        f for f in (
            _skills_factor(job, profile),
            _role_factor(job, profile),
            _location_factor(job, prefs),
            _experience_factor(job, prefs),
            _salary_factor(job, prefs),
        ) if f
    ]
    done = {f["key"] for f in judged}

    breakdown = []
    for f in judged:
        breakdown.append({
            "key": f["key"], "label": LABELS[f["key"]], "weight": round(f["weight"], 1),
            "score": _pct(f["score"]), "status": _status(f["score"]),
            "reason": f["reason"], "details": f.get("details", {}),
        })
    for key in WEIGHTS:
        if key not in done:
            breakdown.append({
                "key": key, "label": LABELS[key], "weight": 0, "score": None,
                "status": "unknown", "reason": _unknown_reason(key, job, profile, prefs),
                "details": {},
            })
    order = list(WEIGHTS)
    breakdown.sort(key=lambda b: order.index(b["key"]))

    total = sum(f["weight"] for f in judged)
    if not total:
        return {
            "score": 0.0, "confidence": "low", "breakdown": breakdown,
            "reasons": ["No skills or description keywords provided yet"],
        }

    score = _pct(sum(f["weight"] * f["score"] for f in judged) / total)
    coverage = total / sum(WEIGHTS.values())
    confidence = "high" if coverage >= 0.75 else "medium" if coverage >= 0.45 else "low"

    reasons: list[str] = []
    skills = next((f for f in judged if f["key"] == "skills"), None)
    if skills:
        d = skills["details"]
        reasons += [f"Matched skill: {s}" for s in d["matched"][:8]]
        reasons += [f"Potential gap: {s}" for s in (d["missing_must_have"] + d["missing_nice_to_have"])[:5]]
    reasons += [f"{LABELS[f['key']]}: {f['reason']}" for f in judged if f["key"] != "skills"]
    return {"score": score, "confidence": confidence, "breakdown": breakdown, "reasons": reasons}


def _unknown_reason(key: str, job: dict, profile: dict, prefs: dict) -> str:
    if key == "skills":
        return "The job has no listed skills or description to compare."
    if key == "role":
        return ("Add a target role to your profile." if not profile.get("target_roles")
                else "The job has no title to compare.")
    if key == "location":
        return ("Add preferred locations or a work style in Job preferences." if not
                (prefs.get("locations") or prefs.get("work_arrangements"))
                else "The job does not say where or how it is done.")
    if key == "experience":
        return ("Choose an experience level in Job preferences." if not prefs.get("experience_level")
                else "The job does not state a level and the title gives no hint.")
    if not prefs.get("salary_min"):
        return "Set a minimum salary in Job preferences."
    if job.get("salary_min") is None and job.get("salary_max") is None:
        return "The job does not list a salary."
    return "The job and your preferences use different currencies, so they are not compared."