"""Fast, offline resume parser. No API key, no network, no rate limits.

Returns the same shape as the OpenRouter-based extractor so the rest of the
app (review form, candidate profile, job matching) works unchanged.
"""

from __future__ import annotations

import re
from datetime import date

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![\w/])(\+?\d[\d\s().-]{8,16}\d)(?![\w/])")
URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>()\[\],;]+"
    r"|(?:github\.com|linkedin\.com/in|linkedin\.com/company)/[^\s<>()\[\],;]+",
    re.IGNORECASE,
)

MONTHS = (
    "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|"
    "january|february|march|april|june|july|august|september|october|november|december"
)
_DATE = rf"(?:(?:{MONTHS})\.?,?\s+\d{{4}}|\d{{1,2}}/\d{{4}}|\d{{4}})"
DATE_RANGE_RE = re.compile(
    rf"({_DATE})\s*(?:-|–|—|to)\s*({_DATE}|present|current|now|ongoing|till date)",
    re.IGNORECASE,
)

SECTION_ALIASES = {
    "summary": ["summary", "professional summary", "profile", "career objective",
                "objective", "about me", "about", "career summary"],
    "skills": ["skills", "technical skills", "key skills", "core skills", "core competencies",
               "skills & tools", "tech stack", "technologies", "competencies"],
    "experience": ["experience", "work experience", "professional experience",
                   "employment history", "work history", "internships", "internship",
                   "employment", "career history"],
    "education": ["education", "academic background", "academics", "qualifications",
                  "educational qualifications", "academic qualifications"],
    "projects": ["projects", "personal projects", "academic projects", "key projects",
                 "project work", "side projects"],
    "certifications": ["certifications", "certification", "certificates", "licenses",
                       "licenses & certifications", "courses", "training",
                       "courses & certifications"],
    "achievements": ["achievements", "awards", "honors", "accomplishments",
                     "awards & achievements", "extracurricular"],
}
_ALIAS_TO_SECTION = {a: s for s, names in SECTION_ALIASES.items() for a in names}

# Used to spot skills anywhere in the text, even without a Skills heading.
KNOWN_SKILLS = {
    "programming_languages": [
        "Python", "JavaScript", "TypeScript", "Java", "C", "C++", "C#", "Go", "Golang",
        "Rust", "Ruby", "PHP", "Swift", "Kotlin", "Scala", "R", "SQL", "Bash", "Shell",
        "Dart", "MATLAB", "HTML", "CSS", "HTML5", "CSS3",
    ],
    "frameworks": [
        "React", "React.js", "ReactJS", "Next.js", "NextJS", "Angular", "Vue", "Vue.js",
        "Node.js", "NodeJS", "Express", "Express.js", "Django", "Flask", "FastAPI",
        "Spring Boot", "Spring", "Laravel", ".NET", "ASP.NET", "Bootstrap",
        "Tailwind CSS", "Tailwind", "jQuery", "Redux", "Svelte", "Flutter",
        "React Native", "Material UI", "Pandas", "NumPy", "TensorFlow", "PyTorch",
        "Scikit-learn", "Streamlit",
    ],
    "databases": [
        "MongoDB", "MySQL", "PostgreSQL", "Postgres", "SQLite", "Oracle", "Redis",
        "Firebase", "Supabase", "DynamoDB", "Cassandra", "SQL Server", "MariaDB",
    ],
    "cloud_platforms": ["AWS", "Azure", "GCP", "Google Cloud", "Vercel", "Netlify",
                        "Heroku", "DigitalOcean"],
    "devops_tools": ["Docker", "Kubernetes", "Jenkins", "Git", "GitHub", "GitLab",
                     "CI/CD", "Terraform", "Ansible", "GitHub Actions", "Linux"],
    "data_tools": ["Power BI", "Tableau", "Excel", "Microsoft Excel", "Looker",
                   "Data Analytics", "Data Analysis", "Machine Learning", "Jupyter"],
    "other_tools": ["VS Code", "Postman", "Figma", "Jira", "REST API", "REST APIs",
                    "GraphQL", "Agile", "Scrum"],
}
SOFT_SKILLS = [
    "Problem Solving", "Communication", "Adaptability", "Continuous Improvement",
    "Creative Thinking", "Self-learning", "Presentation Skills", "Teamwork",
    "Leadership", "Time Management", "Critical Thinking", "Collaboration",
]

