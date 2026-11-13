import re
from datetime import datetime
from typing import Literal, get_origin

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)


class JobSourceReference(BaseModel):
    source: Literal["linkedin", "indeed", "glassdoor", "jobicy", "arbeitnow"]
    source_job_id: str
    title: str = ""
    url: HttpUrl | None = None
    published_at: datetime | None = None
    repost_dates: list[datetime] = Field(default_factory=list)


class RecruiterCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)

    @field_validator("display_name", "email")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Enter a valid email address.")
        return value.casefold()


class RecruiterWorkspaceSummary(BaseModel):
    candidates: int = 0
    jobs: int = 0
    applications: int = 0
    resumes: int = 0


class RecruiterRead(BaseModel):
    uid: str
    display_name: str
    email: str
    created_at: datetime
    created_by_email: str
    workspace: RecruiterWorkspaceSummary


class RecruiterListResponse(BaseModel):
    items: list[RecruiterRead] = Field(default_factory=list)


DuplicateStatus = Literal[
    "new", "known", "reposted", "cross_board_duplicate", "potential_duplicate"
]


class JobCreate(BaseModel):
    candidate_id: str | None = None
    title: str = Field(min_length=1, max_length=250)
    company: str = Field(min_length=1, max_length=250)
    location: str | None = None
    source: str = "manual"
    source_job_id: str | None = None
    url: HttpUrl | None = None
    application_url: HttpUrl | None = None
    description: str = ""
    skills: list[str] = []  # must-have skills
    # Optional details that let matching judge more than skills.
    nice_to_have_skills: list[str] = Field(default_factory=list, max_length=50)
    employment_types: list[str] = Field(default_factory=list, max_length=10)
    published_at: datetime | None = None
    duplicate_group_id: str | None = None
    duplicate_status: DuplicateStatus | None = None
    duplicate_confidence: Literal["medium", "high"] | None = None
    potential_duplicate_of: str | None = None
    available_sources: list[Literal["linkedin", "indeed", "glassdoor", "jobicy", "arbeitnow"]] = Field(
        default_factory=list
    )
    source_listings: list[JobSourceReference] = Field(default_factory=list)
    original_published_at: datetime | None = None
    latest_repost_at: datetime | None = None
    repost_count: int = Field(default=0, ge=0)
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
    candidate_id: str | None = None
    title: str
    company: str
    is_saved: bool = True
    location: str | None
    source: str
    source_job_id: str | None
    url: str | None
    application_url: str | None = None
    description: str
    skills: list[str]
    nice_to_have_skills: list[str] = Field(default_factory=list)
    employment_types: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    duplicate_group_id: str | None = None
    duplicate_status: DuplicateStatus | None = None
    duplicate_confidence: Literal["medium", "high"] | None = None
    potential_duplicate_of: str | None = None
    available_sources: list[Literal["linkedin", "indeed", "glassdoor", "jobicy", "arbeitnow"]] = Field(
        default_factory=list
    )
    source_listings: list[JobSourceReference] = Field(default_factory=list)
    original_published_at: datetime | None = None
    latest_repost_at: datetime | None = None
    repost_count: int = 0
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


class JobSavedUpdate(BaseModel):
    saved: bool
    candidate_id: str | None = None


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
    source: Literal["linkedin", "indeed", "glassdoor", "jobicy", "arbeitnow"] | None = None
    source_job_id: str
    title: str
    company: str
    company_logo_url: HttpUrl | None = None
    location: str | None = None
    url: HttpUrl
    application_url: HttpUrl | None = None
    description: str = ""
    skills: list[str] = Field(default_factory=list)
    nice_to_have_skills: list[str] = Field(default_factory=list)
    employment_types: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    duplicate_group_id: str | None = None
    duplicate_status: DuplicateStatus | None = None
    duplicate_confidence: Literal["medium", "high"] | None = None
    already_applied: bool = False
    applied_application_id: str | None = None
    applied_at: datetime | None = None
    available_sources: list[Literal["linkedin", "indeed", "glassdoor", "jobicy", "arbeitnow"]] = Field(
        default_factory=list
    )
    source_listings: list[JobSourceReference] = Field(default_factory=list)
    original_published_at: datetime | None = None
    latest_repost_at: datetime | None = None
    repost_count: int = 0
    potential_duplicate_of: str | None = None
    work_arrangement: str = ""
    experience_level: str = ""
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str = ""
    salary_period: str = "year"
    match_score: float = 0
    match_reasons: list[str] = Field(default_factory=list)
    match_confidence: Literal["", "low", "medium", "high"] = ""
    match_breakdown: list[MatchFactor] = Field(default_factory=list)


