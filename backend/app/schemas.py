import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class JobCreate(BaseModel):
    title: str = Field(min_length=1, max_length=250)
    company: str = Field(min_length=1, max_length=250)
    location: str | None = None
    source: str = "manual"
    source_job_id: str | None = None
    url: HttpUrl | None = None
    description: str = ""
    skills: list[str] = []


class JobRead(BaseModel):
    id: str
    title: str
    company: str
    location: str | None
    source: str
    source_job_id: str | None
    url: str | None
    description: str
    skills: list[str]
    match_score: float
    match_reasons: list[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApplicationCreate(BaseModel):
    job_id: str
    status: str = "saved"
    notes: str = ""


class ApplicationRead(BaseModel):
    id: str
    job_id: str
    status: str
    allowed_next: list[str] = Field(default_factory=list)
    notes: str
    created_at: datetime
    updated_at: datetime
    job_title: str
    company: str


class WorkExperience(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(default="", max_length=200)
    company: str = Field(default="", max_length=200)
    duration: str = Field(default="", max_length=100)
    location: str = Field(default="", max_length=150)
    description: str = Field(default="", max_length=4000)


class ProjectEntry(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(default="", max_length=200)
    role: str = Field(default="", max_length=200)
    duration: str = Field(default="", max_length=100)
    technologies: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=4000)


class EducationEntry(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    institution: str = Field(default="", max_length=250)
    degree: str = Field(default="", max_length=250)
    duration: str = Field(default="", max_length=100)
    grade: str = Field(default="", max_length=50)


class CertificationEntry(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(default="", max_length=250)
    issuer: str = Field(default="", max_length=250)


class ProfileUpdate(BaseModel):
    """Every field has a default so older saved profiles can still be read.
    Required-field rules are enforced by profile_problems() on save."""

    model_config = ConfigDict(str_strip_whitespace=True)

    # Contact
    full_name: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=40)
    location: str = Field(default="", max_length=150)
    github: str = Field(default="", max_length=250)
    linkedin: str = Field(default="", max_length=250)
    portfolio: str = Field(default="", max_length=250)

    # Career
    target_roles: list[str] = Field(default_factory=list, max_length=20)
    skills: list[str] = Field(default_factory=list, max_length=100)
    experience_summary: str = Field(default="", max_length=4000)

    # Resume sections
    work_experience: list[WorkExperience] = Field(default_factory=list, max_length=20)
    projects: list[ProjectEntry] = Field(default_factory=list, max_length=20)
    education: list[EducationEntry] = Field(default_factory=list, max_length=10)
    certifications: list[CertificationEntry] = Field(default_factory=list, max_length=30)
    achievements: list[str] = Field(default_factory=list, max_length=30)


class ProfileRead(ProfileUpdate):
    id: str


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def profile_problems(profile: ProfileUpdate) -> list[str]:
    """Return a list of human-readable problems. Empty list = profile is complete."""
    problems: list[str] = []

    if not profile.full_name:
        problems.append("Full name is required")
    if not profile.email:
        problems.append("Email is required")
    elif not _EMAIL_RE.match(profile.email):
        problems.append("Email is not a valid address")
    if len(re.sub(r"\D", "", profile.phone)) < 7:
        problems.append("A valid phone number is required")
    if not profile.location:
        problems.append("Location is required")
    if not profile.target_roles:
        problems.append("Add at least one target role")
    if not profile.skills:
        problems.append("Add at least one skill")
    if not profile.experience_summary:
        problems.append("Professional summary is required")
    if not profile.education:
        problems.append("Add at least one education entry")
    if not profile.work_experience and not profile.projects:
        problems.append("Add at least one work experience or project")

    for i, w in enumerate(profile.work_experience, 1):
        if not (w.title and w.company and w.duration and w.description):
            problems.append(
                f"Work experience #{i}: job title, company, duration and description are required"
            )
    for i, pr in enumerate(profile.projects, 1):
        if not (pr.name and pr.description):
            problems.append(f"Project #{i}: name and description are required")
    for i, e in enumerate(profile.education, 1):
        if not (e.institution and e.degree):
            problems.append(f"Education #{i}: institution and degree are required")
    for i, c in enumerate(profile.certifications, 1):
        if not c.name:
            problems.append(f"Certification #{i}: name is required")

    return problems