_DEGREE_RE = re.compile(
    r"\b(b\.?\s?tech|m\.?\s?tech|b\.?\s?e\b|m\.?\s?e\b|b\.?\s?sc|m\.?\s?sc|b\.?\s?c\.?a|"
    r"m\.?\s?c\.?a|b\.?\s?com|m\.?\s?com|b\.?\s?a\b|m\.?\s?a\b|mba|phd|ph\.d|diploma|"
    r"bachelor(?:'s)?|master(?:'s)?|pgdm|high school|secondary|higher secondary|"
    r"12th|10th|puc|ssc|hsc)\b",
    re.IGNORECASE,
)
_INSTITUTION_RE = re.compile(
    r"\b(university|college|institute|school|academy|iit|nit|iiit|polytechnic)\b",
    re.IGNORECASE,
)
_TITLE_WORDS = re.compile(
    r"\b(developer|engineer|intern|analyst|manager|designer|consultant|architect|"
    r"administrator|specialist|associate|lead|director|scientist|trainee|executive|"
    r"officer|programmer|tester|qa|sde|founder|coordinator|assistant|head)\b",
    re.IGNORECASE,
)
_BULLET_RE = re.compile(r"^\s*(?:[-•●▪■◦▶►*·–]|\d+[.)])\s*")


def _clean_lines(text: str) -> list[str]:
    text = text.replace("\r", "\n").replace("\u00a0", " ").replace("\t", " ")
    text = re.sub(r"\(cid:\d+\)", " ", text)  # broken PDF glyphs
    text = re.sub(r":contentReference\[[^\]]*\]\{[^}]*\}", "", text)
    lines = [re.sub(r"[ ]{2,}", " ", ln).strip() for ln in text.split("\n")]
    return [ln for ln in lines if ln]


def _heading_key(line: str) -> str | None:
    stripped = re.sub(r"^[#*\s_>-]+|[#*\s_:>-]+$", "", line).lower()
    stripped = re.sub(r"\s+", " ", stripped)
    if len(stripped) > 40:
        return None
    return _ALIAS_TO_SECTION.get(stripped)