class JobSpyDiscoveryResponse(BaseModel):
    source: Literal[
        "linkedin",
        "indeed",
        "glassdoor",
        "jobicy",
    ]
    results: list[JobDiscoveryListing]


class JobicyDiscoveryResponse(BaseModel):
    source: Literal["jobicy"]
    geo: Literal[
        "apac", "europe", "usa", "canada", "uk",
        "albania", "andorra", "austria", "belarus", "belgium",
        "bosnia-and-herzegovina", "bulgaria", "croatia", "cyprus", "czechia",
        "denmark", "estonia", "finland", "france", "germany", "greece", "hungary",
        "iceland", "ireland", "italy", "kosovo", "latvia", "liechtenstein",
        "lithuania", "luxembourg", "malta", "moldova", "monaco", "montenegro",
        "netherlands", "north-macedonia", "norway", "poland", "portugal",
        "romania", "san-marino", "serbia", "slovakia", "slovenia", "spain",
        "sweden", "switzerland", "ukraine",
    ]
    results: list[JobDiscoveryListing]
    has_more: bool
    next_cursor: str | None = None


class JobRecommendation(JobDiscoveryListing):
    source: Literal["linkedin", "indeed", "glassdoor", "jobicy", "arbeitnow"]


class MultiSourceJobDiscoveryResponse(BaseModel):
    source: Literal["ict_jobs", "all"]
    results: list[JobRecommendation]
    source_failures: list[str] = Field(default_factory=list)
    successful_sources: list[str] = Field(default_factory=list)
    notice: str | None = None


class JobRecommendationsResponse(BaseModel):
    candidate_id: str
    results: list[JobRecommendation]
    source_failures: list[str] = Field(default_factory=list)
    successful_sources: list[str] = Field(default_factory=list)
    updated_at: datetime


class TrendingJob(BaseModel):
    source: Literal["linkedin", "indeed", "glassdoor", "jobicy", "arbeitnow"]
    source_job_id: str
    title: str
    company: str
    company_logo_url: HttpUrl | None = None
    location: str | None = None
    url: HttpUrl
    application_url: HttpUrl | None = None
    employment_types: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    duplicate_group_id: str | None = None
    duplicate_status: DuplicateStatus | None = None
    duplicate_confidence: Literal["medium", "high"] | None = None
    available_sources: list[Literal["linkedin", "indeed", "glassdoor", "jobicy", "arbeitnow"]] = Field(
        default_factory=list
    )
    source_listings: list[JobSourceReference] = Field(default_factory=list)
    original_published_at: datetime | None = None
    latest_repost_at: datetime | None = None
    repost_count: int = 0
    potential_duplicate_of: str | None = None
    work_arrangement: str = ""
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str = ""
    salary_period: str = "year"


class TrendingJobsResponse(BaseModel):
    results: list[TrendingJob]
    source_failures: list[str] = Field(default_factory=list)
    successful_sources: list[str] = Field(default_factory=list)
    updated_at: datetime


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


class JobDescriptionSummary(BaseModel):
    summary: str = Field(min_length=1, max_length=1200)


