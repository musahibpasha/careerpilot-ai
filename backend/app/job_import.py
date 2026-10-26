"""Stage 4: turn pasted job-posting text into a draft job.

This uses plain rules (labels like "Location:", salary patterns, a list of
known skills). It does not call an AI service, so it works offline, costs
nothing and always gives the same answer for the same text. The result is only
a *draft*: the screen fills the form with it and you review before saving.
"""

import re

MAX_IMPORT_CHARS = 20_000
MIN_IMPORT_CHARS = 20

# (canonical skill, spellings to look for). Plain words that are also common
# English ("express", "next", "rest", "excel") only count in their tech spelling.
SKILL_VOCAB: list[tuple[str, tuple[str, ...]]] = [
    ("python", ("python",)), ("java", ("java",)),
    ("javascript", ("javascript", "ecmascript")), ("typescript", ("typescript",)),
    ("c#", ("c#", "c sharp")), ("c++", ("c++",)), ("go", ("golang",)),
    ("rust", ("rust",)), ("ruby", ("ruby",)), ("rails", ("ruby on rails", "rails")),
    ("php", ("php",)), ("laravel", ("laravel",)), ("kotlin", ("kotlin",)),
    ("swift", ("swiftui",)), ("dart", ("dart",)), ("flutter", ("flutter",)),
    ("scala", ("scala",)), ("sql", ("sql",)), ("bash", ("bash", "shell scripting")),
    ("react", ("react", "reactjs", "react.js")), ("react native", ("react native",)),
    ("angular", ("angular", "angularjs")), ("vue", ("vue", "vuejs", "vue.js")),
    ("next", ("next.js", "nextjs", "next js")), ("node", ("node", "node.js", "nodejs")),
    ("express", ("express.js", "expressjs")), ("redux", ("redux",)),
    ("html", ("html", "html5")), ("css", ("css", "css3")), ("sass", ("sass", "scss")),
    ("tailwind", ("tailwind", "tailwindcss")), ("bootstrap", ("bootstrap",)),
    ("django", ("django",)), ("flask", ("flask",)), ("fastapi", ("fastapi",)),
    ("spring boot", ("spring boot", "springboot")), ("spring", ("spring framework",)),
    (".net", (".net", "asp.net", "dotnet")), ("graphql", ("graphql",)),
    ("rest", ("rest api", "rest apis", "restful")), ("microservices", ("microservices", "microservice")),
    ("mysql", ("mysql",)), ("postgresql", ("postgresql", "postgres")),
    ("mongodb", ("mongodb", "mongo")), ("redis", ("redis",)),
    ("elasticsearch", ("elasticsearch",)), ("kafka", ("kafka",)), ("rabbitmq", ("rabbitmq",)),
    ("docker", ("docker",)), ("kubernetes", ("kubernetes", "k8s")),
    ("aws", ("aws", "amazon web services")), ("azure", ("azure",)),
    ("gcp", ("gcp", "google cloud")), ("terraform", ("terraform",)), ("ansible", ("ansible",)),
    ("jenkins", ("jenkins",)), ("ci/cd", ("ci/cd", "ci-cd", "cicd")),
    ("git", ("git", "github", "gitlab")), ("linux", ("linux",)), ("nginx", ("nginx",)),
    ("machine learning", ("machine learning",)), ("deep learning", ("deep learning",)),
    ("nlp", ("nlp", "natural language processing")),
    ("computer vision", ("computer vision",)), ("llm", ("llm", "llms", "large language model")),
    ("pytorch", ("pytorch",)), ("tensorflow", ("tensorflow",)),
    ("scikit-learn", ("scikit-learn", "sklearn")), ("pandas", ("pandas",)), ("numpy", ("numpy",)),
    ("spark", ("spark", "pyspark")), ("hadoop", ("hadoop",)), ("airflow", ("airflow",)),
    ("snowflake", ("snowflake",)), ("bigquery", ("bigquery",)), ("tableau", ("tableau",)),
    ("power bi", ("power bi", "powerbi")), ("excel", ("ms excel", "microsoft excel", "advanced excel")),
    ("selenium", ("selenium",)), ("cypress", ("cypress",)), ("jest", ("jest",)),
    ("pytest", ("pytest",)), ("junit", ("junit",)), ("figma", ("figma",)), ("jira", ("jira",)),
    ("agile", ("agile",)), ("scrum", ("scrum",)), ("android", ("android",)), ("ios", ("ios",)),
]

# Skills too short or too common to find inside prose. They only count when they
# stand alone as an item in a list, written exactly like this ("Python, Go, SQL").
LIST_ONLY_SKILLS = ("Go", "R", "C", "Excel", "Swift")

