import re


def tokens(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[a-zA-Z0-9+#.]+", text) if len(token) > 1}


def calculate_match(profile_skills: list[str], job_skills: list[str], description: str = "") -> tuple[float, list[str]]:
    """Transparent starter score: matched listed skills / distinct listed job skills.
    Replace/extend this with an explainable weighted matcher after the basic flow works.
    """
    expected = {s.strip().lower() for s in job_skills if s.strip()}
    if not expected:
        expected = tokens(description)
    if not expected:
        return 0.0, ["No skills or description keywords provided yet"]
    owned = {s.strip().lower() for s in profile_skills if s.strip()}
    matched = sorted(expected & owned)
    score = round(100 * len(matched) / len(expected), 1)
    reasons = [f"Matched skill: {skill}" for skill in matched[:8]]
    missing = sorted(expected - owned)
    reasons.extend(f"Potential gap: {skill}" for skill in missing[:5])
    return score, reasons
