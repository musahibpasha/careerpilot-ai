import { useEffect, useMemo, useRef, useState } from "react";
import {
  Archive,
  BriefcaseBusiness,
  Check,
  Clock3,
  Download,
  Eye,
  FileUp,
  FileText,
  GraduationCap,
  MapPin,
  Pencil,
  Phone,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  UserRound,
  X,
} from "lucide-react";
import type { FormEvent, ReactNode } from "react";
import { apiFetch } from "./api";

const API = import.meta.env.VITE_API_URL || (import.meta.env.DEV ? "http://127.0.0.1:8000" : "");

type CandidateStatus = "active" | "on_bench" | "under_review" | "temporarily_unavailable" | "placed" | "not_set";
type EditableEntry = Record<string, string | boolean | string[]>;
type CandidateExperience = {
  title: string;
  company: string;
  employment_type: string;
  duration: string;
  start_date: string;
  end_date: string;
  currently_working: boolean;
  location: string;
  description: string;
  responsibilities: string[];
  achievements: string[];
  technologies: string[];
} & EditableEntry;
type CandidateEducation = {
  institution: string;
  university: string;
  degree: string;
  duration: string;
  start_year: string;
  graduation_year: string;
  location: string;
  grade: string;
  details: string;
} & EditableEntry;
type CandidateProject = {
  name: string;
  role: string;
  duration: string;
  technologies: string;
  description: string;
  responsibilities: string[];
  url: string;
  github_url: string;
  start_date: string;
  end_date: string;
} & EditableEntry;
type CandidateCertification = {
  name: string;
  issuer: string;
  issue_date: string;
  expiry_date: string;
  credential_id: string;
  credential_url: string;
  description: string;
} & EditableEntry;
type CandidateProfileDetails = {
  work_experience: CandidateExperience[];
  education: CandidateEducation[];
  certifications: CandidateCertification[];
  projects: CandidateProject[];
  achievements: string[];
  alternate_phone: string;
  country: string;
  personal_website: string;
  other_profile_links: string[];
  professional_headline: string;
  current_job_title: string;
  relevant_experience_years: number | null;
  current_company: string;
  employment_status: string;
  seniority_level: string;
  primary_specialization: string;
  industry_experience: string[];
  skill_categories: Record<string, string[]>;
  programming_languages: string[];
  frameworks: string[];
  cloud_platforms: string[];
  devops_tools: string[];
  databases: string[];
  operating_systems: string[];
  networking_skills: string[];
  security_tools: string[];
  monitoring_tools: string[];
  soft_skills: string[];
  training: string[];
  publications: string[];
  memberships: string[];
  internal_tags: string[];
  notice_period: string;
  available_from: string;
  willing_to_relocate: boolean | null;
  work_arrangement_preference: string;
  employment_preference: string;
  expected_salary: number | null;
  salary_currency: string;
  recruiter_notes: string;
};
type ExtractedResume = Partial<CandidateProfileDetails> & {
  full_name?: string;
  email?: string;
  phone?: string;
  location?: string;
  experience_years?: number | null;
  github?: string;
  linkedin?: string;
  portfolio?: string;
  target_roles?: string[];
  skills?: string[];
  experience_summary?: string;
  work_experience?: CandidateExperience[];
  education?: CandidateEducation[];
  certifications?: CandidateCertification[];
  projects?: CandidateProject[];
  achievements?: string[];
};
type CandidateResume = {
  id: string;
  label: string;
  filename: string;
  size: number;
  page_count: number;
  is_active: boolean;
  uploaded_at: string;
  extracted_at: string | null;
  extracted?: ExtractedResume | null;
};
type Candidate = {
  id: string;
  full_name: string;
  email: string;
  phone: string;
  location: string;
  github: string;
  linkedin: string;
  portfolio: string;
  target_roles: string[];
  skills: string[];
  experience_summary: string;
  work_experience: CandidateExperience[];
  projects: CandidateProject[];
  education: CandidateEducation[];
  certifications: CandidateCertification[];
  achievements: string[];
  status: CandidateStatus;
  availability: string;
  preferred_location: string;
  experience_years: number | null;
  notes: string;
  archived: boolean;
  created_at: string;
  updated_at: string;
  resume_count: number;
  resume_id: string | null;
  resume_filename: string;
  resume_extracted: ExtractedResume | null;
} & CandidateProfileDetails;
type CandidateApplication = {
  id: string;
  job_id: string;
  job_title: string;
  company: string;
  location: string | null;
  status: string;
  apply_status: string;
  application_date: string | null;
  tracked_at: string;
  url: string | null;
};
type CandidateDetail = Candidate & {
  resumes: CandidateResume[];
  applications: CandidateApplication[];
};
type CandidateForm = {
  full_name: string;
  role: string;
  email: string;
  phone: string;
  location: string;
  skills: string;
  experience_years: string;
  availability: string;
  preferred_location: string;
  status: CandidateStatus;
  experience_summary: string;
};
type ResumeParseResponse = {
  extracted: ExtractedResume;
  requires_confirmation: boolean;
};
type CandidateListResponse = {
  items: Candidate[];
  summary: {
    total: number;
    active: number;
    on_bench: number;
    under_review: number;
    resumes_uploaded: number;
    archived: number;
  };
};
type SavedJob = { id: string; title: string; company: string; location: string | null };
type CandidateTab = "overview" | "resume" | "jobs" | "notes";

const STATUS_LABELS: Record<CandidateStatus, string> = {
  active: "Available",
  on_bench: "On Bench",
  under_review: "Under Review",
  temporarily_unavailable: "Temporarily Unavailable",
  placed: "Placed",
  not_set: "Not set",
};