NICE_HEADINGS = (
    "nice to have", "nice-to-have", "good to have", "good-to-have", "preferred",
    "preferred qualifications", "preferred skills", "bonus", "bonus points",
    "desirable", "optional", "a plus", "plus",
)
NICE_INLINE = re.compile(
    r"^(?:nice[- ]to[- ]have|good[- ]to[- ]have|preferred(?: qualifications| skills)?|bonus(?: points)?)"
    r"\s*[:\-–]\s*(.*)$",
    re.I,
)
NICE_PHRASES = re.compile(
    r"\(\s*(?:nice to have|preferred|bonus|optional)\s*\)|\b(?:is|are)\s+a\s+(?:plus|bonus)\b|\ba\s+plus\b",
    re.I,
)

LABEL_TITLE = re.compile(r"^(?:job\s+)?(?:title|position|role)\s*[:\-–]\s*(.+)$", re.I)
LABEL_COMPANY = re.compile(
    r"^(?:company|employer|organi[sz]ation|hiring company|client)(?:\s+name)?\s*[:\-–]\s*(.+)$", re.I
)
LABEL_LOCATION = re.compile(
    r"^(?:job\s+)?(?:location|office|based in|work location)\s*[:\-–]\s*(.+)$", re.I
)
LABEL_MODE = re.compile(r"^(?:work\s*(?:type|model|mode|arrangement)|workplace|job type)\s*[:\-–]\s*(.+)$", re.I)
ABOUT_HEADING = re.compile(
    r"^about\s+(?!the\b|this\b|us\b|you\b|your\b|our\b|role\b|job\b|company\b|team\b|them\b)(.{2,60}?)\s*:?$",
    re.I,
)
IS_HIRING = re.compile(r"^(.{2,50}?)\s+(?:is hiring|is looking for|is seeking|are hiring)\b", re.I)
SKIP_TITLE_LINES = re.compile(
    r"^(?:job description|about (?:us|the (?:job|role|company))|we(?:'|’)?re hiring|we are hiring|"
    r"apply(?: now)?|overview|summary|description|job details)\s*:?$",
    re.I,
)
# Words that mean the part after " - " is more job description, not a company name.
JOB_WORDS = re.compile(
    r"\b(?:remote|hybrid|on-?site|full[- ]time|part[- ]time|contract|permanent|internship|intern|"
    r"engineer|developer|analyst|manager|designer|lead|senior|junior|india|usa|uk|us)\b", re.I
)
TITLE_PREFIX = re.compile(r"^(?:we(?:'|’)?re hiring|we are hiring|hiring|job opening|opening|vacancy)\s*[:\-–]\s*", re.I)

CURRENCIES = {
    "₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR",
    "$": "USD", "us$": "USD", "usd": "USD",
    "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP",
}
_CUR = r"(₹|Rs\.?|INR|US\$|USD|\$|€|EUR|£|GBP)"
_NUM = r"(\d[\d,]*(?:\.\d+)?)"
SALARY_LPA = re.compile(
    rf"(?:{_CUR}\s*)?{_NUM}\s*(?:(?:-|–|—|to)\s*(?:{_CUR}\s*)?{_NUM}\s*)?(?:LPA|lakhs?|lacs?)\b", re.I
)
SALARY_BEFORE = re.compile(
    rf"(?<![A-Za-z]){_CUR}\s*{_NUM}\s*(k)?(?:\s*(?:-|–|—|to)\s*{_CUR}?\s*{_NUM}\s*(k)?)?", re.I
)
SALARY_AFTER = re.compile(
    rf"{_NUM}\s*(k)?\s*(?:-|–|—|to)\s*{_NUM}\s*(k)?\s*{_CUR}", re.I
)
PAY_WORDS = re.compile(
    r"salary|compensation|\bpay\b|\bctc\b|stipend|package|per annum|per year|/\s*year|annual|"
    r"per month|/\s*month|up to|upto|starting|from",
    re.I,
)
MONTH_WORDS = re.compile(r"per month|/\s*month|/\s*mo\b|monthly|a month|\bpm\b", re.I)
URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.I)


def _clean(value: str, limit: int = 120) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t-–—|:·•*#")[:limit].strip()


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.replace("\r", "\n").split("\n") if ln.strip()]


def _num(raw: str, k: str | None = None) -> float:
    value = float(raw.replace(",", ""))
    return value * 1000 if k else value


