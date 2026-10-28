import re
from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)


class JobCreate(BaseModel):
    title: str = Field(min_length=1, max_length=250)
    company: str = Field(min_length=1, max_length=250)
    location: str | None = None
    source: str = "manual"
    source_job_id: str | None = None
    url: HttpUrl | None = None
    description: str = ""
    skills: list[str] = []  # must-have skills
    # Optional details that let matching judge more than skills.
    nice_to_have_skills: list[str] = Field(default_factory=list, max_length=50)
    employment_types: list[str] = Field(default_factory=list, max_length=10)
    published_at: datetime | None = None
    work_arrangement: Literal["", "remote", "hybrid", "onsite"] = ""
    experience_level: Literal["", "intern", "entry", "mid", "senior", "lead"] = ""
    salary_min: int | None = Field(default=None, ge=0, le=1_000_000_000)
    salary_max: int | None = Field(default=None, ge=0, le=1_000_000_000)
    salary_currency: str = Field(default="", max_length=10)
    salary_period: Literal["year", "month"] = "year"

    @model_validator(mode="after")
    def _check_salary(self):
        self.salary_currency = self.salary_currency.strip().upper()
        low, high = self.salary_min, self.salary_max
        if low is not None and high is not None and high < low:
            raise ValueError("Maximum salary cannot be lower than minimum salary")
        if (low is not None or high is not None) and not re.fullmatch(r"[A-Z]{3}", self.salary_currency):
            raise ValueError("A 3-letter currency code (for example INR or USD) is required when a salary is given")
        return self


class MatchFactor(BaseModel):
    """One line of the score explanation. score is None when the factor could not be judged."""

    key: str
    label: str
    weight: float
    score: float | None = None
    status: Literal["good", "partial", "poor", "unknown"]
    reason: str
    details: dict = Field(default_factory=dict)


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
    nice_to_have_skills: list[str] = Field(default_factory=list)
    employment_types: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    work_arrangement: str = ""
    experience_level: str = ""
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str = ""
    salary_period: str = "year"
    match_score: float
    match_reasons: list[str]
    match_confidence: str = ""  # "", "low", "medium" or "high" ("" = not scored with Stage 3 yet)
    match_breakdown: list[MatchFactor] = Field(default_factory=list)
    feedback: str = ""  # "" or "not_interested"
    feedback_reason: str = ""
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


FeedbackReason = Literal["", "role", "skills", "location", "salary", "experience", "company", "other"]


class JobFeedback(BaseModel):
    """Mark a job as "not interested", with an optional reason. An empty value restores it."""

    value: Literal["", "not_interested"]
    reason: FeedbackReason = ""

    @model_validator(mode="after")
    def _reason_needs_dismissal(self):
        if self.value == "" and self.reason:
            raise ValueError("A reason can only be given when marking a job as not interested")
        return self


class JobImportRequest(BaseModel):
    """Pasted job-posting text, or a link to read when no text is given.

    Pasted text of 20+ characters always wins (the link is then only saved as the
    posting URL). Nothing is saved; the result only fills in the form.
    """

    text: str = Field(default="", max_length=20_000)
    url: str = Field(default="", max_length=2000)


class JobImportDraft(BaseModel):
    title: str = ""
    company: str = ""
    location: str = ""
    url: str = ""
    description: str = ""
    skills: list[str] = Field(default_factory=list)
    nice_to_have_skills: list[str] = Field(default_factory=list)
    work_arrangement: str = ""
    experience_level: str = ""
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str = ""
    salary_period: str = "year"


class JobImportPreview(BaseModel):
    draft: JobImportDraft
    found: list[str]  # which details were recognised
    missing: list[str]  # which details were not, so the screen can say so
    source: Literal["text", "url"] = "text"  # where the text came from
    warnings: list[str] = Field(default_factory=list)  # e.g. hidden text was ignored


class JobDiscoveryListing(BaseModel):
    source_job_id: str
    title: str
    company: str
    location: str | None = None
    url: HttpUrl
    description: str = ""
    skills: list[str] = Field(default_factory=list)
    nice_to_have_skills: list[str] = Field(default_factory=list)
    employment_types: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    work_arrangement: str = ""
    experience_level: str = ""
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str = ""
    salary_period: str = "year"