const EMPTY_SUMMARY = {
  total: 0,
  active: 0,
  on_bench: 0,
  under_review: 0,
  resumes_uploaded: 0,
  archived: 0,
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(`${API}${path}`, init);
  if (!response.ok) {
    let detail = `Request failed (HTTP ${response.status}).`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (Array.isArray(body?.detail)) {
        const issues = body.detail
          .slice(0, 5)
          .map((issue: { loc?: unknown[]; msg?: unknown }) => {
            const field = Array.isArray(issue.loc)
              ? issue.loc.filter((part) => part !== "body").join(".")
              : "Request";
            return `${field}: ${typeof issue.msg === "string" ? issue.msg : "invalid value"}`;
          });
        if (issues.length) detail = issues.join("; ");
      }
    } catch {
      // The response can be empty for infrastructure errors.
    }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function initials(name: string): string {
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("") || "?";
}

function displayDate(value: string | null | undefined): string {
  if (!value) return "Not provided";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Not provided"
    : date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function candidateRole(candidate: Candidate): string {
  return candidate.professional_headline || candidate.current_job_title ||
    candidate.target_roles?.[0] || candidate.work_experience?.[0]?.title || "Role not provided";
}

function allCandidateSkills(candidate: Candidate): string[] {
  return [...new Set([...(candidate.skills ?? []), ...Object.values(candidate.skill_categories ?? {}).flat()])];
}

function splitList(value: string): string[] {
  return value.split(",").map((part) => part.trim()).filter(Boolean);
}

function emptyForm(): CandidateForm {
  return {
    full_name: "",
    role: "",
    email: "",
    phone: "",
    location: "",
    skills: "",
    experience_years: "",
    availability: "",
    preferred_location: "",
    status: "under_review",
    experience_summary: "",
  };
}

function emptyProfileDetails(): CandidateProfileDetails {
  return {
    work_experience: [], education: [], certifications: [], projects: [], achievements: [],
    alternate_phone: "", country: "", personal_website: "", other_profile_links: [],
    professional_headline: "", current_job_title: "", relevant_experience_years: null,
    current_company: "", employment_status: "", seniority_level: "",
    primary_specialization: "", industry_experience: [], skill_categories: {},
    programming_languages: [], frameworks: [], cloud_platforms: [], devops_tools: [],
    databases: [], operating_systems: [], networking_skills: [], security_tools: [],
    monitoring_tools: [], soft_skills: [], training: [], publications: [],
    memberships: [], internal_tags: [], notice_period: "", available_from: "",
    willing_to_relocate: null, work_arrangement_preference: "",
    employment_preference: "", expected_salary: null, salary_currency: "",
    recruiter_notes: "",
  };
}

function formFromCandidate(candidate: Candidate): CandidateForm {
  return {
    full_name: candidate.full_name,
    role: candidate.target_roles?.join(", ") ?? "",
    email: candidate.email,
    phone: candidate.phone,
    location: candidate.location,
    skills: candidate.skills?.join(", ") ?? "",
    experience_years: candidate.experience_years == null ? "" : String(candidate.experience_years),
    availability: candidate.availability,
    preferred_location: candidate.preferred_location,
    status: candidate.status,
    experience_summary: candidate.experience_summary,
  };
}

function experienceLabel(candidate: Candidate): string {
  return candidate.experience_years == null
    ? "Not provided"
    : `${candidate.experience_years} ${candidate.experience_years === 1 ? "year" : "years"}`;
}

export default function BenchCandidates() {
  const [items, setItems] = useState<Candidate[]>([]);
  const [summary, setSummary] = useState(EMPTY_SUMMARY);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [search, setSearch] = useState("");
  const [skillCriteria, setSkillCriteria] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [experienceFilter, setExperienceFilter] = useState("all");
  const [locationFilter, setLocationFilter] = useState("all");
  const [availabilityFilter, setAvailabilityFilter] = useState("all");
  const [candidateId, setCandidateId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CandidateDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [activeTab, setActiveTab] = useState<CandidateTab>("overview");
  const [resumeId, setResumeId] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [resumeBusy, setResumeBusy] = useState(false);
  const [resumeError, setResumeError] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [editingCandidate, setEditingCandidate] = useState<Candidate | null>(null);
  const [form, setForm] = useState<CandidateForm>(emptyForm());
  const [profileDetails, setProfileDetails] = useState<CandidateProfileDetails>(emptyProfileDetails());
  const [formBusy, setFormBusy] = useState(false);
  const [formError, setFormError] = useState("");
  const [candidateResumeFile, setCandidateResumeFile] = useState<File | null>(null);
  const [resumeScanBusy, setResumeScanBusy] = useState(false);
  const [resumeScanError, setResumeScanError] = useState("");
  const [resumeScanMessage, setResumeScanMessage] = useState("");
  const [resumeExtracted, setResumeExtracted] = useState<ExtractedResume | null>(null);
  const resumeScanRequestId = useRef(0);
  const [notesDraft, setNotesDraft] = useState("");
  const [notesBusy, setNotesBusy] = useState(false);
  const [notesMessage, setNotesMessage] = useState("");
  const [actionError, setActionError] = useState("");
  const [jobs, setJobs] = useState<SavedJob[]>([]);
  const [jobsError, setJobsError] = useState("");
  const [selectedJobId, setSelectedJobId] = useState("");
  const [jobBusy, setJobBusy] = useState(false);

  async function loadCandidates(showLoading = true) {
    if (showLoading) setLoading(true);
    setListError("");
    try {
      const result = await request<CandidateListResponse>(
        `/api/candidates?include_archived=${includeArchived}`,
      );
      setItems(result.items);
      setSummary(result.summary);
    } catch (error) {
      setListError(error instanceof Error ? error.message : "Could not load candidates.");
    } finally {
      if (showLoading) setLoading(false);
    }
  }

  async function loadDetail(id: string) {
    setDetailLoading(true);
    setDetailError("");
    try {
      const result = await request<CandidateDetail>(`/api/candidates/${id}`);
      setDetail(result);
      setNotesDraft(result.notes ?? "");
      setResumeId((current) =>
        current && result.resumes.some((resume) => resume.id === current)
          ? current
          : result.resumes[0]?.id ?? null,
      );
    } catch (error) {
      setDetailError(error instanceof Error ? error.message : "Could not load candidate details.");
    } finally {
      setDetailLoading(false);
    }
  }

  async function loadSavedJobs() {
    setJobsError("");
    try {
      setJobs(await request<SavedJob[]>("/api/jobs/saved"));
    } catch (error) {
      setJobsError(error instanceof Error ? error.message : "Could not load saved jobs.");
    }
  }

  useEffect(() => {
    void loadCandidates();
  }, [includeArchived]);

  useEffect(() => {
    if (candidateId) void loadDetail(candidateId);
    else {
      setDetail(null);
      setPreviewUrl("");
    }
  }, [candidateId]);

  useEffect(() => {
    if (candidateId) return;
    void loadSavedJobs();
  }, [candidateId]);

  useEffect(() => {
    const activeResume = detail?.resumes.find((resume) => resume.id === resumeId);
    if (
      !activeResume ||
      activeTab !== "resume" ||
      activeResume.filename.toLocaleLowerCase().endsWith(".docx")
    ) {
      setPreviewUrl("");
      return;
    }
    let cancelled = false;
    let objectUrl = "";
    void apiFetch(`${API}/api/resumes/${activeResume.id}/file`)
      .then(async (response) => {
        if (!response.ok) throw new Error(`Resume preview failed (HTTP ${response.status}).`);
        return response.blob();
      })
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setPreviewUrl(objectUrl);
      })
      .catch((error: unknown) => {
        if (!cancelled) setResumeError(error instanceof Error ? error.message : "Resume preview failed.");
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [detail, resumeId, activeTab]);

  const skillTerms = useMemo(
    () => [...new Set(splitList(skillCriteria).map((skill) => skill.toLocaleLowerCase()))],
    [skillCriteria],
  );
  const locations = useMemo(
    () => [...new Set(items.map((item) => item.location.trim()).filter(Boolean))].sort(),
    [items],
  );
  const availabilityOptions = useMemo(
    () => [...new Set(items.map((item) => item.availability.trim()).filter(Boolean))].sort(),
    [items],
  );
  const filtered = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    return items.filter((candidate) => {
      const searchable = [
        candidate.full_name,
        candidate.email,
        candidate.location,
        ...allCandidateSkills(candidate),
        ...candidate.target_roles,
      ].join(" ").toLocaleLowerCase();
      if (query && !searchable.includes(query)) return false;
      if (statusFilter !== "all" && candidate.status !== statusFilter) return false;
      if (locationFilter !== "all" && candidate.location !== locationFilter) return false;
      if (availabilityFilter !== "all" && candidate.availability !== availabilityFilter) return false;
      if (experienceFilter === "under2" && (candidate.experience_years == null || candidate.experience_years >= 2)) return false;
      if (experienceFilter === "2to5" && (candidate.experience_years == null || candidate.experience_years < 2 || candidate.experience_years > 5)) return false;
      if (experienceFilter === "over5" && (candidate.experience_years == null || candidate.experience_years <= 5)) return false;
      return true;
    });
  }, [items, search, statusFilter, locationFilter, availabilityFilter, experienceFilter]);

  function openCreateForm() {
    setEditingCandidate(null);
    setForm(emptyForm());
    setProfileDetails(emptyProfileDetails());
    setFormError("");
    setCandidateResumeFile(null);
    setResumeExtracted(null);
    setResumeScanError("");
    setResumeScanMessage("");
    setFormOpen(true);
  }

  function openEditForm(candidate: Candidate) {
    setEditingCandidate(candidate);
    setForm(formFromCandidate(candidate));
    setProfileDetails({ ...emptyProfileDetails(), ...candidate });
    setFormError("");
    setCandidateResumeFile(null);
    setResumeExtracted(null);
    setResumeScanError("");
    setResumeScanMessage("");
    setFormOpen(true);
  }

  async function scanCandidateResume(file: File | undefined) {
    if (!file) return;
    const requestId = ++resumeScanRequestId.current;
    setResumeExtracted(null);
    setResumeScanError("");
    setResumeScanMessage("");
    const lowerName = file.name.toLocaleLowerCase();
    const allowedMimeTypes = [
      "",
      "application/pdf",
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ];
    if (!(lowerName.endsWith(".pdf") || lowerName.endsWith(".docx")) || !allowedMimeTypes.includes(file.type)) {
      setCandidateResumeFile(null);
      setResumeScanError("Choose a PDF or DOCX resume.");
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      setCandidateResumeFile(null);
      setResumeScanError("Resume must be 5 MB or smaller.");
      return;
    }

    setCandidateResumeFile(file);
    setResumeScanBusy(true);
    const formData = new FormData();
    formData.set("file", file);
    try {
      const result = await request<ResumeParseResponse>("/api/resume/parse", {
        method: "POST",
        body: formData,
      });
      if (requestId !== resumeScanRequestId.current) return;
      const extracted = result.extracted ?? {};
      setResumeExtracted(extracted);
      setProfileDetails((current) => ({ ...current, ...extracted }));
      setForm((current) => ({
        ...current,
        full_name: extracted.full_name || current.full_name,
        role: extracted.target_roles?.length
          ? extracted.target_roles.join(", ")
          : extracted.current_job_title || extracted.professional_headline || current.role,
        email: extracted.email || current.email,
        phone: extracted.phone || current.phone,
        location: extracted.location || current.location,
        skills: extracted.skills?.length ? extracted.skills.join(", ") : current.skills,
        experience_summary: extracted.experience_summary || current.experience_summary,
        experience_years: extracted.experience_years == null
          ? current.experience_years
          : String(extracted.experience_years),
      }));
      setResumeScanMessage("Resume scanned. Review the extracted fields before saving.");
    } catch (error) {
      if (requestId === resumeScanRequestId.current) {
        setResumeScanError(
          error instanceof Error ? error.message : "Could not scan this resume.",
        );
      }
    } finally {
      if (requestId === resumeScanRequestId.current) setResumeScanBusy(false);
    }
  }

  async function saveCandidate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormBusy(true);
    setFormError("");
    const extractedProfile = resumeExtracted ?? editingCandidate;
    const payload = {
      ...(editingCandidate ?? {
        github: "",
        linkedin: "",
        portfolio: "",
        work_experience: [],
        projects: [],
        education: [],
        certifications: [],
        achievements: [],
      }),
      ...profileDetails,
      github: extractedProfile?.github ?? editingCandidate?.github ?? "",
      linkedin: extractedProfile?.linkedin ?? editingCandidate?.linkedin ?? "",
      portfolio: extractedProfile?.portfolio ?? editingCandidate?.portfolio ?? "",
      full_name: form.full_name.trim(),
      email: form.email.trim(),
      phone: form.phone.trim(),
      location: form.location.trim(),
      target_roles: splitList(form.role),
      skills: splitList(form.skills),
      experience_summary: form.experience_summary.trim(),
      status: form.status,
      availability: form.availability.trim(),
      preferred_location: form.preferred_location.trim(),
      experience_years: form.experience_years ? Number(form.experience_years) : null,
    };
    try {
      const saved = await request<Candidate>(
        editingCandidate ? `/api/candidates/${editingCandidate.id}` : "/api/candidates",
        {
          method: editingCandidate ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
      );
      if (candidateResumeFile) {
        const resumeForm = new FormData();
        resumeForm.set("file", candidateResumeFile);
        resumeForm.set("candidate_id", saved.id);
        const labelStem = candidateResumeFile.name.replace(/\.(pdf|docx)$/i, "").trim();
        resumeForm.set(
          "label",
          `${form.full_name.trim()} - ${labelStem} (${saved.id.slice(-6)})`,
        );
        if (resumeExtracted) {
          resumeForm.set("extracted_data", JSON.stringify(resumeExtracted));
        }
        try {
          await request("/api/resumes", { method: "POST", body: resumeForm });
        } catch (error) {
          setEditingCandidate(saved);
          setFormError(
            `Candidate details were saved, but the resume could not be attached: ${
              error instanceof Error ? error.message : "Upload failed."
            } Select Save again to retry attaching the resume.`,
          );
          await loadCandidates(false);
          return;
        }
      }
      setFormOpen(false);
      setCandidateResumeFile(null);
      setResumeExtracted(null);
      setCandidateId(saved.id);
      await loadCandidates(false);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Could not save candidate.");
    } finally {
      setFormBusy(false);
    }
  }

  async function updateStatus(status: CandidateStatus) {
    if (!candidateId) return;
    setActionError("");
    try {
      await request(`/api/candidates/${candidateId}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      await Promise.all([loadCandidates(false), loadDetail(candidateId)]);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not update candidate status.");
    }
  }

  async function archiveCandidate(archived: boolean) {
    if (!detail) return;
    const verb = archived ? "archive" : "restore";
    if (!window.confirm(`${archived ? "Archive" : "Restore"} ${detail.full_name}?`)) return;
    setActionError("");
    try {
      await request(`/api/candidates/${detail.id}/archive`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ archived }),
      });
      await loadCandidates(false);
      if (archived && !includeArchived) setCandidateId(null);
      else await loadDetail(detail.id);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : `Could not ${verb} candidate.`);
    }
  }

  async function toggleRowArchive(candidate: Candidate) {
    const archived = !candidate.archived;
    if (!window.confirm(`${archived ? "Archive" : "Restore"} ${candidate.full_name}?`)) return;
    setActionError("");
    try {
      await request(`/api/candidates/${candidate.id}/archive`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ archived }),
      });
      await loadCandidates(false);
      if (candidateId === candidate.id) {
        if (archived && !includeArchived) setCandidateId(null);
        else await loadDetail(candidate.id);
      }
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not update archive state.");
    }
  }

  async function saveNotes(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail) return;
    setNotesBusy(true);
    setNotesMessage("");
    try {
      await request(`/api/candidates/${detail.id}/notes`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ notes: notesDraft }),
      });
      setNotesMessage("Notes saved.");
      await loadCandidates(false);
      await loadDetail(detail.id);
    } catch (error) {
      setNotesMessage(error instanceof Error ? error.message : "Could not save notes.");
    } finally {
      setNotesBusy(false);
    }
  }

  async function uploadResume(file: File | undefined) {
    if (!detail || !file) return;
    setResumeBusy(true);
    setResumeError("");
    try {
      const formData = new FormData();
      formData.set("file", file);
      formData.set("candidate_id", detail.id);
      await request("/api/resumes", { method: "POST", body: formData });
      await Promise.all([loadDetail(detail.id), loadCandidates(false)]);
      setActiveTab("resume");
    } catch (error) {
      setResumeError(error instanceof Error ? error.message : "Could not upload resume.");
    } finally {
      setResumeBusy(false);
    }
  }

  async function extractResume(id: string) {
    setResumeBusy(true);
    setResumeError("");
    try {
      await request(`/api/resumes/${id}/extract`, { method: "POST" });
      if (detail) {
        await Promise.all([loadDetail(detail.id), loadCandidates(false)]);
      }
    } catch (error) {
      setResumeError(error instanceof Error ? error.message : "Could not extract resume details.");
    } finally {
      setResumeBusy(false);
    }
  }

  async function downloadResume(id: string, filename: string, open = false) {
    setResumeError("");
    const newWindow = open ? window.open("about:blank", "_blank") : null;
    if (open && !newWindow) {
      setResumeError("Allow pop-ups to view the resume in a new tab.");
      return;
    }
    try {
      const response = await apiFetch(`${API}/api/resumes/${id}/file`);
      if (!response.ok) throw new Error(`Resume download failed (HTTP ${response.status}).`);
      const url = URL.createObjectURL(await response.blob());
      if (open) {
        newWindow!.location.href = url;
      } else {
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = filename || "resume.pdf";
        anchor.click();
      }
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (error) {
      newWindow?.close();
      setResumeError(error instanceof Error ? error.message : "Could not download resume.");
    }
  }

  async function trackJob(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail || !selectedJobId) return;
    setJobBusy(true);
    setActionError("");
    try {
      await request(`/api/candidates/${detail.id}/applications`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: selectedJobId, status: "saved" }),
      });
      setSelectedJobId("");
      await loadDetail(detail.id);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not track job for candidate.");
    } finally {
      setJobBusy(false);
    }
  }

  const selectedResume = detail?.resumes.find((resume) => resume.id === resumeId) ?? null;
  const extracted = activeTab === "resume" && selectedResume
    ? selectedResume.extracted ?? null
    : detail?.resume_extracted ?? selectedResume?.extracted ?? null;
  const requestedSkills = skillTerms;

  return (
    <section className="bench-page page-view" aria-label="Bench Candidates">
      <div className="bench-toolbar">
        <div className="bench-toolbar-note">
          <span className="bench-page-mark"><UserRound size={20} /></span>
          <span>Candidate profiles and their linked resume and application records.</span>
        </div>
        <div className="bench-toolbar-actions">
          <label className="bench-archived-toggle">
            <input
              type="checkbox"
              checked={includeArchived}
              onChange={(event) => {
                setCandidateId(null);
                setIncludeArchived(event.target.checked);
              }}
            />
            Include archived
          </label>
          <button type="button" className="primary-button" onClick={openCreateForm}>
            <Plus size={16} /> Add candidate
          </button>
        </div>
      </div>

      <section className="bench-summary" aria-label="Candidate summary">
        <SummaryCard icon={<UserRound size={17} />} label="Total Candidates" value={summary.total} tone="violet" />
        <SummaryCard icon={<Check size={17} />} label="Active Candidates" value={summary.active} tone="green" />
        <SummaryCard icon={<BriefcaseBusiness size={17} />} label="On Bench" value={summary.on_bench} tone="amber" />
        <SummaryCard icon={<Clock3 size={17} />} label="Under Review" value={summary.under_review} tone="blue" />
        <SummaryCard icon={<FileText size={17} />} label="Resumes Uploaded" value={summary.resumes_uploaded} tone="violet" />
      </section>

      <section className="bench-filters panel" aria-label="Candidate search and filters">
        <label className="bench-search">
          <span className="sr-only">Search candidate name, skills, or location</span>
          <Search size={16} />
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search candidates by name, skills, location..."
          />
        </label>
        <label>
          <span className="sr-only">Required skills for match</span>
          <input
            type="search"
            value={skillCriteria}
            onChange={(event) => setSkillCriteria(event.target.value)}
            placeholder="Required skills"
            aria-label="Required skills for match and highlighting"
          />
        </label>
        <label>
          <span className="sr-only">Filter by status</span>
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="all">All statuses</option>
            {Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <label>
          <span className="sr-only">Filter by experience</span>
          <select value={experienceFilter} onChange={(event) => setExperienceFilter(event.target.value)}>
            <option value="all">All experience</option>
            <option value="under2">Under 2 years</option>
            <option value="2to5">2-5 years</option>
            <option value="over5">Over 5 years</option>
          </select>
        </label>
        <label>
          <span className="sr-only">Filter by location</span>
          <select value={locationFilter} onChange={(event) => setLocationFilter(event.target.value)}>
            <option value="all">All locations</option>
            {locations.map((location) => <option key={location} value={location}>{location}</option>)}
          </select>
        </label>
        <label>
          <span className="sr-only">Filter by availability</span>
          <select value={availabilityFilter} onChange={(event) => setAvailabilityFilter(event.target.value)}>
            <option value="all">All availability</option>
            {availabilityOptions.map((availability) => <option key={availability} value={availability}>{availability}</option>)}
          </select>
        </label>
      </section>

      {actionError && <p className="bench-inline-error" role="alert">{actionError}</p>}
      {listError && (
        <section className="bench-error panel" role="alert">
          <p>{listError}</p>
          <button type="button" className="secondary-button" onClick={() => void loadCandidates()}>
            <RefreshCw size={14} /> Try again
          </button>
        </section>
      )}
      {!listError && loading ? (
        <section className="bench-empty panel" role="status">Loading candidates...</section>
      ) : !listError && filtered.length === 0 ? (
        <section className="bench-empty panel">
          <span className="bench-empty-icon"><UserRound size={22} /></span>
          <h2>{items.length ? "No candidates match these filters" : "No candidates yet"}</h2>
          <p>
            {items.length
              ? "Try changing the search or filters."
              : "Add candidate profiles to manage resumes, availability, skills and applications in your authenticated workspace."}
          </p>
          {items.length ? (
            <button type="button" className="secondary-button" onClick={() => {
              setSearch("");
              setSkillCriteria("");
              setStatusFilter("all");
              setExperienceFilter("all");
              setLocationFilter("all");
              setAvailabilityFilter("all");
            }}>
              Clear filters
            </button>
          ) : (
            <div className="bench-empty-actions">
              <button type="button" className="primary-button" onClick={openCreateForm}><Plus size={15} /> Add candidate</button>
            </div>
          )}
        </section>
      ) : !listError && (
        <section className="bench-table-wrap panel">
          <div className="bench-table-scroll">
            <table className="bench-table">
              <thead>
                <tr>
                  <th>Candidate</th>
                  <th>Skills</th>
                  <th>Experience</th>
                  <th>Location</th>
                  <th>Availability</th>
                  <th>Status</th>
                  <th>Match</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((candidate) => {
                  const candidateSkills = allCandidateSkills(candidate);
                  const matchedSkills = candidateSkills.filter((skill) =>
                    requestedSkills.some((required) => skill.toLocaleLowerCase() === required),
                  );
                  const match = requestedSkills.length > 0 && candidateSkills.length > 0
                    ? Math.round((matchedSkills.length / requestedSkills.length) * 100)
                    : null;
                  return (
                    <tr key={candidate.id} className={candidate.id === candidateId ? "selected" : ""}>
                      <td>
                        <div className="bench-candidate-cell">
                          <span className="bench-avatar">{initials(candidate.full_name)}</span>
                          <span className="bench-candidate-copy">
                            <strong>{candidate.full_name}</strong>
                            <small>{candidateRole(candidate)}</small>
                          </span>
                        </div>
                      </td>
                      <td>
                        <div className="bench-skill-tags">
                          {candidateSkills.length ? candidateSkills.slice(0, 5).map((skill) => {
                            const matched = requestedSkills.some((required) => skill.toLocaleLowerCase() === required);
                            return <span key={skill} className={matched ? "matched" : ""}>{skill}</span>;
                          }) : <span className="bench-muted">No skills provided</span>}
                          {candidateSkills.length > 5 && <small>+{candidateSkills.length - 5}</small>}
                        </div>
                      </td>
                      <td>{experienceLabel(candidate)}</td>
                      <td>{candidate.location || "Not provided"}</td>
                      <td>{candidate.availability || "Not provided"}</td>
                      <td><span className={`bench-status ${candidate.status}`}>{STATUS_LABELS[candidate.status]}</span></td>
                      <td>
                        {match == null
                          ? <span className="bench-muted">-</span>
                          : <span className="bench-match" title={`${matchedSkills.length} of ${requestedSkills.length} required skills matched`}>
                              {match}%<small>{matchedSkills.length}/{requestedSkills.length}</small>
                            </span>}
                      </td>
                      <td>
                        <div className="bench-row-actions">
                          <button type="button" aria-label={`View ${candidate.full_name}`} title="View details" onClick={() => {
                            setCandidateId(candidate.id);
                            setActiveTab("overview");
                            setActionError("");
                          }}><Eye size={16} /></button>
                          <button type="button" aria-label={`Edit ${candidate.full_name}`} title="Edit candidate" onClick={() => openEditForm(candidate)}><Pencil size={15} /></button>
                          {candidate.resume_id && (
                            <button type="button" aria-label={`Download ${candidate.full_name}'s resume`} title="Download resume" onClick={() => void downloadResume(candidate.resume_id!, candidate.resume_filename)}><Download size={15} /></button>
                          )}
                          <button type="button" aria-label={`${candidate.archived ? "Restore" : "Archive"} ${candidate.full_name}`} title={candidate.archived ? "Restore candidate" : "Archive candidate"} onClick={() => void toggleRowArchive(candidate)}><Archive size={15} /></button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <footer className="bench-table-footer">
            Showing {filtered.length} of {items.length} candidate{items.length === 1 ? "" : "s"}
            {requestedSkills.length > 0 && <span> · Skill matches are calculated from the entered required skills.</span>}
          </footer>
        </section>
      )}

      {candidateId && (
        <div className="bench-drawer-backdrop" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setCandidateId(null);
        }}>
          <aside className="bench-drawer" role="dialog" aria-modal="true" aria-label="Candidate details">
            <div className="bench-drawer-top">
              {detailLoading && !detail ? <p>Loading candidate details...</p> : detailError ? (
                <div className="bench-error-inline" role="alert">{detailError}</div>
              ) : detail && (
                <>
                  <div className="bench-drawer-identity">
                    <span className="bench-avatar large">{initials(detail.full_name)}</span>
                    <div>
                      <div className="bench-name-status">
                        <h2>{detail.full_name}</h2>
                        <span className={`bench-status ${detail.status}`}>{STATUS_LABELS[detail.status]}</span>
                      </div>
                      <p>{candidateRole(detail)}</p>
                    </div>
                  </div>
                  <button type="button" className="bench-close" aria-label="Close candidate details" onClick={() => setCandidateId(null)}><X size={18} /></button>
                  <div className="bench-contact-grid">
                    <span><MapPin size={14} />{detail.location || "Location not provided"}</span>
                    <span><BriefcaseBusiness size={14} />{experienceLabel(detail)}</span>
                    <span><span className="bench-contact-glyph">@</span>{detail.email || "Email not provided"}</span>
                    <span><Phone size={14} />{detail.phone || "Phone not provided"}</span>
                  </div>
                  <div className="bench-drawer-controls">
                    <label>
                      <span className="sr-only">Update bench status</span>
                      <select value={detail.status} onChange={(event) => void updateStatus(event.target.value as CandidateStatus)}>
                        {Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                      </select>
                    </label>
                    <button type="button" className="secondary-button" onClick={() => openEditForm(detail)}><Pencil size={14} /> Edit</button>
                    {detail.resume_id && <button type="button" className="secondary-button" onClick={() => void downloadResume(detail.resume_id!, detail.resume_filename)}><Download size={14} /> Resume</button>}
                    <button type="button" className="secondary-button" onClick={() => void archiveCandidate(!detail.archived)}><Archive size={14} />{detail.archived ? "Restore" : "Archive"}</button>
                  </div>
                </>
              )}
            </div>
            {detail && (
              <>
                <nav className="bench-tabs" aria-label="Candidate sections">
                  {(["overview", "resume", "jobs", "notes"] as CandidateTab[]).map((tab) => (
                    <button type="button" key={tab} className={activeTab === tab ? "active" : ""} onClick={() => setActiveTab(tab)}>
                      {tab[0].toUpperCase() + tab.slice(1)}
                    </button>
                  ))}
                </nav>
                <div className="bench-drawer-content">
                  {actionError && <p className="bench-inline-error" role="alert">{actionError}</p>}
                  {activeTab === "overview" && (
                    <section className="bench-detail-section">
                      <h3><Sparkles size={15} /> AI-extracted resume details</h3>
                      {extracted ? (
                        <ExtractedDetails extracted={extracted} candidate={detail} />
                      ) : (
                        <div className="bench-detail-empty">
                          <p>Resume details have not been extracted yet. Extraction uses the existing resume parser and stores the result with this resume.</p>
                          {selectedResume && <button type="button" className="secondary-button" disabled={resumeBusy} onClick={() => void extractResume(selectedResume.id)}><Sparkles size={14} />{resumeBusy ? "Extracting..." : "Extract resume details"}</button>}
                          {!detail.resumes.length && <p>Upload a resume in the Resume tab to begin.</p>}
                        </div>
                      )}
                      <div className="bench-fact-list">
                        <Fact icon={<MapPin size={14} />} label="Location" value={detail.location || "Not provided"} />
                        <Fact icon={<Clock3 size={14} />} label="Availability" value={detail.availability || "Not provided"} />
                        <Fact icon={<MapPin size={14} />} label="Preferred location" value={detail.preferred_location || "Not provided"} />
                      </div>
                      {detail.linkedin && <p className="bench-profile-link"><a href={detail.linkedin} target="_blank" rel="noreferrer">LinkedIn profile</a></p>}
                      {detail.github && <p className="bench-profile-link"><a href={detail.github} target="_blank" rel="noreferrer">GitHub profile</a></p>}
                      {detail.portfolio && <p className="bench-profile-link"><a href={detail.portfolio} target="_blank" rel="noreferrer">Portfolio</a></p>}
                    </section>
                  )}

                  {activeTab === "resume" && (
                    <section className="bench-detail-section">
                      <div className="bench-section-heading">
                        <h3><FileText size={15} /> Resumes</h3>
                        <label className={`bench-upload-button ${resumeBusy ? "disabled" : ""}`}>
                          <FileUp size={14} /> Upload resume
                          <input type="file" accept="application/pdf,.pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,.docx" disabled={resumeBusy} onChange={(event) => {
                            void uploadResume(event.target.files?.[0]);
                            event.currentTarget.value = "";
                          }} />
                        </label>
                      </div>
                      {resumeError && <p className="bench-inline-error" role="alert">{resumeError}</p>}
                      {detail.resumes.length === 0 ? (
                        <div className="bench-detail-empty"><p>No resume is attached to this candidate.</p></div>
                      ) : (
                        <>
                          <label className="bench-resume-select">
                            <span>Candidate resume</span>
                            <select value={resumeId ?? ""} onChange={(event) => setResumeId(event.target.value)}>
                              {detail.resumes.map((resume) => <option key={resume.id} value={resume.id}>{resume.filename}{resume.is_active ? " · Active" : ""}</option>)}
                            </select>
                          </label>
                          {selectedResume && (
                            <>
                              <div className="bench-resume-card">
                                <span className="bench-resume-icon"><FileText size={18} /></span>
                                <div><strong>{selectedResume.filename}</strong><small>{(selectedResume.size / (1024 * 1024)).toFixed(2)} MB · {selectedResume.filename.toLocaleLowerCase().endsWith(".docx") ? "DOCX document" : `${selectedResume.page_count} pages`} · Uploaded {displayDate(selectedResume.uploaded_at)} · Extraction {selectedResume.extracted_at ? "complete" : "not run"}</small></div>
                                <button type="button" className="secondary-button" onClick={() => void downloadResume(selectedResume.id, selectedResume.filename)}><Download size={14} /> Download</button>
                              </div>
                              <div className="bench-resume-actions">
                                {!selectedResume.filename.toLocaleLowerCase().endsWith(".docx") && <button type="button" className="secondary-button" onClick={() => void downloadResume(selectedResume.id, selectedResume.filename, true)}>View full resume</button>}
                                <button type="button" className="secondary-button" disabled={resumeBusy} onClick={() => void extractResume(selectedResume.id)}><Sparkles size={14} />{resumeBusy ? "Extracting..." : selectedResume.extracted_at ? "Refresh extracted details" : "Extract details"}</button>
                              </div>
                              {selectedResume.filename.toLocaleLowerCase().endsWith(".docx")
                                ? <div className="bench-preview-placeholder">Preview is unavailable for DOCX files. Download the original document to view it.</div>
                                : previewUrl
                                  ? <iframe className="bench-resume-preview" src={previewUrl} title={`${selectedResume.filename} preview`} />
                                  : <div className="bench-preview-placeholder">Loading secure PDF preview...</div>}
                              {extracted && <ExtractedDetails extracted={extracted} candidate={detail} />}
                            </>
                          )}
                        </>
                      )}
                    </section>
                  )}

                  {activeTab === "jobs" && (
                    <section className="bench-detail-section">
                      <div className="bench-section-heading">
                        <h3><BriefcaseBusiness size={15} /> Candidate applications</h3>
                      </div>
                      <p className="bench-tab-help">Only actual application records assigned to this candidate appear here. Saved jobs alone are not displayed as applications.</p>
                      {jobsError && <div className="bench-inline-error" role="alert">{jobsError} <button type="button" className="bench-retry-link" onClick={() => void loadSavedJobs()}>Retry</button></div>}
                      {detail.applications.length ? (
                        <div className="bench-applications">
                          {detail.applications.map((application) => (
                            <article className="bench-application-card" key={application.id}>
                              <div><strong>{application.job_title}</strong><small>{application.company}{application.location ? ` · ${application.location}` : ""}</small></div>
                              <div className="bench-app-meta">
                                <span className="bench-application-status">{application.apply_status.replace(/_/g, " ")}</span>
                                <small>Workflow: {application.status.replace(/_/g, " ")}</small>
                                <small>{application.application_date ? `Applied ${displayDate(application.application_date)}` : `Tracked ${displayDate(application.tracked_at)}`}</small>
                                {application.url && <a href={application.url} target="_blank" rel="noreferrer">Original posting</a>}
                              </div>
                            </article>
                          ))}
                        </div>
                      ) : <div className="bench-detail-empty"><p>No applications are linked to this candidate yet.</p></div>}
                      <form className="bench-track-job" onSubmit={(event) => void trackJob(event)}>
                        <label>
                          <span>Track a saved job for this candidate</span>
                          <select value={selectedJobId} onChange={(event) => setSelectedJobId(event.target.value)}>
                            <option value="">Select a saved job...</option>
                            {jobs.filter((job) => !detail.applications.some((application) => application.job_id === job.id)).map((job) => (
                              <option key={job.id} value={job.id}>{job.title} · {job.company}</option>
                            ))}
                          </select>
                        </label>
                        <button type="submit" className="primary-button" disabled={!selectedJobId || jobBusy}>{jobBusy ? "Adding..." : "Create application"}</button>
                      </form>
                    </section>
                  )}

                  {activeTab === "notes" && (
                    <form className="bench-detail-section bench-notes-form" onSubmit={(event) => void saveNotes(event)}>
                      <h3>Internal recruiter notes</h3>
                      <p className="bench-tab-help">Notes are private to this authenticated workspace.</p>
                      <textarea rows={9} maxLength={10000} value={notesDraft} onChange={(event) => setNotesDraft(event.target.value)} placeholder="Add internal context, follow-ups, or recruiter notes..." />
                      <div className="bench-notes-footer">
                        <small>{notesDraft.length}/10000</small>
                        <button type="submit" className="primary-button" disabled={notesBusy}>{notesBusy ? "Saving..." : "Save notes"}</button>
                      </div>
                      {notesMessage && <p className={notesMessage === "Notes saved." ? "bench-success" : "bench-inline-error"} role="status">{notesMessage}</p>}
                    </form>
                  )}
                </div>
              </>
            )}
          </aside>
        </div>
      )}

      {formOpen && (
        <div className="bench-modal-backdrop" onMouseDown={(event) => {
          if (event.target === event.currentTarget && !formBusy && !resumeScanBusy) setFormOpen(false);
        }}>
          <form className="bench-candidate-form" role="dialog" aria-modal="true" aria-label={editingCandidate ? "Edit candidate" : "Add candidate"} onSubmit={(event) => void saveCandidate(event)}>
            <div className="bench-modal-heading">
              <div><span className="eyebrow">{editingCandidate ? "CANDIDATE PROFILE" : "NEW CANDIDATE"}</span><h2>{editingCandidate ? "Edit candidate" : "Add candidate"}</h2></div>
              <button type="button" className="bench-close" aria-label="Close form" disabled={formBusy || resumeScanBusy} onClick={() => setFormOpen(false)}><X size={18} /></button>
            </div>
            {formError && <p className="bench-inline-error" role="alert">{formError}</p>}
            <section className="bench-resume-scan">
              <div>
                <strong><Sparkles size={14} /> Add resume and auto-fill</strong>
                <p>Upload a PDF or DOCX. OpenRouter will extract details into the form for you to review before saving.</p>
              </div>
              <label className={`bench-upload-button ${resumeScanBusy || formBusy ? "disabled" : ""}`}>
                <FileUp size={14} /> {resumeScanBusy ? "Scanning..." : candidateResumeFile ? "Choose another file" : "Add resume"}
                <input
                  type="file"
                  accept="application/pdf,.pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,.docx"
                  disabled={resumeScanBusy || formBusy}
                  onChange={(event) => {
                    void scanCandidateResume(event.target.files?.[0]);
                    event.currentTarget.value = "";
                  }}
                />
              </label>
              {candidateResumeFile && (
                <div className="bench-selected-resume">
                  <small>{candidateResumeFile.name}</small>
                  <button type="button" disabled={formBusy || resumeScanBusy} onClick={() => {
                    resumeScanRequestId.current += 1;
                    setCandidateResumeFile(null);
                    setResumeExtracted(null);
                    setResumeScanError("");
                    setResumeScanMessage("");
                    setResumeScanBusy(false);
                  }}>Remove</button>
                </div>
              )}
              {resumeScanMessage && <p className="bench-success" role="status">{resumeScanMessage}</p>}
              {resumeScanError && <p className="bench-inline-error" role="alert">{resumeScanError}</p>}
            </section>
            <div className="bench-form-grid">
              <label className="wide"><span>Full name *</span><input required maxLength={200} value={form.full_name} onChange={(event) => setForm({ ...form, full_name: event.target.value })} /></label>
              <label><span>Target roles (comma separated)</span><input maxLength={500} value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value })} /></label>
              <label><span>Status</span><select value={form.status} onChange={(event) => setForm({ ...form, status: event.target.value as CandidateStatus })}>{Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
              <label><span>Email</span><input type="email" maxLength={254} value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} /></label>
              <label><span>Phone</span><input maxLength={40} value={form.phone} onChange={(event) => setForm({ ...form, phone: event.target.value })} /></label>
              <label><span>Location</span><input maxLength={150} value={form.location} onChange={(event) => setForm({ ...form, location: event.target.value })} /></label>
              <label><span>Availability</span><input maxLength={100} placeholder="e.g. Immediate, 15 days" value={form.availability} onChange={(event) => setForm({ ...form, availability: event.target.value })} /></label>
              <label><span>Experience (years)</span><input type="number" min="0" max="80" step="0.5" value={form.experience_years} onChange={(event) => setForm({ ...form, experience_years: event.target.value })} /></label>
              <label><span>Preferred location</span><input maxLength={150} value={form.preferred_location} onChange={(event) => setForm({ ...form, preferred_location: event.target.value })} /></label>
              <label className="wide"><span>Skills (comma separated)</span><input value={form.skills} onChange={(event) => setForm({ ...form, skills: event.target.value })} /></label>
              <label className="wide"><span>Professional summary</span><textarea rows={4} maxLength={4000} value={form.experience_summary} onChange={(event) => setForm({ ...form, experience_summary: event.target.value })} /></label>
              <details className="bench-form-section wide">
                <summary>Personal and professional details</summary>
                <div className="bench-form-grid">
                  <label><span>Alternate phone</span><input value={profileDetails.alternate_phone} onChange={(event) => setProfileDetails({ ...profileDetails, alternate_phone: event.target.value })} /></label>
                  <label><span>Country</span><input value={profileDetails.country} onChange={(event) => setProfileDetails({ ...profileDetails, country: event.target.value })} /></label>
                  <label><span>LinkedIn URL</span><input value={resumeExtracted?.linkedin ?? editingCandidate?.linkedin ?? ""} onChange={(event) => setResumeExtracted({ ...(resumeExtracted ?? {}), linkedin: event.target.value })} /></label>
                  <label><span>GitHub URL</span><input value={resumeExtracted?.github ?? editingCandidate?.github ?? ""} onChange={(event) => setResumeExtracted({ ...(resumeExtracted ?? {}), github: event.target.value })} /></label>
                  <label><span>Portfolio URL</span><input value={resumeExtracted?.portfolio ?? editingCandidate?.portfolio ?? ""} onChange={(event) => setResumeExtracted({ ...(resumeExtracted ?? {}), portfolio: event.target.value })} /></label>
                  <label><span>Personal website</span><input value={profileDetails.personal_website} onChange={(event) => setProfileDetails({ ...profileDetails, personal_website: event.target.value })} /></label>
                  <label className="wide"><span>Other professional links (comma separated)</span><input value={profileDetails.other_profile_links.join(", ")} onChange={(event) => setProfileDetails({ ...profileDetails, other_profile_links: splitList(event.target.value) })} /></label>
                  <label className="wide"><span>Professional headline</span><input value={profileDetails.professional_headline} onChange={(event) => setProfileDetails({ ...profileDetails, professional_headline: event.target.value })} /></label>
                  <label><span>Current job title</span><input value={profileDetails.current_job_title} onChange={(event) => setProfileDetails({ ...profileDetails, current_job_title: event.target.value })} /></label>
                  <label><span>Current company</span><input value={profileDetails.current_company} onChange={(event) => setProfileDetails({ ...profileDetails, current_company: event.target.value })} /></label>
                  <label><span>Relevant experience (years)</span><input type="number" min="0" max="80" step="0.5" value={profileDetails.relevant_experience_years ?? ""} onChange={(event) => setProfileDetails({ ...profileDetails, relevant_experience_years: event.target.value ? Number(event.target.value) : null })} /></label>
                  <label><span>Current employment status</span><input value={profileDetails.employment_status} onChange={(event) => setProfileDetails({ ...profileDetails, employment_status: event.target.value })} /></label>
                  <label><span>Seniority level</span><input value={profileDetails.seniority_level} onChange={(event) => setProfileDetails({ ...profileDetails, seniority_level: event.target.value })} /></label>
                  <label><span>Primary specialization</span><input value={profileDetails.primary_specialization} onChange={(event) => setProfileDetails({ ...profileDetails, primary_specialization: event.target.value })} /></label>
                  <label className="wide"><span>Industry/domain experience</span><input value={profileDetails.industry_experience.join(", ")} onChange={(event) => setProfileDetails({ ...profileDetails, industry_experience: splitList(event.target.value) })} /></label>
                </div>
              </details>
              <details className="bench-form-section wide">
                <summary>Skills by category</summary>
                <div className="bench-form-grid">
                  {([
                    ["Programming languages", "programming_languages"], ["Frameworks", "frameworks"],
                    ["Cloud platforms", "cloud_platforms"], ["DevOps tools", "devops_tools"],
                    ["Databases", "databases"], ["Operating systems", "operating_systems"],
                    ["Networking", "networking_skills"], ["Security tools", "security_tools"],
                    ["Monitoring tools", "monitoring_tools"], ["Soft skills", "soft_skills"],
                  ] as const).map(([label, key]) => <label key={key}><span>{label}</span><input value={profileDetails[key].join(", ")} onChange={(event) => setProfileDetails({ ...profileDetails, [key]: splitList(event.target.value) })} /></label>)}
                </div>
              </details>
              <EditableEntryList title="Work Experiences" entries={profileDetails.work_experience}
                fields={[
                  { key: "company", label: "Company" }, { key: "title", label: "Job title" },
                  { key: "employment_type", label: "Employment type" }, { key: "location", label: "Location" },
                  { key: "start_date", label: "Start date" }, { key: "end_date", label: "End date" },
                  { key: "currently_working", label: "Currently working here", kind: "checkbox" },
                  { key: "description", label: "Description", kind: "textarea" },
                  { key: "responsibilities", label: "Responsibilities", kind: "list" },
                  { key: "achievements", label: "Achievements", kind: "list" },
                  { key: "technologies", label: "Technologies and tools", kind: "list" },
                ]}
                makeEntry={() => ({ title: "", company: "", employment_type: "", duration: "", start_date: "", end_date: "", currently_working: false, location: "", description: "", responsibilities: [], achievements: [], technologies: [] })}
                onChange={(work_experience) => setProfileDetails({ ...profileDetails, work_experience })} />
              <EditableEntryList title="Education" entries={profileDetails.education}
                fields={[
                  { key: "degree", label: "Degree" }, { key: "institution", label: "Institution" },
                  { key: "university", label: "University" }, { key: "location", label: "Location" },
                  { key: "start_year", label: "Start year" }, { key: "graduation_year", label: "Graduation year" },
                  { key: "grade", label: "Grade / GPA" }, { key: "details", label: "Additional details", kind: "textarea" },
                ]}
                makeEntry={() => ({ institution: "", university: "", degree: "", duration: "", start_year: "", graduation_year: "", location: "", grade: "", details: "" })}
                onChange={(education) => setProfileDetails({ ...profileDetails, education })} />
              <EditableEntryList title="Certifications" entries={profileDetails.certifications}
                fields={[
                  { key: "name", label: "Certification" }, { key: "issuer", label: "Issuer" },
                  { key: "issue_date", label: "Issue date" }, { key: "expiry_date", label: "Expiry date" },
                  { key: "credential_id", label: "Credential ID" }, { key: "credential_url", label: "Credential URL" },
                  { key: "description", label: "Description", kind: "textarea" },
                ]}
                makeEntry={() => ({ name: "", issuer: "", issue_date: "", expiry_date: "", credential_id: "", credential_url: "", description: "" })}
                onChange={(certifications) => setProfileDetails({ ...profileDetails, certifications })} />
              <EditableEntryList title="Projects" entries={profileDetails.projects}
                fields={[
                  { key: "name", label: "Project name" }, { key: "role", label: "Role" },
                  { key: "technologies", label: "Technologies" }, { key: "start_date", label: "Start date" },
                  { key: "end_date", label: "End date" }, { key: "url", label: "Project URL" },
                  { key: "github_url", label: "GitHub URL" }, { key: "description", label: "Description", kind: "textarea" },
                  { key: "responsibilities", label: "Responsibilities", kind: "list" },
                ]}
                makeEntry={() => ({ name: "", role: "", duration: "", technologies: "", description: "", responsibilities: [], url: "", github_url: "", start_date: "", end_date: "" })}
                onChange={(projects) => setProfileDetails({ ...profileDetails, projects })} />
              <details className="bench-form-section wide">
                <summary>Training, achievements, publications and membership</summary>
                <div className="bench-form-grid">
                  {([
                    ["Training and courses", "training"], ["Achievements and awards", "achievements"],
                    ["Publications", "publications"], ["Professional memberships", "memberships"],
                  ] as const).map(([label, key]) => <label className="wide" key={key}><span>{label}</span><textarea rows={2} value={profileDetails[key].join(", ")} onChange={(event) => setProfileDetails({ ...profileDetails, [key]: splitList(event.target.value) })} /></label>)}
                </div>
              </details>
              <details className="bench-form-section wide">
                <summary>Recruiter-managed information</summary>
                <div className="bench-form-grid">
                  <label><span>Notice period</span><input value={profileDetails.notice_period} onChange={(event) => setProfileDetails({ ...profileDetails, notice_period: event.target.value })} /></label>
                  <label><span>Available from</span><input value={profileDetails.available_from} onChange={(event) => setProfileDetails({ ...profileDetails, available_from: event.target.value })} /></label>
                  <label><span>Work arrangement</span><select value={profileDetails.work_arrangement_preference} onChange={(event) => setProfileDetails({ ...profileDetails, work_arrangement_preference: event.target.value })}><option value="">Not provided</option><option>Remote</option><option>Hybrid</option><option>On-site</option></select></label>
                  <label><span>Employment preference</span><input value={profileDetails.employment_preference} onChange={(event) => setProfileDetails({ ...profileDetails, employment_preference: event.target.value })} /></label>
                  <label><span>Expected salary</span><input type="number" min="0" value={profileDetails.expected_salary ?? ""} onChange={(event) => setProfileDetails({ ...profileDetails, expected_salary: event.target.value ? Number(event.target.value) : null })} /></label>
                  <label><span>Salary currency</span><input value={profileDetails.salary_currency} onChange={(event) => setProfileDetails({ ...profileDetails, salary_currency: event.target.value })} /></label>
                  <label><span>Willing to relocate</span><select value={profileDetails.willing_to_relocate == null ? "" : String(profileDetails.willing_to_relocate)} onChange={(event) => setProfileDetails({ ...profileDetails, willing_to_relocate: event.target.value === "" ? null : event.target.value === "true" })}><option value="">Not specified</option><option value="true">Yes</option><option value="false">No</option></select></label>
                  <label><span>Internal tags</span><input value={profileDetails.internal_tags.join(", ")} onChange={(event) => setProfileDetails({ ...profileDetails, internal_tags: splitList(event.target.value) })} /></label>
                  <label className="wide"><span>Recruiter notes</span><textarea rows={3} value={profileDetails.recruiter_notes} onChange={(event) => setProfileDetails({ ...profileDetails, recruiter_notes: event.target.value })} /></label>
                </div>
              </details>
            </div>
            <div className="bench-form-actions">
              <button type="button" className="secondary-button" disabled={formBusy || resumeScanBusy} onClick={() => setFormOpen(false)}>Cancel</button>
              <button type="submit" className="primary-button" disabled={formBusy || resumeScanBusy}>{resumeScanBusy ? "Scanning resume..." : formBusy ? "Saving..." : editingCandidate ? "Save changes" : "Create candidate"}</button>
            </div>
          </form>
        </div>
      )}
    </section>
  );
}