def _split_sections(lines: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    header: list[str] = []
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        key = _heading_key(line)
        if key:
            current = key
            sections.setdefault(current, [])
            continue
        if current is None:
            header.append(line)
        else:
            sections[current].append(line)
    return header, sections


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.casefold().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


def _find_known(text: str, vocabulary: list[str]) -> list[str]:
    found = []
    for term in vocabulary:
        pattern = r"(?<![A-Za-z0-9+#.])" + re.escape(term) + r"(?![A-Za-z0-9+#])"
        if re.search(pattern, text, re.IGNORECASE):
            found.append(term)
    return found


def _canonical(term: str) -> str:
    aliases = {
        "reactjs": "React.js", "react.js": "React.js", "nextjs": "Next.js",
        "nodejs": "Node.js", "golang": "Go", "postgres": "PostgreSQL",
        "html5": "HTML", "css3": "CSS", "tailwind": "Tailwind CSS",
        "microsoft excel": "Microsoft Excel",
    }
    return aliases.get(term.lower(), term)


def _parse_date(value: str) -> tuple[int, int] | None:
    value = value.strip().lower()
    if value in {"present", "current", "now", "ongoing", "till date"}:
        today = date.today()
        return today.year, today.month
    month_names = {m: i + 1 for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
    m = re.match(r"([a-z]+)\.?,?\s+(\d{4})", value)
    if m and m.group(1)[:3] in month_names:
        return int(m.group(2)), month_names[m.group(1)[:3]]
    m = re.match(r"(\d{1,2})/(\d{4})", value)
    if m:
        return int(m.group(2)), min(max(int(m.group(1)), 1), 12)
    m = re.match(r"(\d{4})$", value)
    if m:
        return int(m.group(1)), 1
    return None


def _months_between(start: str, end: str) -> int:
    a, b = _parse_date(start), _parse_date(end)
    if not a or not b:
        return 0
    return max((b[0] - a[0]) * 12 + (b[1] - a[1]) + 1, 0)


def _contact(header: list[str], full_text: str) -> dict:
    out: dict = {}
    email = EMAIL_RE.search(full_text)
    if email:
        out["email"] = email.group(0)

    phones = []
    for m in PHONE_RE.finditer(full_text):
        digits = re.sub(r"\D", "", m.group(1))
        if 10 <= len(digits) <= 13 and not re.fullmatch(r"(19|20)\d{2}(19|20)\d{2}", digits):
            phones.append(re.sub(r"\s+", " ", m.group(1)).strip())
    phones = _dedupe(phones)
    if phones:
        out["phone"] = phones[0]
    if len(phones) > 1:
        out["alternate_phone"] = phones[1]

    links = [u.rstrip(".") for u in URL_RE.findall(full_text)]
    others = []
    for link in links:
        low = link.lower()
        if "linkedin.com" in low and "linkedin" not in out:
            out["linkedin"] = link
        elif "github.com" in low and "github" not in out:
            out["github"] = link
        elif EMAIL_RE.search(link):
            continue
        else:
            others.append(link)
    others = _dedupe(others)
    if others:
        out["portfolio"] = others[0]
        if len(others) > 1:
            out["other_profile_links"] = others[1:]

    # Name: first header line that looks like a person's name.
    for line in header[:6]:
        cleaned = re.sub(r"^[#*\s]+|[#*\s]+$", "", line)
        if EMAIL_RE.search(cleaned) or URL_RE.search(cleaned) or re.search(r"\d", cleaned):
            continue
        words = cleaned.split()
        if 2 <= len(words) <= 4 and all(re.fullmatch(r"[A-Za-z.'-]+", w) for w in words):
            out["full_name"] = cleaned.title() if cleaned.isupper() else cleaned
            break

    # Location: "City, State" style line in the header, ignoring contact lines.
    for line in header[:10]:
        text = re.sub(r"^[^\w]+", "", line)  # strip emoji/pin prefix
        if EMAIL_RE.search(text) or URL_RE.search(text):
            continue
        m = re.search(r"([A-Z][A-Za-z .]+(?:,\s*[A-Z][A-Za-z .]+){1,2})", text)
        if m and not re.search(r"\d", m.group(1)):
            out["location"] = m.group(1).strip()
            break
        if line.strip().lstrip("📍 ").strip() and "📍" in line:
            out["location"] = line.replace("📍", "").strip()
            break
    return out


def _parse_skills(section_lines: list[str], full_text: str) -> dict:
    listed: list[str] = []
    categories: dict[str, list[str]] = {}
    for line in section_lines:
        line = _BULLET_RE.sub("", line)
        label = None
        if ":" in line:
            head, tail = line.split(":", 1)
            if len(head) <= 40 and tail.strip():
                label, line = head.strip(), tail
        parts = [p.strip(" .") for p in re.split(r"[,|;•●▪/]|\s{2,}", line) if p.strip(" .")]
        parts = [p for p in parts if 1 < len(p) <= 40 and len(p.split()) <= 4]
        listed.extend(parts)
        if label:
            categories.setdefault(label, []).extend(parts)

    found: dict[str, list[str]] = {}
    for group, vocab in KNOWN_SKILLS.items():
        hits = [_canonical(t) for t in _find_known(full_text, vocab)]
        if hits:
            found[group] = _dedupe(hits)

    soft = _dedupe(_find_known(full_text, SOFT_SKILLS))
    all_skills = _dedupe(
        [s for s in listed if s.casefold() not in {x.casefold() for x in soft}]
        + [s for group in found.values() for s in group]
    )

    result: dict = {"skills": all_skills[:40]}
    if categories:
        result["skill_categories"] = {k: _dedupe(v) for k, v in categories.items()}
    if found.get("programming_languages"):
        result["programming_languages"] = found["programming_languages"]
    if found.get("frameworks"):
        result["frameworks"] = found["frameworks"]
    if found.get("databases"):
        result["databases"] = found["databases"]
    if found.get("cloud_platforms"):
        result["cloud_platforms"] = found["cloud_platforms"]
    if found.get("devops_tools"):
        result["devops_tools"] = found["devops_tools"]
    if soft:
        result["soft_skills"] = soft
    return result


def _group_entries(lines: list[str]) -> list[list[str]]:
    """Split a section into entries. A new entry starts at a non-bullet line that
    follows a bullet block, or at a line carrying a date range."""
    entries: list[list[str]] = []
    current: list[str] = []
    seen_bullet = False
    for line in lines:
        is_bullet = bool(_BULLET_RE.match(line))
        starts_new = False
        if current and not is_bullet:
            if seen_bullet:
                starts_new = True
            elif DATE_RANGE_RE.search(line) and any(DATE_RANGE_RE.search(x) for x in current):
                starts_new = True
        if starts_new:
            entries.append(current)
            current, seen_bullet = [], False
        current.append(line)
        seen_bullet = seen_bullet or is_bullet
    if current:
        entries.append(current)
    return entries


def _split_title_company(text: str) -> tuple[str, str]:
    text = DATE_RANGE_RE.sub("", text)
    text = re.sub(r"\|.*$", "", text) if text.count("|") == 1 and "—" in text else text
    for sep in (" — ", " – ", " - ", " | ", " at ", " @ ", ", "):
        if sep in text:
            left, right = [p.strip(" -–—|,") for p in text.split(sep, 1)]
            if _TITLE_WORDS.search(left) and not _TITLE_WORDS.search(right):
                return left, right
            if _TITLE_WORDS.search(right) and not _TITLE_WORDS.search(left):
                return right, left
            return left, right
    return text.strip(" -–—|,"), ""


def _parse_experience(lines: list[str]) -> list[dict]:
    jobs = []
    for entry in _group_entries(lines):
        head_lines = [ln for ln in entry if not _BULLET_RE.match(ln)]
        bullets = [_BULLET_RE.sub("", ln).strip() for ln in entry if _BULLET_RE.match(ln)]
        if not head_lines:
            continue
        head = " ".join(head_lines[:3])
        match = DATE_RANGE_RE.search(head)
        title, company = _split_title_company(head_lines[0])
        if not company and len(head_lines) > 1:
            second = DATE_RANGE_RE.sub("", head_lines[1]).strip(" -–—|,")
            if second and not _BULLET_RE.match(second):
                company_candidate = re.split(r"\s+\|\s+", second)[0].strip()
                if _TITLE_WORDS.search(company_candidate) and not _TITLE_WORDS.search(title):
                    title, company = company_candidate, title
                else:
                    company = company_candidate
        job: dict = {"title": title, "company": company}
        if match:
            start, end = match.group(1), match.group(2)
            job.update(
                duration=f"{start} – {end}", start_date=start, end_date=end,
                currently_working=end.lower() in {"present", "current", "now", "ongoing", "till date"},
            )
        loc = re.search(r"\|\s*([A-Z][A-Za-z .,]+)$", head)
        if loc:
            job["location"] = loc.group(1).strip()
        if bullets:
            job["responsibilities"] = bullets[:3]
            job["description"] = " ".join(bullets)[:300]
        techs = _dedupe(
            [_canonical(t) for vocab in KNOWN_SKILLS.values() for t in _find_known(" ".join(entry), vocab)]
        )
        if techs:
            job["technologies"] = techs[:12]
        if title or company:
            jobs.append(job)
    return jobs[:8]


def _parse_education(lines: list[str]) -> list[dict]:
    items = []
    for entry in _group_entries(lines):
        text = " ".join(entry)
        degree_line = next((ln for ln in entry if _DEGREE_RE.search(ln)), "")
        inst_line = next(
            (ln for ln in entry if _INSTITUTION_RE.search(ln) and ln != degree_line), ""
        )
        if not inst_line and entry:
            inst_line = next((ln for ln in entry if ln != degree_line), "")
        item: dict = {}
        if inst_line:
            item["institution"] = DATE_RANGE_RE.sub("", inst_line).strip(" -–—|,*")
        if degree_line:
            item["degree"] = re.sub(r"\*+", "", DATE_RANGE_RE.sub("", degree_line)).strip(" -–—|,")
        years = re.findall(r"(?:19|20)\d{2}", text)
        if years:
            item["start_year"] = years[0] if len(years) > 1 else ""
            item["graduation_year"] = years[-1]
            item["duration"] = " – ".join(dict.fromkeys(years[:2])) if len(years) > 1 else years[0]
        grade = re.search(r"(?:gpa|cgpa|grade|percentage|score)\s*[:\-]?\s*([\d.]+\s*(?:/\s*\d+|%)?)", text, re.I)
        if grade:
            item["grade"] = grade.group(1).strip()
        if item.get("institution") or item.get("degree"):
            items.append(item)
    return items[:6]


def _parse_projects(lines: list[str]) -> list[dict]:
    projects = []
    for entry in _group_entries(lines):
        head = [ln for ln in entry if not _BULLET_RE.match(ln)]
        bullets = [_BULLET_RE.sub("", ln).strip() for ln in entry if _BULLET_RE.match(ln)]
        if not head:
            continue
        name_line = head[0]
        role_match = re.search(r"\b((?:front[- ]?end|back[- ]?end|full[- ]?stack)?\s*(?:developer|engineer|analyst|designer))\b", " ".join(head), re.I)
        project: dict = {"name": re.sub(r"\*+", "", DATE_RANGE_RE.sub("", name_line)).strip(" -–—|,")}
        if len(head) > 1 and not DATE_RANGE_RE.fullmatch(head[1].strip()):
            sub = re.sub(r"\*+", "", head[1])
            m = DATE_RANGE_RE.search(sub)
            role_text = DATE_RANGE_RE.sub("", sub).strip(" -–—|,")
            if role_text:
                project["role"] = role_text
            if m:
                project.update(duration=f"{m.group(1)} – {m.group(2)}",
                               start_date=m.group(1), end_date=m.group(2))
        elif role_match:
            project["role"] = role_match.group(1).strip()
        m = DATE_RANGE_RE.search(" ".join(head))
        if m and "duration" not in project:
            project.update(duration=f"{m.group(1)} – {m.group(2)}",
                           start_date=m.group(1), end_date=m.group(2))
        if bullets:
            project["description"] = " ".join(bullets)[:300]
            project["responsibilities"] = bullets[:3]
        techs = _dedupe(
            [_canonical(t) for vocab in KNOWN_SKILLS.values() for t in _find_known(" ".join(entry), vocab)]
        )
        if techs:
            project["technologies"] = ", ".join(techs[:12])
        urls = URL_RE.findall(" ".join(entry))
        for u in urls:
            if "github.com" in u.lower():
                project["github_url"] = u
            else:
                project.setdefault("url", u)
        if project["name"]:
            projects.append(project)
    return projects[:8]


def _parse_certifications(lines: list[str]) -> list[dict]:
    certs = []
    for line in lines:
        if _BULLET_RE.match(line) or len(line) > 140:
            if certs and _BULLET_RE.match(line):
                certs[-1].setdefault("description", _BULLET_RE.sub("", line).strip()[:300])
            continue
        name = re.sub(r"\*+|^#+\s*", "", line).strip(" -–—|,")
        cert: dict = {}
        m = re.match(r"(.+?)\s*\(([^)]+)\)\s*$", name)
        if m:
            cert["name"], cert["issuer"] = m.group(1).strip(), m.group(2).strip()
        else:
            cert["name"] = name
        if cert["name"]:
            certs.append(cert)
    return certs[:12]


def _experience_years(jobs: list[dict]) -> float | None:
    total = sum(_months_between(j.get("start_date", ""), j.get("end_date", "")) for j in jobs
                if j.get("start_date") and j.get("end_date"))
    return round(total / 12, 1) if total else None


def _seniority(years: float | None) -> str:
    if years is None or years < 1:
        return "Entry level"
    if years < 3:
        return "Junior"
    if years < 6:
        return "Mid level"
    return "Senior"


def parse_resume_locally(text: str) -> dict:
    """Parse plain resume text into the same dict shape as the AI extractor."""
    lines = _clean_lines(text)
    header, sections = _split_sections(lines)
    full_text = "\n".join(lines)
    extracted: dict = _contact(header, full_text)

    summary_lines = sections.get("summary", [])
    if summary_lines:
        extracted["experience_summary"] = " ".join(summary_lines)[:300]

    skills = _parse_skills(sections.get("skills", []), full_text)
    extracted.update({k: v for k, v in skills.items() if v})

    jobs = _parse_experience(sections.get("experience", []))
    if jobs:
        extracted["work_experience"] = jobs
        extracted["current_job_title"] = jobs[0].get("title", "")
        extracted["current_company"] = jobs[0].get("company", "")
        if jobs[0].get("currently_working"):
            extracted["employment_status"] = "Employed"
    years = _experience_years(jobs)
    if years is not None:
        extracted["experience_years"] = years
        extracted["relevant_experience_years"] = years
    extracted["seniority_level"] = _seniority(years)

    edu = _parse_education(sections.get("education", []))
    if edu:
        extracted["education"] = edu
    projects = _parse_projects(sections.get("projects", []))
    if projects:
        extracted["projects"] = projects
    certs = _parse_certifications(sections.get("certifications", []))
    if certs:
        extracted["certifications"] = certs
    achievements = [_BULLET_RE.sub("", ln).strip() for ln in sections.get("achievements", [])]
    if achievements:
        extracted["achievements"] = achievements[:10]

    # Headline: a title-like line right under the name, if the resume has one.
    for line in header[1:5]:
        if _TITLE_WORDS.search(line) and not EMAIL_RE.search(line) and len(line) < 80:
            extracted["professional_headline"] = line.strip("*# ")
            break

    # Normalise the keys the rest of the app always expects.
    for key, default in {
        "full_name": "", "email": "", "phone": "", "location": "", "github": "",
        "linkedin": "", "portfolio": "", "target_roles": [], "skills": [],
        "experience_summary": "", "work_experience": [], "projects": [],
        "education": [], "certifications": [], "achievements": [],
    }.items():
        extracted.setdefault(key, default)
    return extracted