class JobDiscoveryResponse(BaseModel):
    source: Literal["jobicy"] = "jobicy"
    geo: str
    results: list[JobDiscoveryListing]
    has_more: bool
    next_cursor: str | None = None


class JobApplicationPlan(BaseModel):
    job_id: str
    title: str
    company: str
    score: float
    confidence: Literal["low", "medium", "high"]
    fit_summary: str
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    positioning: list[str] = Field(default_factory=list)
    resume_focus: list[str] = Field(default_factory=list)
    cover_letter_angle: str = ""
    interview_questions: list[str] = Field(default_factory=list)


class ResumeBulletSuggestion(BaseModel):
    section: str = Field(min_length=1, max_length=200)
    source_text: str = Field(min_length=1, max_length=4000)
    tailored_text: str = Field(min_length=1, max_length=4000)


class JobApplicationMaterials(BaseModel):
    job_id: str
    title: str
    company: str
    cover_letter: str = Field(min_length=1, max_length=8000)
    resume_summary: str = Field(min_length=1, max_length=2000)
    skills_to_prioritize: list[str] = Field(default_factory=list, max_length=50)
    bullet_suggestions: list[ResumeBulletSuggestion] = Field(
        default_factory=list, max_length=30
    )
    caveats: list[str] = Field(default_factory=list, max_length=20)


class CoverLetterPdfRequest(BaseModel):
    cover_letter: str = Field(min_length=1, max_length=8000)

    @field_validator("cover_letter")
    @classmethod
    def _require_non_whitespace_letter(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Cover-letter text cannot be blank.")
        return value


class ApplicationCreate(BaseModel):
    job_id: str
    status: str = "saved"
    notes: str = ""


class StatusHistoryEntry(BaseModel):
    from_status: str | None = None
    to_status: str
    at: datetime
    actor: str = "user"
    note: str = ""


class StatusChange(BaseModel):
    """Request body for PATCH /api/applications/{id}/status."""

    model_config = ConfigDict(str_strip_whitespace=True)

    status: str = Field(min_length=1, max_length=50)
    note: str = Field(default="", max_length=500)


class ApplicationRead(BaseModel):
    id: str
    job_id: str
    status: str
    allowed_next: list[str] = Field(default_factory=list)
    gated_next: list[str] = Field(default_factory=list)
    status_history: list[StatusHistoryEntry] = Field(default_factory=list)
    resume_id: str | None = None
    resume_label: str | None = None
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


class PreferencesUpdate(BaseModel):
    """Job preferences. Every field is optional. Target roles live in the
    profile (target_roles), so they are not repeated here."""

    model_config = ConfigDict(str_strip_whitespace=True)

    locations: list[str] = Field(default_factory=list, max_length=20)
    experience_level: Literal["", "intern", "entry", "mid", "senior", "lead"] = ""
    work_arrangements: list[Literal["remote", "hybrid", "onsite"]] = Field(
        default_factory=list, max_length=3
    )
    salary_min: int | None = Field(default=None, ge=0, le=1_000_000_000)
    salary_max: int | None = Field(default=None, ge=0, le=1_000_000_000)
    salary_currency: str = Field(default="", max_length=10)
    salary_period: Literal["year", "month"] = "year"


class PreferencesRead(PreferencesUpdate):
    updated_at: datetime | None = None


class ResumeRead(BaseModel):
    """Details of a stored resume file. The file itself is downloaded separately."""

    id: str
    label: str
    filename: str
    size: int
    page_count: int
    is_active: bool
    uploaded_at: datetime
    extracted_at: datetime | None = None


class ResumeUpdate(BaseModel):
    """Rename a resume and/or make it the active one."""

    model_config = ConfigDict(str_strip_whitespace=True)

    label: str | None = Field(default=None, max_length=200)
    is_active: bool | None = None


class ResumeExtractRead(BaseModel):
    """Details the AI read from a stored resume. They are NOT confirmed: they
    only fill the profile form, and nothing is saved until you click Save."""

    extracted: dict
    cached: bool
    extracted_at: datetime