type EntryField = { key: string; label: string; kind?: "textarea" | "list" | "checkbox" };

function EditableEntryList<T extends EditableEntry>({
  title, entries, fields, makeEntry, onChange,
}: {
  title: string;
  entries: T[];
  fields: EntryField[];
  makeEntry: () => T;
  onChange: (entries: T[]) => void;
}) {
  function update(index: number, key: string, value: string | boolean | string[]) {
    onChange(entries.map((entry, itemIndex) =>
      itemIndex === index ? { ...entry, [key]: value } as T : entry,
    ));
  }
  return (
    <section className="bench-form-section wide">
      <div className="bench-entry-heading">
        <h3>{title}</h3>
        <button type="button" className="secondary-button" onClick={() => onChange([...entries, makeEntry()])}>
          <Plus size={14} /> Add entry
        </button>
      </div>
      {!entries.length && <p className="bench-muted">No entries provided.</p>}
      {entries.map((entry, index) => (
        <article className="bench-entry-card" key={`${title}-${index}`}>
          <div className="bench-entry-heading">
            <strong>{title.slice(0, -1)} {index + 1}</strong>
            <button type="button" className="bench-entry-remove" onClick={() => onChange(entries.filter((_, itemIndex) => itemIndex !== index))}>
              <X size={13} /> Remove
            </button>
          </div>
          <div className="bench-form-grid">
            {fields.map((field) => {
              const value = entry[field.key];
              if (field.kind === "checkbox") return (
                <label className="bench-entry-checkbox" key={field.key}>
                  <input type="checkbox" checked={value === true} onChange={(event) => update(index, field.key, event.target.checked)} />
                  <span>{field.label}</span>
                </label>
              );
              const text = Array.isArray(value) ? value.join(", ") : typeof value === "string" ? value : "";
              const change = (next: string) => update(index, field.key, field.kind === "list" ? splitList(next) : next);
              return <label className={field.kind === "textarea" ? "wide" : ""} key={field.key}>
                <span>{field.label}</span>
                {field.kind === "textarea"
                  ? <textarea rows={3} value={text} onChange={(event) => change(event.target.value)} />
                  : <input value={text} onChange={(event) => change(event.target.value)} />}
              </label>;
            })}
          </div>
        </article>
      ))}
    </section>
  );
}