class CoverLetterPdfRequest(BaseModel):
    cover_letter: str = Field(min_length=1, max_length=8000)

    @field_validator("cover_letter")
    @classmethod
    def _require_non_whitespace_letter(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Cover-letter text cannot be blank.")
        return value


class ResumePdfRequest(BaseModel):
    materials: JobApplicationMaterials


class ApplicationCreate(BaseModel):
    job_id: str
    status: str = "saved"
    notes: str = ""
    candidate_id: str | None = None


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


ApplyStatus = Literal[
    "not_applied",
    "applied",
    "interview",
    "assessment",
    "rejected",
    "offer_received",
    "withdrawn",
]


class ApplicationApplyUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    status: ApplyStatus
    notes: str = Field(default="", max_length=5000)
    apply_documents: JobApplicationMaterials | None = None


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
    apply_status: ApplyStatus = "not_applied"
    apply_status_history: list[StatusHistoryEntry] = Field(default_factory=list)
    applied_at: datetime | None = None
    apply_updated_at: datetime | None = None
    apply_documents: JobApplicationMaterials | None = None
    candidate_id: str | None = None


class AdminApplicationRead(BaseModel):
    id: str
    recruiter_uid: str
    recruiter_name: str
    recruiter_email: str
    candidate_id: str | None = None
    candidate_name: str
    job_title: str
    company: str
    apply_status: ApplyStatus = "not_applied"
    applied_at: datetime | None = None
    updated_at: datetime


class ResumeEntry(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def _normalize_resume_entry(cls, value):
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for name, field in cls.model_fields.items():
            if name not in normalized:
                continue
            item = normalized[name]
            annotation = field.annotation
            if annotation is str:
                if item is None:
                    normalized[name] = ""
                elif isinstance(item, (int, float)):
                    normalized[name] = str(item)
                elif isinstance(item, list):
                    normalized[name] = ", ".join(
                        str(part)
                        for part in item
                        if isinstance(part, (str, int, float))
                    )
                elif not isinstance(item, str):
                    normalized[name] = ""
            elif get_origin(annotation) is list:
                if item is None:
                    normalized[name] = []
                elif not isinstance(item, list):
                    normalized[name] = (
                        [item] if isinstance(item, (str, int, float)) else []
                    )
                else:
                    normalized[name] = [
                        str(element) if isinstance(element, (int, float)) else element
                        for element in item
                        if isinstance(element, (str, int, float))
                    ]
        return normalized


class WorkExperience(ResumeEntry):
    title: str = Field(default="", max_length=200)
    company: str = Field(default="", max_length=200)
    employment_type: str = Field(default="", max_length=100)
    duration: str = Field(default="", max_length=100)
    start_date: str = Field(default="", max_length=100)
    end_date: str = Field(default="", max_length=100)
    currently_working: bool = False
    location: str = Field(default="", max_length=150)
    description: str = Field(default="", max_length=4000)
    responsibilities: list[str] = Field(default_factory=list, max_length=50)
    achievements: list[str] = Field(default_factory=list, max_length=50)
    technologies: list[str] = Field(default_factory=list, max_length=100)


class ProjectEntry(ResumeEntry):
    name: str = Field(default="", max_length=200)
    role: str = Field(default="", max_length=200)
    duration: str = Field(default="", max_length=100)
    technologies: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=4000)
    responsibilities: list[str] = Field(default_factory=list, max_length=50)
    url: str = Field(default="", max_length=500)
    github_url: str = Field(default="", max_length=500)
    start_date: str = Field(default="", max_length=100)
    end_date: str = Field(default="", max_length=100)


class EducationEntry(ResumeEntry):
    institution: str = Field(default="", max_length=250)
    degree: str = Field(default="", max_length=250)
    duration: str = Field(default="", max_length=100)
    grade: str = Field(default="", max_length=50)
    university: str = Field(default="", max_length=250)
    location: str = Field(default="", max_length=150)
    start_year: str = Field(default="", max_length=20)
    graduation_year: str = Field(default="", max_length=20)
    details: str = Field(default="", max_length=2000)


class CertificationEntry(ResumeEntry):
    name: str = Field(default="", max_length=250)
    issuer: str = Field(default="", max_length=250)
    issue_date: str = Field(default="", max_length=100)
    expiry_date: str = Field(default="", max_length=100)
    credential_id: str = Field(default="", max_length=200)
    credential_url: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=2000)


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
    work_experience: list[WorkExperience] = Field(default_factory=list, max_length=50)
    projects: list[ProjectEntry] = Field(default_factory=list, max_length=50)
    education: list[EducationEntry] = Field(default_factory=list, max_length=25)
    certifications: list[CertificationEntry] = Field(default_factory=list, max_length=50)
    achievements: list[str] = Field(default_factory=list, max_length=100)


class ProfileRead(ProfileUpdate):
    id: str


CandidateStatus = Literal[
    "active", "on_bench", "under_review", "temporarily_unavailable", "placed", "not_set"
]


