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