# --------------------------------------------------------------------------
# Individual fields
# --------------------------------------------------------------------------
def _find_title_and_company(lines: list[str]) -> tuple[str, str]:
    head = lines[:40]
    title = company = ""

    for ln in head:
        if not title and (m := LABEL_TITLE.match(ln)):
            title = _clean(m.group(1), 150)
        if not company and (m := LABEL_COMPANY.match(ln)):
            company = _clean(m.group(1), 100)

    if not title:
        for ln in lines[:5]:
            if (LABEL_COMPANY.match(ln) or LABEL_LOCATION.match(ln) or LABEL_MODE.match(ln)
                    or SKIP_TITLE_LINES.match(ln) or len(ln) > 100 or URL_RE.match(ln)
                    or ln.endswith(".") or len(ln.split()) > 12):
                continue  # a full sentence is not a job title
            title = _clean(TITLE_PREFIX.sub("", ln), 150)
            break

    if title:
        m = re.match(r"^(.*?)\s+(?:at|@)\s+(.{2,60})$", title, re.I)
        if m and m.group(1):
            title = _clean(m.group(1), 150)
            company = company or _clean(m.group(2), 100)
        title = re.sub(r"\s*[\(\[]\s*(?:remote|hybrid|on-?site)\s*[\)\]]\s*$", "", title, flags=re.I)
        title = _clean(re.sub(r"\s+[-–—|]\s+(?:remote|hybrid|on-?site|full[- ]time|part[- ]time|contract)\s*$", "", title, flags=re.I), 150)
        if not company:
            m = re.match(r"^(.+?)\s+[-–—|]\s+([A-Z0-9][\w&.']*(?:\s+[A-Z0-9][\w&.']*){0,3})$", title)
            if m and not re.search(JOB_WORDS, m.group(2)):
                title, company = _clean(m.group(1), 150), _clean(m.group(2), 100)

    if not company:
        for ln in lines[:40]:
            if (m := ABOUT_HEADING.match(ln)):
                company = _clean(m.group(1), 100)
                break
    if not company:
        for ln in lines[:15]:
            if (m := IS_HIRING.match(ln)):
                company = _clean(m.group(1), 100)
                break
    return title, company


def _find_location(lines: list[str]) -> str:
    for ln in lines[:60]:
        if (m := LABEL_LOCATION.match(ln)):
            return _clean(m.group(1), 100)
    return ""


def _find_arrangement(lines: list[str], text: str) -> str:
    for ln in lines[:60]:
        if (m := LABEL_MODE.match(ln)):
            found = _arrangement_in(m.group(1))
            if found:
                return found
    return _arrangement_in(text)


def _arrangement_in(text: str) -> str:
    low = text.lower()
    if re.search(r"\bhybrid\b", low):
        return "hybrid"
    if re.search(r"\b(?:fully |100% )?remote\b|work from home|\bwfh\b", low) and not re.search(
        r"\bnot remote\b|no remote|remote work is not", low
    ):
        return "remote"
    if re.search(r"\bon-?site\b|in[- ]office|work from office|\bwfo\b", low):
        return "onsite"
    return ""


def _find_level(title: str, text: str) -> str:
    t = title.lower()
    for level, pattern in (
        ("intern", r"\bintern(?:ship)?\b|\btrainee\b"),
        ("lead", r"\blead\b|\bprincipal\b|\bhead of\b|\barchitect\b|\bstaff\b"),
        ("senior", r"\bsenior\b|\bsr\.?\b"),
        ("entry", r"\bjunior\b|\bjr\.?\b|\bfresher\b|\bgraduate\b|\bentry[- ]level\b"),
    ):
        if re.search(pattern, t):
            return level
    if re.search(r"\bfreshers?\b|\bentry[- ]level\b", text, re.I):
        return "entry"
    m = re.search(r"(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*)?(?:years?|yrs?)\b", text, re.I)
    if m:
        years = int(m.group(1))
        return "entry" if years <= 1 else "mid" if years <= 4 else "senior" if years <= 8 else "lead"
    return ""