class CandidateCreate(ProfileUpdate):
    """A recruiter-managed candidate profile; unlike ProfileUpdate, fields may be incomplete."""

    status: CandidateStatus = "under_review"
    availability: str = Field(default="", max_length=100)
    preferred_location: str = Field(default="", max_length=150)
    experience_years: float | None = Field(default=None, ge=0, le=80)
    alternate_phone: str = Field(default="", max_length=40)
    country: str = Field(default="", max_length=100)
    personal_website: str = Field(default="", max_length=500)
    other_profile_links: list[str] = Field(default_factory=list, max_length=30)
    professional_headline: str = Field(default="", max_length=300)
    current_job_title: str = Field(default="", max_length=200)
    relevant_experience_years: float | None = Field(default=None, ge=0, le=80)
    current_company: str = Field(default="", max_length=200)
    employment_status: str = Field(default="", max_length=100)
    seniority_level: str = Field(default="", max_length=100)
    primary_specialization: str = Field(default="", max_length=200)
    industry_experience: list[str] = Field(default_factory=list, max_length=30)
    skill_categories: dict[str, list[str]] = Field(default_factory=dict)
    programming_languages: list[str] = Field(default_factory=list, max_length=100)
    frameworks: list[str] = Field(default_factory=list, max_length=100)
    cloud_platforms: list[str] = Field(default_factory=list, max_length=100)
    devops_tools: list[str] = Field(default_factory=list, max_length=100)
    databases: list[str] = Field(default_factory=list, max_length=100)
    operating_systems: list[str] = Field(default_factory=list, max_length=100)
    networking_skills: list[str] = Field(default_factory=list, max_length=100)
    security_tools: list[str] = Field(default_factory=list, max_length=100)
    monitoring_tools: list[str] = Field(default_factory=list, max_length=100)
    soft_skills: list[str] = Field(default_factory=list, max_length=100)
    training: list[str] = Field(default_factory=list, max_length=100)
    publications: list[str] = Field(default_factory=list, max_length=50)
    memberships: list[str] = Field(default_factory=list, max_length=50)
    internal_tags: list[str] = Field(default_factory=list, max_length=50)
    notice_period: str = Field(default="", max_length=100)
    available_from: str = Field(default="", max_length=100)
    willing_to_relocate: bool | None = None
    work_arrangement_preference: str = Field(default="", max_length=50)
    employment_preference: str = Field(default="", max_length=200)
    expected_salary: float | None = Field(default=None, ge=0)
    salary_currency: str = Field(default="", max_length=10)
    recruiter_notes: str = Field(default="", max_length=10000)

    @model_validator(mode="before")
    @classmethod
    def _normalize_candidate_collections(cls, value):
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        list_fields = (
            "target_roles",
            "skills",
            "achievements",
            "industry_experience",
            "programming_languages",
            "frameworks",
            "cloud_platforms",
            "devops_tools",
            "databases",
            "operating_systems",
            "networking_skills",
            "security_tools",
            "monitoring_tools",
            "soft_skills",
            "training",
            "publications",
            "memberships",
            "internal_tags",
            "other_profile_links",
        )
        for name in list_fields:
            item = normalized.get(name)
            if item is None:
                normalized[name] = []
            elif not isinstance(item, list):
                normalized[name] = [item]
            normalized[name] = [
                str(element) if isinstance(element, (int, float)) else element
                for element in normalized[name]
                if isinstance(element, (str, int, float))
            ]

        categories = normalized.get("skill_categories")
        if categories is None:
            normalized["skill_categories"] = {}
        elif isinstance(categories, dict):
            normalized["skill_categories"] = {
                key: [
                    str(item) if isinstance(item, (int, float)) else item
                    for item in (value if isinstance(value, list) else [value])
                    if isinstance(item, (str, int, float))
                ]
                for key, value in categories.items()
                if isinstance(key, str)
            }

        for name in ("work_experience", "projects", "education", "certifications"):
            item = normalized.get(name)
            if isinstance(item, dict):
                normalized[name] = [item]
            elif item is None:
                normalized[name] = []
        return normalized

    @model_validator(mode="before")
    @classmethod
    def _normalize_candidate_scalars(cls, value):
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for name, field in cls.model_fields.items():
            item = normalized.get(name)
            if field.annotation is str:
                if item is None:
                    normalized[name] = ""
                elif isinstance(item, (int, float)):
                    normalized[name] = str(item)
                elif isinstance(item, list):
                    normalized[name] = ", ".join(
                        str(part)
                        for part in item
                        if isinstance(part, (str, int, float))
                    )
                elif not isinstance(item, str):
                    normalized[name] = ""
            elif name in {"experience_years", "relevant_experience_years", "expected_salary"}:
                if isinstance(item, str):
                    try:
                        normalized[name] = float(item) if item.strip() else None
                    except ValueError:
                        normalized[name] = None
            elif name == "willing_to_relocate" and isinstance(item, str):
                answer = item.strip().casefold()
                if answer in {"true", "yes", "1"}:
                    normalized[name] = True
                elif answer in {"false", "no", "0"}:
                    normalized[name] = False
                elif not answer:
                    normalized[name] = None
        return normalized

    @field_validator("full_name")
    @classmethod
    def _candidate_name_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Candidate name is required.")
        return value.strip()


class CandidateStatusUpdate(BaseModel):
    status: CandidateStatus


class CandidateNotesUpdate(BaseModel):
    notes: str = Field(max_length=10000)


class CandidateArchiveUpdate(BaseModel):
    archived: bool


class CandidateApplicationCreate(BaseModel):
    job_id: str
    status: Literal["discovered", "saved"] = "saved"


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
        if not (w.title and w.company and w.description):
            problems.append(
                f"Work experience #{i}: job title, company and description are required"
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
    candidate_id: str | None = None
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