function SummaryCard({ icon, label, value, tone }: { icon: ReactNode; label: string; value: number; tone: string }) {
  return (
    <article className="bench-summary-card">
      <span className={`bench-summary-icon ${tone}`}>{icon}</span>
      <span className="bench-summary-copy"><small>{label}</small><strong>{value}</strong></span>
      <span className="bench-summary-accent" aria-hidden="true" />
    </article>
  );
}

function Fact({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return <div className="bench-fact"><span>{icon}</span><div><small>{label}</small><strong>{value}</strong></div></div>;
}

function ExtractedDetails({ extracted, candidate }: { extracted: ExtractedResume; candidate: Candidate }) {
  const skills = extracted.skills?.length ? extracted.skills :
    candidate.skills?.length ? candidate.skills :
      Object.values(extracted.skill_categories ?? candidate.skill_categories ?? {}).flat();
  const work = extracted.work_experience?.length ? extracted.work_experience : candidate.work_experience;
  const education = extracted.education?.length ? extracted.education : candidate.education;
  const certifications = extracted.certifications?.length ? extracted.certifications : candidate.certifications;
  const projects = extracted.projects?.length ? extracted.projects : candidate.projects;
  const summary = extracted.experience_summary || candidate.experience_summary;
  const training = extracted.training?.length ? extracted.training : candidate.training;
  const achievements = extracted.achievements?.length ? extracted.achievements : candidate.achievements;
  const hasContent = Boolean(summary || skills?.length || work?.length || education?.length || certifications?.length || projects?.length || achievements?.length || training?.length || extracted.publications?.length || extracted.memberships?.length);
  if (!hasContent) return <div className="bench-detail-empty">No extracted profile details are available for this resume yet.</div>;
  return (
    <div className="bench-extracted">
      {summary && <p className="bench-extracted-summary">{summary}</p>}
      {skills?.length ? <div className="bench-extracted-block"><h4>Technical skills</h4><div className="bench-skill-tags">{skills.map((skill) => <span key={skill}>{skill}</span>)}</div></div> : null}
      {(extracted.current_job_title || extracted.professional_headline || extracted.current_company) && <p className="bench-extracted-summary">{[extracted.professional_headline || extracted.current_job_title, extracted.current_company].filter(Boolean).join(" · ")}</p>}
      {work?.length ? <div className="bench-extracted-block"><h4>Work experience</h4>{work.map((entry, index) => <article className="bench-history-entry" key={`${entry.company}-${index}`}><strong>{entry.title || "Role not provided"}</strong><small>{[entry.company, entry.duration, entry.location].filter(Boolean).join(" · ") || "Employer details not provided"}</small>{entry.description && <p>{entry.description}</p>}</article>)}</div> : null}
      {education?.length ? <div className="bench-extracted-block"><h4><GraduationCap size={14} /> Education</h4>{education.map((entry, index) => <article className="bench-history-entry" key={`${entry.institution}-${index}`}><strong>{entry.degree || "Degree not provided"}</strong><small>{[entry.institution, entry.duration, entry.grade].filter(Boolean).join(" · ")}</small></article>)}</div> : null}
      {certifications?.length ? <div className="bench-extracted-block"><h4>Certifications</h4>{certifications.map((entry, index) => <article className="bench-history-entry" key={`${entry.name}-${index}`}><strong>{entry.name || "Certification"}</strong>{entry.issuer && <small>{entry.issuer}</small>}</article>)}</div> : null}
      {projects?.length ? <div className="bench-extracted-block"><h4>Projects</h4>{projects.map((entry, index) => <article className="bench-history-entry" key={`${entry.name}-${index}`}><strong>{entry.name || "Project"}</strong><small>{[entry.role, entry.duration, entry.technologies].filter(Boolean).join(" · ")}</small>{entry.description && <p>{entry.description}</p>}</article>)}</div> : null}
      {training?.length ? <div className="bench-extracted-block"><h4>Training and courses</h4><ul>{training.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}
      {achievements?.length ? <div className="bench-extracted-block"><h4>Achievements</h4><ul>{achievements.map((achievement) => <li key={achievement}>{achievement}</li>)}</ul></div> : null}
      {extracted.publications?.length ? <div className="bench-extracted-block"><h4>Publications</h4><ul>{extracted.publications.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}
      {extracted.memberships?.length ? <div className="bench-extracted-block"><h4>Memberships</h4><ul>{extracted.memberships.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}
    </div>
  );
}