def _find_salary(text: str) -> dict:
    """Return salary_min / salary_max / salary_currency / salary_period, or {}."""
    candidates: list[tuple[int, float | None, float | None, str, str]] = []  # pos, low, high, cur, kind

    for m in SALARY_LPA.finditer(text):
        low = float(m.group(2).replace(",", "")) * 100_000  # "18 LPA" is clearly pay already
        high = float(m.group(4).replace(",", "")) * 100_000 if m.group(4) else None
        candidates.append((m.start(), low, high, "INR", "lpa"))

    for m in SALARY_BEFORE.finditer(text):
        cur = m.group(1)
        low = _num(m.group(2), m.group(3))
        high = _num(m.group(5), m.group(6)) if m.group(5) else None
        candidates.append((m.start(), low, high, CURRENCIES[cur.lower()], "currency"))

    for m in SALARY_AFTER.finditer(text):
        low, high = _num(m.group(1), m.group(2)), _num(m.group(3), m.group(4))
        candidates.append((m.start(), low, high, CURRENCIES[m.group(5).lower()], "currency"))

    for pos, low, high, cur, kind in sorted(candidates):
        window = text[max(0, pos - 80): pos + 120]
        is_range = high is not None
        if kind == "currency":
            if low < 1000 or (high is not None and high < 1000):
                continue  # hourly rates, "$5 off", etc.
            if not is_range and not PAY_WORDS.search(window):
                continue  # a lone amount must sit next to a pay word
        if is_range and high < low:
            low, high = high, low
        period = "month" if MONTH_WORDS.search(text[pos: pos + 60]) else "year"
        up_to = bool(re.search(r"(?:up ?to|max(?:imum)?)\s*$", text[max(0, pos - 12): pos], re.I))
        out = {
            "salary_min": None if up_to and not is_range else int(round(low)),
            "salary_max": int(round(high)) if is_range else (int(round(low)) if up_to else None),
            "salary_currency": cur,
            "salary_period": period,
        }
        return out
    return {}


def _find_skills(lines: list[str]) -> tuple[list[str], list[str]]:
    must: list[str] = []
    nice: list[str] = []
    mode = "must"

    def add(target: list[str], skill: str):
        if skill not in must and skill not in nice:
            target.append(skill)

    for raw in lines:
        low = raw.lower().strip(" :-–*•#\t")
        if low in NICE_HEADINGS:
            mode = "nice"
            continue
        if (low.endswith(":") or len(low) < 40) and re.fullmatch(
            r"(?:requirements?|required(?: skills)?|must[- ]have|qualifications?|skills?|key skills|"
            r"what you(?:'|’)?ll (?:need|do)|what we(?:'|’)?re looking for|who you are|responsibilities|"
            r"about the role|tech(?:nical)? stack|benefits|what we offer|perks)",
            low.rstrip(":"),
        ):
            mode = "must"
            continue

        body = raw
        line_mode = mode
        if (m := NICE_INLINE.match(raw.strip(" *•-–#"))):
            line_mode, body = "nice", m.group(1)

        # "...Kubernetes is a plus." only makes that one sentence nice-to-have.
        for sentence in re.split(r"(?<=[.!?])\s+", body):
            sentence_mode = "nice" if line_mode == "nice" or NICE_PHRASES.search(sentence) else "must"
            _collect(sentence, nice if sentence_mode == "nice" else must, add)
    return must[:25], nice[:15]


def _collect(text: str, target: list[str], add) -> None:
    lowered = text.lower()
    for canon, spellings in SKILL_VOCAB:
        for spelling in spellings:
            if re.search(rf"(?<![a-z0-9+#.]){re.escape(spelling)}(?![a-z0-9+#])", lowered):
                add(target, canon)
                break
    items = [i.strip(" .*•-–") for i in re.split(r"[,;|/•·]", text)]
    for skill in LIST_ONLY_SKILLS:
        if skill in items:
            add(target, skill.lower())


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
def extract_job(text: str, url: str = "") -> dict:
    """Return {"draft": {...}, "found": [...], "missing": [...]} for pasted job text."""
    text = text.strip()
    lines = _lines(text)
    title, company = _find_title_and_company(lines)
    location = _find_location(lines)
    arrangement = _find_arrangement(lines, text)
    if not location and arrangement == "remote":
        location = "Remote"
    must, nice = _find_skills(lines)
    salary = _find_salary(text)

    if not url:
        m = URL_RE.search(text)
        url = m.group(0).rstrip(".,;)") if m else ""

    draft = {
        "title": title,
        "company": company,
        "location": location,
        "url": url,
        "description": text,
        "skills": must,
        "nice_to_have_skills": nice,
        "work_arrangement": arrangement,
        "experience_level": _find_level(title, text),
        "salary_min": salary.get("salary_min"),
        "salary_max": salary.get("salary_max"),
        "salary_currency": salary.get("salary_currency", ""),
        "salary_period": salary.get("salary_period", "year"),
    }
    present = {
        "title": bool(title),
        "company": bool(company),
        "location": bool(location),
        "skills": bool(must),
        "nice_to_have_skills": bool(nice),
        "work_arrangement": bool(arrangement),
        "experience_level": bool(draft["experience_level"]),
        "salary": bool(salary),
    }
    return {
        "draft": draft,
        "found": [k for k, v in present.items() if v],
        "missing": [k for k, v in present.items() if not v],
    }