import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  BriefcaseBusiness,
  EyeOff,
  RotateCcw,
  CheckCircle2,
  CircleAlert,
  Gauge,
  Kanban,
  List,
  Lock,
  Plus,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Target,
  Upload,
  X,
} from "lucide-react";

const API = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

// Order and names shown to the user. The server is the source of truth for
// which moves are allowed; this is only for display.
const STATUS_LABELS: Record<string, string> = {
  discovered: "Discovered",
  saved: "Saved",
  under_review: "Under review",
  ready_for_approval: "Ready for approval",
  approved: "Approved",
  submitted: "Submitted",
  interview: "Interview",
  offer: "Offer",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
};

const KANBAN_STATUSES = Object.keys(STATUS_LABELS);

function statusLabel(status: string | null): string {
  if (!status) return "Created";
  return (
    STATUS_LABELS[status] ??
    status.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase())
  );
}

// Statuses only the approval workflow may set. The server enforces this; the
// board only shows them as locked.
const LOCKED_STATUSES = ["approved", "submitted"];

// "today", "1 day", "5 days"... since the application last changed status.
function timeInStage(app: { status_history?: { at: string }[] }): string | null {
  const history = app.status_history ?? [];
  const last = history[history.length - 1];
  if (!last) return null;
  const days = Math.floor(
    (Date.now() - new Date(last.at).getTime()) / (1000 * 60 * 60 * 24),
  );
  if (Number.isNaN(days)) return null;
  if (days <= 0) return "today";
  return days === 1 ? "1 day" : `${days} days`;
}

function readSetting<T extends string>(key: string, fallback: T, allowed: T[]): T {
  try {
    const value = window.localStorage.getItem(key) as T | null;
    return value && allowed.includes(value) ? value : fallback;
  } catch {
    return fallback;
  }
}
function saveSetting(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Storage can be blocked; the board still works without remembering.
  }
}

type Page = "overview" | "applications";

// Simple page switching with the URL hash, so refresh and the browser Back
// button keep working without adding a router library.
function pageFromHash(): Page {
  return window.location.hash.startsWith("#/applications")
    ? "applications"
    : "overview";
}

type Job = {
  id: string;
  title: string;
  company: string;
  location: string | null;
  source: string;
  url: string | null;
  description: string;
  skills: string[];
  nice_to_have_skills?: string[];
  work_arrangement?: string;
  experience_level?: string;
  match_score: number;
  match_reasons: string[];
  match_confidence?: "" | "low" | "medium" | "high";
  match_breakdown?: MatchFactor[];
  feedback?: "" | "not_interested";
  feedback_reason?: string;
};

// Why you passed on a job. "page" is where the matching setting lives.
const DISMISS_REASONS: { key: string; label: string; page?: "profile" | "preferences" }[] = [
  { key: "role", label: "Wrong kind of role", page: "profile" },
  { key: "skills", label: "Skills don't fit", page: "profile" },
  { key: "location", label: "Wrong location", page: "preferences" },
  { key: "salary", label: "Pay too low", page: "preferences" },
  { key: "experience", label: "Wrong level", page: "preferences" },
  { key: "company", label: "Don't like the company" },
  { key: "other", label: "Other" },
];
const IMPORT_LABELS: Record<string, string> = {
  title: "title",
  company: "company",
  location: "location",
  skills: "required skills",
  nice_to_have_skills: "nice-to-have skills",
  work_arrangement: "work style",
  experience_level: "level",
  salary: "salary",
};

const reasonLabel = (key?: string) =>
  DISMISS_REASONS.find((r) => r.key === key)?.label ?? "No reason given";

// One line of the score explanation. score is null when the factor could not
// be judged (the job or your preferences do not say enough).
type MatchFactor = {
  key: string;
  label: string;
  weight: number;
  score: number | null;
  status: "good" | "partial" | "poor" | "unknown";
  reason: string;
  details?: {
    matched?: string[];
    missing_must_have?: string[];
    missing_nice_to_have?: string[];
  };
};

const CONFIDENCE_HELP: Record<string, string> = {
  high: "Most factors could be judged.",
  medium: "Some factors could not be judged.",
  low: "Few factors could be judged. Add job details or fill in your preferences.",
};

// "Why this score?" panel: one bar per factor, with the reason in plain words.
function MatchBreakdown({ job }: { job: Job }) {
  const factors = job.match_breakdown ?? [];
  const judged = factors.filter((f) => f.status !== "unknown");
  const unknown = factors.filter((f) => f.status === "unknown");
  const skills = factors.find((f) => f.key === "skills");
  const matched = skills?.details?.matched ?? [];
  const missingMust = skills?.details?.missing_must_have ?? [];
  const missingNice = skills?.details?.missing_nice_to_have ?? [];

  return (
    <details className="why-score">
      <summary>
        Why this score?
        {job.match_confidence && (
          <span className={`confidence ${job.match_confidence}`}>
            {job.match_confidence} confidence
          </span>
        )}
      </summary>

      {job.match_confidence && (
        <p className="why-help">{CONFIDENCE_HELP[job.match_confidence]}</p>
      )}

      {judged.map((f) => (
        <div className="factor" key={f.key}>
          <div className="factor-head">
            <span>{f.label}</span>
            <span className="factor-score">
              {Math.round(f.score ?? 0)}%
              <small> · weight {Math.round(f.weight)}</small>
            </span>
          </div>
          <div className="factor-bar" aria-hidden="true">
            <div
              className={`factor-fill ${f.status}`}
              style={{ width: `${Math.max(2, f.score ?? 0)}%` }}
            />
          </div>
          <small className="factor-reason">{f.reason}</small>
        </div>
      ))}

      {(matched.length > 0 || missingMust.length > 0 || missingNice.length > 0) && (
        <div className="why-skills">
          {matched.length > 0 && (
            <small className="reason ok">You have: {matched.join(", ")}</small>
          )}
          {missingMust.length > 0 && (
            <small className="reason gap">
              Missing must-have: {missingMust.join(", ")}
            </small>
          )}
          {missingNice.length > 0 && (
            <small className="reason">
              Missing nice-to-have: {missingNice.join(", ")}
            </small>
          )}
        </div>
      )}

      {unknown.length > 0 && (
        <div className="why-unknown">
          <strong>Not counted</strong>
          {unknown.map((f) => (
            <small className="reason" key={f.key}>
              {f.label}: {f.reason}
            </small>
          ))}
        </div>
      )}
    </details>
  );
}

type StatusHistoryEntry = {
  from_status: string | null;
  to_status: string;
  at: string;
  actor: string;
  note: string;
};

type Application = {
  id: string;
  job_id: string;
  status: string;
  allowed_next: string[];
  gated_next: string[];
  status_history: StatusHistoryEntry[];
  resume_id: string | null;
  resume_label: string | null;
  job_title: string;
  company: string;
  notes: string;
};

type Profile = {
  id?: string;
  full_name: string;
  target_roles: string[];
  skills: string[];
  experience_summary: string;
} | null;

type WorkForm = {
  title: string;
  company: string;
  duration: string;
  location: string;
  description: string;
};
type ProjectForm = {
  name: string;
  role: string;
  duration: string;
  technologies: string;
  description: string;
};
type EduForm = {
  institution: string;
  degree: string;
  duration: string;
  grade: string;
};
type CertForm = { name: string; issuer: string };

type ProfileForm = {
  full_name: string;
  email: string;
  phone: string;
  location: string;
  github: string;
  linkedin: string;
  portfolio: string;
  target_roles: string;
  skills: string;
  experience_summary: string;
  work_experience: WorkForm[];
  projects: ProjectForm[];
  education: EduForm[];
  certifications: CertForm[];
  achievements: string;
};

type ListKey = "work_experience" | "projects" | "education" | "certifications";

const EMPTY_ENTRY = {
  work_experience: {
    title: "",
    company: "",
    duration: "",
    location: "",
    description: "",
  },
  projects: { name: "", role: "", duration: "", technologies: "", description: "" },
  education: { institution: "", degree: "", duration: "", grade: "" },
  certifications: { name: "", issuer: "" },
};

function emptyProfileForm(): ProfileForm {
  return {
    full_name: "",
    email: "",
    phone: "",
    location: "",
    github: "",
    linkedin: "",
    portfolio: "",
    target_roles: "",
    skills: "",
    experience_summary: "",
    work_experience: [],
    projects: [],
    education: [],
    certifications: [],
    achievements: "",
  };
}

// Safe converters: the AI / API may return strings, arrays or nothing.
function asText(v: unknown): string {
  if (v == null) return "";
  if (Array.isArray(v)) return v.map((i) => String(i).trim()).filter(Boolean).join("\n");
  return String(v).trim();
}
function asInline(v: unknown): string {
  return Array.isArray(v)
    ? v.map((i) => String(i).trim()).filter(Boolean).join(", ")
    : asText(v);
}
function asList(v: unknown): string[] {
  return Array.isArray(v) ? v.map((i) => String(i).trim()).filter(Boolean) : [];
}
function splitCommas(text: string): string[] {
  return text.split(",").map((i) => i.trim()).filter(Boolean);
}
function isBlankEntry(entry: Record<string, string>): boolean {
  return Object.values(entry).every((v) => !v.trim());
}

// Builds the form from API data (profile or AI-extracted resume).
// When `prev` is given, empty incoming values keep what the user already had.
function profileToForm(data: any, prev?: ProfileForm): ProfileForm {
  const base = prev ?? emptyProfileForm();
  const text = (key: keyof ProfileForm, incoming: string) =>
    incoming || (base[key] as string);

  const entries = <T extends Record<string, string>>(
    incoming: unknown,
    shape: T,
    fallback: T[],
    inlineKeys: string[] = [],
  ): T[] => {
    if (!Array.isArray(incoming) || incoming.length === 0) return fallback;
    return incoming.map((raw: any) => {
      const item: Record<string, string> = {};
      for (const key of Object.keys(shape)) {
        item[key] = inlineKeys.includes(key) ? asInline(raw?.[key]) : asText(raw?.[key]);
      }
      return item as T;
    });
  };

  const achievements = asList(data?.achievements).join("\n");

  return {
    full_name: text("full_name", asText(data?.full_name)),
    email: text("email", asText(data?.email)),
    phone: text("phone", asText(data?.phone)),
    location: text("location", asText(data?.location)),
    github: text("github", asText(data?.github)),
    linkedin: text("linkedin", asText(data?.linkedin)),
    portfolio: text("portfolio", asText(data?.portfolio)),
    target_roles: text("target_roles", asList(data?.target_roles).join(", ")),
    skills: text("skills", asList(data?.skills).join(", ")),
    experience_summary: text("experience_summary", asText(data?.experience_summary)),
    work_experience: entries(data?.work_experience, EMPTY_ENTRY.work_experience, base.work_experience),
    projects: entries(data?.projects, EMPTY_ENTRY.projects, base.projects, ["technologies"]),
    education: entries(data?.education, EMPTY_ENTRY.education, base.education),
    certifications: entries(data?.certifications, EMPTY_ENTRY.certifications, base.certifications),
    achievements: achievements || base.achievements,
  };
}

// Required-field checker. Returns { fieldKey: message }. Empty object = OK.
// Completely blank entries are ignored (they are dropped on save).
function validateProfile(f: ProfileForm): Record<string, string> {
  const e: Record<string, string> = {};
  const req = "This field is required";

  if (!f.full_name.trim()) e.full_name = req;
  if (!f.email.trim()) e.email = req;
  else if (!/^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$/.test(f.email.trim()))
    e.email = "Enter a valid email address";
  if (f.phone.replace(/\D/g, "").length < 7) e.phone = "Enter a valid phone number";
  if (!f.location.trim()) e.location = req;
  if (splitCommas(f.target_roles).length === 0) e.target_roles = "Add at least one role";
  if (splitCommas(f.skills).length === 0) e.skills = "Add at least one skill";
  if (!f.experience_summary.trim()) e.experience_summary = req;

  const work = f.work_experience.map((w, i) => ({ w, i })).filter(({ w }) => !isBlankEntry(w));
  const projects = f.projects.map((p, i) => ({ p, i })).filter(({ p }) => !isBlankEntry(p));
  const edu = f.education.map((x, i) => ({ x, i })).filter(({ x }) => !isBlankEntry(x));

  if (edu.length === 0) e.education = "Add at least one education entry";
  if (work.length === 0 && projects.length === 0)
    e.experience_or_projects = "Add at least one work experience or project";

  work.forEach(({ w, i }) => {
    (["title", "company", "duration", "description"] as const).forEach((k) => {
      if (!w[k].trim()) e[`work_experience.${i}.${k}`] = req;
    });
  });
  projects.forEach(({ p, i }) => {
    (["name", "description"] as const).forEach((k) => {
      if (!p[k].trim()) e[`projects.${i}.${k}`] = req;
    });
  });
  edu.forEach(({ x, i }) => {
    (["institution", "degree"] as const).forEach((k) => {
      if (!x[k].trim()) e[`education.${i}.${k}`] = req;
    });
  });
  f.certifications.forEach((c, i) => {
    if (!isBlankEntry(c) && !c.name.trim()) e[`certifications.${i}.name`] = req;
  });

  return e;
}

type ResumeFile = {
  id: string;
  label: string;
  filename: string;
  size: number;
  page_count: number;
  is_active: boolean;
  uploaded_at: string;
  extracted_at: string | null;
};

const MAX_RESUMES = 5;

function formatSize(bytes: number): string {
  return bytes >= 1024 * 1024
    ? `${(bytes / (1024 * 1024)).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

type PreferencesForm = {
  locations: string;
  experience_level: string;
  work_arrangements: string[];
  salary_min: string;
  salary_max: string;
  salary_currency: string;
  salary_period: string;
};

const EXPERIENCE_LEVELS: Record<string, string> = {
  "": "No preference",
  intern: "Intern",
  entry: "Entry level / fresher",
  mid: "Mid level",
  senior: "Senior",
  lead: "Lead / principal",
};

const WORK_ARRANGEMENTS: Record<string, string> = {
  remote: "Remote",
  hybrid: "Hybrid",
  onsite: "On-site",
};

function emptyPreferencesForm(): PreferencesForm {
  return {
    locations: "",
    experience_level: "",
    work_arrangements: [],
    salary_min: "",
    salary_max: "",
    salary_currency: "",
    salary_period: "year",
  };
}

function preferencesToForm(data: any): PreferencesForm {
  return {
    locations: asList(data?.locations).join(", "),
    experience_level: asText(data?.experience_level),
    work_arrangements: asList(data?.work_arrangements),
    salary_min: data?.salary_min == null ? "" : String(data.salary_min),
    salary_max: data?.salary_max == null ? "" : String(data.salary_max),
    salary_currency: asText(data?.salary_currency),
    salary_period: asText(data?.salary_period) || "year",
  };
}

function validatePreferences(f: PreferencesForm): Record<string, string> {
  const errors: Record<string, string> = {};
  const toNumber = (v: string) => (v.trim() === "" ? null : Number(v));
  const low = toNumber(f.salary_min);
  const high = toNumber(f.salary_max);

  if (low !== null && (!Number.isInteger(low) || low < 0))
    errors.salary_min = "Enter a whole number, 0 or more";
  if (high !== null && (!Number.isInteger(high) || high < 0))
    errors.salary_max = "Enter a whole number, 0 or more";

  if (!errors.salary_min && !errors.salary_max) {
    if (low !== null && high !== null && high < low)
      errors.salary_max = "Maximum cannot be lower than minimum";
    if (
      (low !== null || high !== null) &&
      !/^[A-Za-z]{3}$/.test(f.salary_currency.trim())
    )
      errors.salary_currency = "Use a 3-letter code, e.g. INR or USD";
  }
  return errors;
}

// A labelled input wrapper with a required star and inline error text.
function Field({
  label,
  required,
  error,
  hint,
  children,
}: {
  label: string;
  required?: boolean;
  error?: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className={error ? "has-error" : undefined}>
      {label}
      {required && <span className="req"> *</span>}
      {hint && <small> ({hint})</small>}
      {children}
      {error && <small className="field-error">{error}</small>}
    </label>
  );
}

// Turns an API error body into readable text.
function errorText(data: any, fallback: string): string {
  const d = data?.detail;

  if (typeof d === "string") return d;

  if (Array.isArray(d)) {
    return d
      .map(
        (x: any) => `${(x.loc || []).slice(1).join(".") || "input"}: ${x.msg}`,
      )
      .join("; ");
  }

  return fallback;
}

function numberOrNull(v: string): number | null {
  return v.trim() === "" ? null : Number(v);
}

function emptyJobForm() {
  return {
    title: "",
    company: "",
    location: "",
    url: "",
    description: "",
    skills: "",
    nice_to_have_skills: "",
    work_arrangement: "",
    experience_level: "",
    salary_min: "",
    salary_max: "",
    salary_currency: "",
    salary_period: "year",
  };
}

function MatchReasons({ reasons }: { reasons: string[] }) {
  const matched = reasons
    .filter((r) => r.startsWith("Matched skill: "))
    .map((r) => r.replace("Matched skill: ", ""));

  const gaps = reasons
    .filter((r) => r.startsWith("Potential gap: "))
    .map((r) => r.replace("Potential gap: ", ""));

  const other = reasons.filter(
    (r) => !r.startsWith("Matched skill: ") && !r.startsWith("Potential gap: "),
  );

  return (
    <>
      {matched.length > 0 && (
        <small className="reason ok">Matched: {matched.join(", ")}</small>
      )}

      {gaps.length > 0 && (
        <small className="reason gap">Missing: {gaps.join(", ")}</small>
      )}

      {other.map((r, i) => (
        <small className="reason" key={i}>
          {r}
        </small>
      ))}
    </>
  );
}

function App() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [applications, setApplications] = useState<Application[]>([]);
  const [profile, setProfile] = useState<Profile>(null);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [minMatch, setMinMatch] = useState(0);
  const [sourceFilter, setSourceFilter] = useState("all");
  const [sortBy, setSortBy] = useState<"best" | "newest" | "company">("best");

  // Application tracker: board (Kanban) or list, remembered between visits.
  const [trackerView, setTrackerView] = useState<"board" | "list">(() =>
    readSetting("cp-tracker-view", "board", ["board", "list"]),
  );
  const [hideEmpty, setHideEmpty] = useState<boolean>(
    () => readSetting("cp-hide-empty", "no", ["yes", "no"]) === "yes",
  );
  const [page, setPage] = useState<Page>(pageFromHash);
  const [dismissingId, setDismissingId] = useState<string | null>(null);
  const [draggedAppId, setDraggedAppId] = useState<string | null>(null);
  const [dragOverStatus, setDragOverStatus] = useState<string | null>(null);
  const [showJobForm, setShowJobForm] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [pasteOpen, setPasteOpen] = useState(true);
  const [importing, setImporting] = useState(false);
  const [importMessage, setImportMessage] = useState("");
  const [showProfile, setShowProfile] = useState(false);
  const [busy, setBusy] = useState(false);
  const [parsing, setParsing] = useState(false);
  const [parseMessage, setParseMessage] = useState("");
  const [notice, setNotice] = useState("");
  const [formError, setFormError] = useState("");

  const [form, setForm] = useState(emptyJobForm());

  const [profileForm, setProfileForm] = useState<ProfileForm>(emptyProfileForm());
  const [profileErrors, setProfileErrors] = useState<Record<string, string>>({});

  const [showPreferences, setShowPreferences] = useState(false);
  const [preferencesForm, setPreferencesForm] = useState<PreferencesForm>(
    emptyPreferencesForm(),
  );
  const [preferencesErrors, setPreferencesErrors] = useState<
    Record<string, string>
  >({});
  const [preferencesError, setPreferencesError] = useState("");
  const [preferencesBusy, setPreferencesBusy] = useState(false);

  const [resumes, setResumes] = useState<ResumeFile[]>([]);
  const [resumeBusy, setResumeBusy] = useState(false);
  const [resumeMessage, setResumeMessage] = useState("");
  const [resumeError, setResumeError] = useState("");
  const [fillingId, setFillingId] = useState<string | null>(null);

  async function load() {
    // Load each part separately so one failing request (e.g. the profile)
    // can never hide the jobs and the application tracker.
    const get = async (path: string) => {
      const response = await fetch(`${API}${path}`);
      if (!response.ok) throw new Error(`${path} returned HTTP ${response.status}`);
      return response.json();
    };

    const [
      jobsResult,
      applicationsResult,
      profileResult,
      preferencesResult,
      resumesResult,
    ] = await Promise.allSettled([
      get("/api/jobs"),
      get("/api/applications"),
      get("/api/profile"),
      get("/api/preferences"),
      get("/api/resumes"),
    ]);

    const problems: string[] = [];

    if (jobsResult.status === "fulfilled") setJobs(jobsResult.value);
    else problems.push(String(jobsResult.reason?.message ?? "jobs failed"));

    if (applicationsResult.status === "fulfilled")
      setApplications(applicationsResult.value);
    else
      problems.push(
        String(applicationsResult.reason?.message ?? "applications failed"),
      );

    if (profileResult.status === "fulfilled") {
      setProfile(profileResult.value);
      if (profileResult.value) setProfileForm(profileToForm(profileResult.value));
    } else {
      problems.push(String(profileResult.reason?.message ?? "profile failed"));
    }

    if (preferencesResult.status === "fulfilled") {
      setPreferencesForm(preferencesToForm(preferencesResult.value));
    } else {
      problems.push(
        String(preferencesResult.reason?.message ?? "preferences failed"),
      );
    }

    if (resumesResult.status === "fulfilled") {
      setResumes(resumesResult.value);
    } else {
      problems.push(String(resumesResult.reason?.message ?? "resumes failed"));
    }

    if (problems.length > 0) {
      setNotice(
        `Some data could not be loaded (${problems.join("; ")}). ` +
          "Check that MongoDB and the FastAPI backend are running (see README).",
      );
    }
  }

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    const onHashChange = () => setPage(pageFromHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    setFormError("");
  }, [showJobForm, showProfile, showPreferences]);

  // job id -> job, so board cards can show match score, location and link
  const jobsById = useMemo(
    () => new Map(jobs.map((j) => [String(j.id), j])),
    [jobs],
  );

  const draggedApplication = useMemo(
    () => applications.find((a) => a.id === draggedAppId) ?? null,
    [applications, draggedAppId],
  );

  const sources = useMemo(
    () => Array.from(new Set(jobs.map((j) => j.source))).sort(),
    [jobs],
  );

  const dismissedJobs = useMemo(
    () => jobs.filter((j) => j.feedback === "not_interested"),
    [jobs],
  );

  // If you keep passing on jobs for the same fixable reason, say where to fix it.
  const dismissTip = useMemo(() => {
    const counts = new Map<string, number>();
    for (const j of dismissedJobs) {
      const reason = j.feedback_reason ?? "";
      counts.set(reason, (counts.get(reason) ?? 0) + 1);
    }
    return DISMISS_REASONS.filter((r) => r.page && (counts.get(r.key) ?? 0) >= 2)
      .map((r) => ({ ...r, count: counts.get(r.key) ?? 0 }))
      .sort((a, b) => b.count - a.count)[0];
  }, [dismissedJobs]);

  const visibleJobs = useMemo(() => {
    // job id -> application status ("none" if the job is not tracked yet)
    const statusByJob = new Map(
      applications.map((a) => [String(a.job_id), a.status]),
    );
    const text = query.toLowerCase();

    const list = jobs.filter((j) => {
      const matchesText = `${j.title} ${j.company} ${j.location || ""}`
        .toLowerCase()
        .includes(text);
      const status = statusByJob.get(j.id) ?? "none";
      return (
        j.feedback !== "not_interested" &&
        matchesText &&
        j.match_score >= minMatch &&
        (sourceFilter === "all" || j.source === sourceFilter) &&
        (statusFilter === "all" || status === statusFilter)
      );
    });

    // `jobs` arrives newest-first from the API, so "newest" keeps that order.
    if (sortBy === "best") {
      list.sort((a, b) => b.match_score - a.match_score);
    } else if (sortBy === "company") {
      list.sort((a, b) => a.company.localeCompare(b.company));
    }
    return list;
  }, [jobs, applications, query, statusFilter, minMatch, sourceFilter, sortBy]);

  const appliedCount = applications.filter((a) =>
    ["submitted", "interview", "offer", "rejected"].includes(a.status),
  ).length;

  const reviewCount = applications.filter(
    (a) => ["under_review", "ready_for_approval"].includes(a.status),
  ).length;

  // "Not interested" (with a reason), or restore the job (value "").
  async function saveFeedback(
    job: Job,
    value: "" | "not_interested",
    reason = "",
  ) {
    try {
      const response = await fetch(`${API}/api/jobs/${job.id}/feedback`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value, reason }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(errorText(data, "Could not save your choice."));
      }
      setJobs((list) => list.map((j) => (j.id === job.id ? data : j)));
      setDismissingId(null);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Could not save your choice.");
    }
  }

  // Read pasted posting text and fill the form. Nothing is saved until you press Save.
  async function importFromText() {
    setImporting(true);
    setImportMessage("");
    try {
      const response = await fetch(`${API}/api/jobs/import-preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: pasteText, url: form.url.trim() }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(errorText(data, "Could not read that posting."));
      }
      const d = data.draft;
      // Only overwrite fields the importer actually found.
      setForm((f) => ({
        ...f,
        title: d.title || f.title,
        company: d.company || f.company,
        location: d.location || f.location,
        url: d.url || f.url,
        description: d.description || f.description,
        skills: d.skills.length ? d.skills.join(", ") : f.skills,
        nice_to_have_skills: d.nice_to_have_skills.length
          ? d.nice_to_have_skills.join(", ")
          : f.nice_to_have_skills,
        work_arrangement: d.work_arrangement || f.work_arrangement,
        experience_level: d.experience_level || f.experience_level,
        salary_min: d.salary_min != null ? String(d.salary_min) : f.salary_min,
        salary_max: d.salary_max != null ? String(d.salary_max) : f.salary_max,
        salary_currency: d.salary_currency || f.salary_currency,
        salary_period: d.salary_currency ? d.salary_period : f.salary_period,
      }));
      const found = (data.found as string[]).map((k) => IMPORT_LABELS[k] ?? k);
      const missing = (data.missing as string[]).map((k) => IMPORT_LABELS[k] ?? k);
      setImportMessage(
        found.length
          ? `Filled in: ${found.join(", ")}.` +
              (missing.length ? ` Not found: ${missing.join(", ")}.` : "") +
              " Please check the details below before saving."
          : "I could not recognise any details. Add them by hand below.",
      );
    } catch (e) {
      setImportMessage(e instanceof Error ? e.message : "Could not read that posting.");
    } finally {
      setImporting(false);
    }
  }

  // Jobs saved before explainable matching have no breakdown yet.
  async function rescoreJobs() {
    setBusy(true);
    try {
      const response = await fetch(`${API}/api/jobs/rescore`, { method: "POST" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setNotice("Scores updated.");
      await load();
    } catch (e) {
      setNotice(
        `Could not re-score jobs (${e instanceof Error ? e.message : "error"}).`,
      );
    } finally {
      setBusy(false);
    }
  }

  async function addJob(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setNotice("");

    try {
      const response = await fetch(`${API}/api/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...form,
          location: form.location || null,
          url: form.url || null,
          skills: splitCommas(form.skills),
          nice_to_have_skills: splitCommas(form.nice_to_have_skills),
          salary_min: numberOrNull(form.salary_min),
          salary_max: numberOrNull(form.salary_max),
          salary_currency: form.salary_currency.trim().toUpperCase(),
          source: "manual",
        }),
      });

      const data = await response.json().catch(() => null);

      if (!response.ok) {
        throw new Error(
          errorText(data, `Could not add job (HTTP ${response.status})`),
        );
      }

      setShowJobForm(false);
      setForm(emptyJobForm());
      setPasteText("");
      setImportMessage("");
      setPasteOpen(true);

      setNotice("Job added successfully.");
      await load();
    } catch (e) {
      const message = e instanceof Error ? e.message : "Something went wrong.";
      setNotice(message);
      setFormError(message);
    } finally {
      setBusy(false);
    }
  }

  // Upload a PDF resume, let the AI extract details, and pre-fill the profile
  // form. Nothing is saved until the user reviews it and clicks "Save profile".
  async function parseResume(file: File) {
    setParsing(true);
    setParseMessage("");
    setFormError("");

    try {
      const body = new FormData();
      body.append("file", file);

      const response = await fetch(`${API}/api/resume/parse`, {
        method: "POST",
        body,
      });
      const data = await response.json().catch(() => null);

      if (!response.ok) {
        throw new Error(errorText(data, "Could not read this resume."));
      }

      setProfileForm((prev) => profileToForm(data?.extracted ?? {}, prev));
      setProfileErrors({});
      setParseMessage(
        "Resume read. Review every section below, fill anything marked *, then click Save profile.",
      );
    } catch (e) {
      setFormError(
        e instanceof Error
          ? e.message
          : "Could not read this resume. Check that the API is running.",
      );
    } finally {
      setParsing(false);
    }
  }

  // Generic helpers for the repeatable sections (experience, projects, ...).
  function setItem(key: ListKey, index: number, field: string, value: string) {
    setProfileForm((prev) => ({
      ...prev,
      [key]: (prev[key] as any[]).map((item, i) =>
        i === index ? { ...item, [field]: value } : item,
      ),
    }));
  }
  function addItem(key: ListKey) {
    setProfileForm((prev) => ({
      ...prev,
      [key]: [...(prev[key] as any[]), { ...EMPTY_ENTRY[key] }],
    }));
  }
  function removeItem(key: ListKey, index: number) {
    setProfileForm((prev) => ({
      ...prev,
      [key]: (prev[key] as any[]).filter((_, i) => i !== index),
    }));
    setProfileErrors({});
  }

  async function saveProfile(e: React.FormEvent) {
    e.preventDefault();
    setNotice("");

    // Required-field check before anything is sent.
    const errors = validateProfile(profileForm);
    setProfileErrors(errors);
    if (Object.keys(errors).length > 0) {
      setFormError(
        `Please fill the ${Object.keys(errors).length} required item(s) marked in red.`,
      );
      setTimeout(
        () =>
          document
            .querySelector(".field-error")
            ?.scrollIntoView({ behavior: "smooth", block: "center" }),
        0,
      );
      return;
    }
    setFormError("");
    setBusy(true);

    try {
      const keep = <T extends Record<string, string>>(items: T[]) =>
        items.filter((i) => !isBlankEntry(i));

      const response = await fetch(`${API}/api/profile`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...profileForm,
          target_roles: splitCommas(profileForm.target_roles),
          skills: splitCommas(profileForm.skills),
          work_experience: keep(profileForm.work_experience),
          projects: keep(profileForm.projects),
          education: keep(profileForm.education),
          certifications: keep(profileForm.certifications),
          achievements: profileForm.achievements
            .split("\n")
            .map((a) => a.trim())
            .filter(Boolean),
        }),
      });

      const data = await response.json().catch(() => null);

      if (!response.ok) {
        throw new Error(errorText(data, "Could not save profile."));
      }

      setShowProfile(false);
      setNotice("Profile saved and job matches recalculated.");
      await load();
    } catch (e) {
      const message =
        e instanceof Error
          ? e.message
          : "Could not save profile. Check that the API is running.";

      setNotice(message);
      setFormError(message);
    } finally {
      setBusy(false);
    }
  }

  // Reloads only the resume list, so unsaved profile edits are not lost.
  async function loadResumes() {
    try {
      const response = await fetch(`${API}/api/resumes`);
      if (response.ok) setResumes(await response.json());
    } catch {
      // The next full reload will show a connection problem.
    }
  }

  async function uploadResumeFile(file: File, fillForm = false) {
    setResumeMessage("");
    setResumeError("");
    setResumeBusy(true);
    try {
      const body = new FormData();
      body.append("file", file);

      const response = await fetch(`${API}/api/resumes`, {
        method: "POST",
        body,
      });
      const data = await response.json().catch(() => null);

      if (!response.ok) {
        throw new Error(errorText(data, "Could not save this resume."));
      }

      setResumeMessage(`Saved "${data.label}".`);
      await loadResumes();

      // The active resume (the first one) fills the profile form. The top
      // upload button asks for the form to be filled whatever happens.
      if (data.is_active || fillForm) await fillProfileFromResume(data);
    } catch (err) {
      setResumeError(
        err instanceof Error ? err.message : "Could not save this resume.",
      );
    } finally {
      setResumeBusy(false);
    }
  }

  async function changeResume(
    resume: ResumeFile,
    changes: { label?: string; is_active?: boolean },
    success: string,
  ): Promise<boolean> {
    setResumeMessage("");
    setResumeError("");
    try {
      const response = await fetch(`${API}/api/resumes/${resume.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(changes),
      });
      const data = await response.json().catch(() => null);

      if (!response.ok) {
        throw new Error(errorText(data, "Could not update this resume."));
      }

      setResumeMessage(success);
      await loadResumes();
      return true;
    } catch (err) {
      setResumeError(
        err instanceof Error ? err.message : "Could not update this resume.",
      );
      return false;
    }
  }

  // Fills the profile FORM from a stored resume. Nothing is saved until you
  // review it and click Save profile (closing the window undoes it).
  async function fillProfileFromResume(resume: ResumeFile, refresh = false) {
    setResumeMessage("");
    setResumeError("");
    setFillingId(resume.id);
    try {
      const response = await fetch(
        `${API}/api/resumes/${resume.id}/extract${refresh ? "?refresh=true" : ""}`,
        { method: "POST" },
      );
      const data = await response.json().catch(() => null);

      if (!response.ok) {
        throw new Error(errorText(data, "Could not read this resume."));
      }

      const found = data?.extracted ?? {};
      setProfileForm((prev) => profileToForm(found, prev));
      setProfileErrors({});
      setFormError("");

      const count = (v: unknown) => (Array.isArray(v) ? v.length : 0);
      setResumeMessage(
        `Profile form filled from "${resume.label}"` +
          (data?.cached ? " (saved reading)" : "") +
          `: ${count(found.skills)} skills, ${count(found.work_experience)} jobs, ` +
          `${count(found.projects)} projects, ${count(found.education)} education entries. ` +
          "Review the sections below, then click Save profile. " +
          "Close this window without saving to undo.",
      );
      await loadResumes();

      setTimeout(
        () =>
          document
            .getElementById("profile-career")
            ?.scrollIntoView({ behavior: "smooth", block: "start" }),
        0,
      );
    } catch (err) {
      setResumeError(
        `"${resume.label}" is your active resume, but its details could not be read: ` +
          (err instanceof Error ? err.message : "unknown error") +
          " Your profile form was not changed.",
      );
    } finally {
      setFillingId(null);
    }
  }

  async function activateResume(resume: ResumeFile) {
    const ok = await changeResume(
      resume,
      { is_active: true },
      `"${resume.label}" is now your active resume.`,
    );
    if (ok) await fillProfileFromResume(resume);
  }

  function renameResume(resume: ResumeFile) {
    const label = window.prompt("New name for this resume:", resume.label);
    if (label === null || label.trim() === resume.label) return;
    changeResume(resume, { label }, "Resume renamed.");
  }

  async function deleteResume(resume: ResumeFile) {
    if (!window.confirm(`Delete the resume "${resume.label}"?`)) return;
    setResumeMessage("");
    setResumeError("");
    try {
      const response = await fetch(`${API}/api/resumes/${resume.id}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new Error(errorText(data, "Could not delete this resume."));
      }
      setResumeMessage("Resume deleted.");
      await loadResumes();
    } catch (err) {
      setResumeError(
        err instanceof Error ? err.message : "Could not delete this resume.",
      );
    }
  }

  function toggleArrangement(value: string) {
    setPreferencesForm((prev) => ({
      ...prev,
      work_arrangements: prev.work_arrangements.includes(value)
        ? prev.work_arrangements.filter((v) => v !== value)
        : [...prev.work_arrangements, value],
    }));
  }

  async function savePreferences(e: React.FormEvent) {
    e.preventDefault();
    setNotice("");

    const errors = validatePreferences(preferencesForm);
    setPreferencesErrors(errors);
    if (Object.keys(errors).length > 0) {
      setPreferencesError("Please fix the items marked in red.");
      return;
    }
    setPreferencesError("");
    setPreferencesBusy(true);

    try {
      const toNumber = (v: string) => (v.trim() === "" ? null : Number(v));

      const response = await fetch(`${API}/api/preferences`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          locations: splitCommas(preferencesForm.locations),
          experience_level: preferencesForm.experience_level,
          work_arrangements: preferencesForm.work_arrangements,
          salary_min: toNumber(preferencesForm.salary_min),
          salary_max: toNumber(preferencesForm.salary_max),
          salary_currency: preferencesForm.salary_currency.trim().toUpperCase(),
          salary_period: preferencesForm.salary_period,
        }),
      });

      const data = await response.json().catch(() => null);

      if (!response.ok) {
        throw new Error(errorText(data, "Could not save job preferences."));
      }

      setShowPreferences(false);
      setNotice("Job preferences saved.");
    } catch (e) {
      setPreferencesError(
        e instanceof Error
          ? e.message
          : "Could not save job preferences. Check that the API is running.",
      );
    } finally {
      setPreferencesBusy(false);
    }
  }

  async function trackJob(job: Job) {
    // Prevent creating a duplicate application for the same job.
    const existingApplication = applications.find(
      (application) =>
        String(application.job_id).trim() === String(job.id).trim() ||
        (application.job_title.trim().toLowerCase() ===
          job.title.trim().toLowerCase() &&
          application.company.trim().toLowerCase() ===
            job.company.trim().toLowerCase()),
    );

    if (existingApplication) {
      const status = statusLabel(existingApplication.status);

      setNotice(
        `${job.company} is already tracked with status "${status}". Open Applications in the left menu to see it.`,
      );


      return;
    }

    setBusy(true);
    setNotice("");

    try {
      const response = await fetch(`${API}/api/applications`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          job_id: job.id,
          status: "saved",
        }),
      });

      const data = await response.json().catch(() => null);

      if (!response.ok) {
        throw new Error(errorText(data, "Could not save application."));
      }

      setNotice(
        `Tracking started for ${job.company}. Open Applications in the left menu to see the board.`,
      );
      await load();
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Could not track job.");
    } finally {
      setBusy(false);
    }
  }

  async function changeApplicationStatus(
    application: Application,
    nextStatus: string,
  ) {
    if (!nextStatus) return;

    // Do not submit a status that is not allowed by the API response.
    if (!application.allowed_next?.includes(nextStatus)) {
      setNotice("That status transition is not allowed.");
      return;
    }

    setNotice("");

    try {
      const response = await fetch(
        `${API}/api/applications/${application.id}/status`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: nextStatus }),
        },
      );

      const data = await response.json().catch(() => null);

      if (!response.ok) {
        setNotice(errorText(data, "Could not update application status."));
      } else {
        setNotice("Application status updated.");
      }
    } catch {
      setNotice("Could not reach the API.");
    }

    await load();
  }

  function chooseTrackerView(view: "board" | "list") {
    setTrackerView(view);
    saveSetting("cp-tracker-view", view);
  }

  function toggleHideEmpty(value: boolean) {
    setHideEmpty(value);
    saveSetting("cp-hide-empty", value ? "yes" : "no");
  }

  function handleCardDragStart(e: React.DragEvent<HTMLElement>, appId: string) {
    e.dataTransfer.setData("text/plain", appId);
    e.dataTransfer.effectAllowed = "move";
    // Delay the state change so the browser can take its drag image first.
    window.setTimeout(() => setDraggedAppId(appId), 0);
  }

  function handleCardDragEnd() {
    setDraggedAppId(null);
    setDragOverStatus(null);
  }

  function handleColumnDragOver(
    e: React.DragEvent<HTMLElement>,
    statusKey: string,
  ) {
    if (!draggedAppId) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (dragOverStatus !== statusKey) setDragOverStatus(statusKey);
  }

  async function handleColumnDrop(
    e: React.DragEvent<HTMLElement>,
    targetStatus: string,
  ) {
    e.preventDefault();
    const appId = e.dataTransfer.getData("text/plain") || draggedAppId;
    setDraggedAppId(null);
    setDragOverStatus(null);

    const app = applications.find((a) => a.id === appId);
    if (!app || app.status === targetStatus) return;

    await changeApplicationStatus(app, targetStatus);
  }

  function goTo(next: Page) {
    window.location.hash = next === "applications" ? "#/applications" : "#/";
    setPage(next);
    window.scrollTo({ top: 0 });
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <Sparkles size={20} />
          </div>
          <div>
            <strong>CareerPilot</strong>
            <span>AI JOB COPILOT</span>
          </div>
        </div>

        <div className="workspace-label">WORKSPACE</div>

        <a
          className={`nav ${page === "overview" ? "active" : ""}`}
          href="#/"
          onClick={(e) => {
            e.preventDefault();
            goTo("overview");
          }}
        >
          <Gauge size={18} /> Overview
        </a>

        <a
          className={`nav ${page === "applications" ? "active" : ""}`}
          href="#/applications"
          onClick={(e) => {
            e.preventDefault();
            goTo("applications");
          }}
        >
          <Kanban size={18} /> Applications
          <span className="nav-count">{applications.length}</span>
        </a>

        <a className="nav" onClick={() => setShowProfile(true)}>
          <Target size={18} /> My profile
        </a>

        <a className="nav" onClick={() => setShowPreferences(true)}>
          <SlidersHorizontal size={18} /> Job preferences
        </a>

        <div className="sidebar-bottom">
          <div className="secure-icon">
            <ShieldCheck size={18} />
          </div>
          <strong>Human-first automation</strong>
          <p>AI prepares. You approve. You stay in control.</p>
          <span className="status-pill">
            <i /> Approval gate enabled
          </span>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div>
            <div className="eyebrow">
              {page === "applications"
                ? "YOUR PIPELINE"
                : "YOUR CAREER, IN CONTROL"}
            </div>
            <h1>
              {page === "applications" ? "Applications" : "Job hunt overview"}
            </h1>
            <p>
              {page === "applications"
                ? "Drag a card to another column, or use its Move to menu."
                : "Find the right opportunities. Spend less time on repetitive work."}
            </p>
          </div>

          <button
            className="profile-button"
            onClick={() => setShowProfile(true)}
          >
            <span className="avatar">
              {profile?.full_name
                ? profile.full_name.slice(0, 1).toUpperCase()
                : "Y"}
            </span>
            <span>{profile?.full_name || "Your profile"}</span>
          </button>
        </header>

        {notice && (
          <div className="notice">
            <CircleAlert size={16} />
            {notice}
            <button onClick={() => setNotice("")} aria-label="Dismiss">
              <X size={15} />
            </button>
          </div>
        )}

        {page === "overview" ? (
          <>
        <section className="hero">
          <div className="hero-copy">
            <div className="hero-tag">
              <Sparkles size={14} /> YOUR AI CAREER COPILOT
            </div>
            <h2>
              Make your next move
              <br />
              <em>your best move.</em>
            </h2>
            <p>
              One focused workspace for job discovery, experience-based
              matching, and application tracking.
            </p>
            <button
              className="primary-button"
              onClick={() => setShowJobForm(true)}
            >
              <Plus size={17} /> Add a job <ArrowUpRight size={16} />
            </button>
          </div>

          <div className="hero-art">
            <div className="orbit orbit-one" />
            <div className="orbit orbit-two" />
            <div className="core">
              <Sparkles size={34} />
            </div>
            <div className="float-card card-match">
              <Target size={17} />
              <div>
                <strong>Match engine</strong>
                <small>Skills-aware scoring</small>
              </div>
              <b>AI</b>
            </div>
            <div className="float-card card-safe">
              <ShieldCheck size={18} />
              <div>
                <strong>You stay in control</strong>
                <small>Approval before submit</small>
              </div>
              <CheckCircle2 size={18} />
            </div>
            <div className="mini-dot dot-a" />
            <div className="mini-dot dot-b" />
          </div>
        </section>

        <section className="stats-grid">
          <div className="stat-card">
            <div className="stat-top">
              <span>Jobs discovered</span>
              <span className="stat-icon purple">
                <BriefcaseBusiness size={18} />
              </span>
            </div>
            <strong>{jobs.length}</strong>
            <small>In your opportunity list</small>
          </div>

          <div
            className="stat-card clickable"
            role="button"
            tabIndex={0}
            onClick={() => goTo("applications")}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") goTo("applications");
            }}
          >
            <div className="stat-top">
              <span>Applications tracked</span>
              <span className="stat-icon blue">
                <Activity size={18} />
              </span>
            </div>
            <strong>{applications.length}</strong>
            <small>{appliedCount} submitted or progressed</small>
          </div>

          <div className="stat-card">
            <div className="stat-top">
              <span>Awaiting review</span>
              <span className="stat-icon amber">
                <ShieldCheck size={18} />
              </span>
            </div>
            <strong>{reviewCount}</strong>
            <small>Human approval queue</small>
          </div>

          <div className="stat-card">
            <div className="stat-top">
              <span>Profile readiness</span>
              <span className="stat-icon green">
                <Target size={18} />
              </span>
            </div>
            <strong>{profile?.skills?.length ? "Ready" : "Setup"}</strong>
            <small>
              {profile?.skills?.length
                ? `${profile.skills.length} skills in your profile`
                : "Add skills to improve matching"}
            </small>
          </div>
        </section>

        <section className="section-head">
          <div>
            <h2>Opportunity feed</h2>
            <p>Ranked by how well each role fits your skills.</p>
          </div>

          <div className="actions">
            <label className="searchbox">
              <Search size={16} />
              <input
                placeholder="Search roles or companies"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>

            <button
              className="secondary-button"
              onClick={() => setShowJobForm(true)}
            >
              <Plus size={16} /> Add job
            </button>
          </div>
        </section>

        <section className="filter-bar">
          <label>
            Status
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option value="all">All</option>
              <option value="none">Not tracked</option>
              {Object.entries(STATUS_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>

          <label>
            Min match
            <select
              value={minMatch}
              onChange={(e) => setMinMatch(Number(e.target.value))}
            >
              <option value={0}>Any</option>
              <option value={25}>25%+</option>
              <option value={50}>50%+</option>
              <option value={75}>75%+</option>
            </select>
          </label>

          <label>
            Source
            <select
              value={sourceFilter}
              onChange={(e) => setSourceFilter(e.target.value)}
            >
              <option value="all">All</option>
              {sources.map((src) => (
                <option key={src} value={src}>
                  {src}
                </option>
              ))}
            </select>
          </label>

          <label>
            Sort by
            <select
              value={sortBy}
              onChange={(e) =>
                setSortBy(e.target.value as "best" | "newest" | "company")
              }
            >
              <option value="best">Best match</option>
              <option value="newest">Newest</option>
              <option value="company">Company</option>
            </select>
          </label>
        </section>

        {jobs.some((j) => !j.match_breakdown?.length) && (
          <div className="rescore-banner">
            <span>Some jobs were scored before score explanations existed.</span>
            <button
              className="secondary-button"
              onClick={() => void rescoreJobs()}
              disabled={busy}
            >
              Re-score all jobs
            </button>
          </div>
        )}

        <section className="jobs-list">
          {visibleJobs.length === 0 ? (
            <div className="empty-state">
              <div className="empty-icon">
                <BriefcaseBusiness size={25} />
              </div>
              <h3>Your next opportunity starts here</h3>
              <p>
                Add a job posting to start scoring opportunities against your
                profile.
              </p>
              <button
                className="primary-button"
                onClick={() => setShowJobForm(true)}
              >
                <Plus size={16} /> Add your first job
              </button>
            </div>
          ) : (
            visibleJobs.map((job) => (
              <article className="job-card" key={job.id}>
                <div className="company-logo">
                  {job.company.slice(0, 1).toUpperCase()}
                </div>

                <div className="job-info">
                  <div className="job-title-row">
                    <h3>{job.title}</h3>
                    <span
                      className={`match ${
                        job.match_score >= 70
                          ? "high"
                          : job.match_score >= 40
                            ? "mid"
                            : "low"
                      }`}
                    >
                      {Math.round(job.match_score)}% match
                    </span>
                  </div>

                  <p>
                    {job.company}
                    {job.location ? ` · ${job.location}` : ""}
                  </p>

                  <div className="skill-list">
                    {job.skills.slice(0, 5).map((skill) => (
                      <span key={skill}>{skill}</span>
                    ))}
                  </div>

                  {job.match_breakdown?.length ? (
                    <MatchBreakdown job={job} />
                  ) : (
                    <MatchReasons reasons={job.match_reasons} />
                  )}

                  {dismissingId === job.id && (
                    <div className="dismiss-panel" role="group" aria-label="Why not interested?">
                      <span>Why not?</span>
                      {DISMISS_REASONS.map((r) => (
                        <button
                          type="button"
                          key={r.key}
                          onClick={() => void saveFeedback(job, "not_interested", r.key)}
                        >
                          {r.label}
                        </button>
                      ))}
                      <button
                        type="button"
                        className="link-button"
                        onClick={() => setDismissingId(null)}
                      >
                        Cancel
                      </button>
                    </div>
                  )}
                </div>

                <div className="job-actions">
                  <button
                    type="button"
                    className="ghost-button"
                    aria-expanded={dismissingId === job.id}
                    onClick={() =>
                      setDismissingId(dismissingId === job.id ? null : job.id)
                    }
                  >
                    <EyeOff size={14} /> Not interested
                  </button>
                  {job.url && (
                    <a
                      href={job.url}
                      target="_blank"
                      rel="noreferrer"
                      className="icon-link"
                      aria-label="Open job posting"
                    >
                      <ArrowUpRight size={17} />
                    </a>
                  )}

                  <button
                    className="secondary-button"
                    onClick={() => void trackJob(job)}
                    disabled={busy}
                  >
                    Track job
                  </button>
                </div>
              </article>
            ))
          )}
        </section>

        {dismissTip && (
          <div className="rescore-banner">
            <span>
              You passed on {dismissTip.count} jobs because of “
              {dismissTip.label.toLowerCase()}”. Your{" "}
              {dismissTip.page === "profile" ? "profile" : "job preferences"} may
              need a tweak so these score lower.
            </span>
            <button
              className="secondary-button"
              onClick={() =>
                dismissTip.page === "profile"
                  ? setShowProfile(true)
                  : setShowPreferences(true)
              }
            >
              Open {dismissTip.page === "profile" ? "my profile" : "job preferences"}
            </button>
          </div>
        )}

        {dismissedJobs.length > 0 && (
          <details className="dismissed">
            <summary>Not interested ({dismissedJobs.length})</summary>
            {dismissedJobs.map((job) => (
              <div className="dismissed-row" key={job.id}>
                <div>
                  <strong>{job.title}</strong>
                  <small>
                    {job.company} · {reasonLabel(job.feedback_reason)}
                  </small>
                </div>
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => void saveFeedback(job, "")}
                >
                  <RotateCcw size={13} /> Restore
                </button>
              </div>
            ))}
          </details>
        )}

          </>
        ) : (
          <>
        {applications.length > 0 && (
          <section className="kb-toolbar page-toolbar">
            <span className="kb-summary">
              {applications.length} tracked ·{" "}
              {
                applications.filter(
                  (a) => !["rejected", "withdrawn"].includes(a.status),
                ).length
              }{" "}
              in progress
            </span>

            {trackerView === "board" && (
              <>
                <label className="kb-check">
                  <input
                    type="checkbox"
                    checked={hideEmpty}
                    onChange={(e) => toggleHideEmpty(e.target.checked)}
                  />
                  Hide empty columns
                </label>

                <span className="kb-legend">
                  <Lock size={12} /> Approved and Submitted unlock with the
                  approval workflow
                </span>
              </>
            )}

            <div className="kb-view-toggle" role="group" aria-label="Tracker view">
              <button
                type="button"
                className={trackerView === "board" ? "active" : ""}
                aria-pressed={trackerView === "board"}
                onClick={() => chooseTrackerView("board")}
              >
                <Kanban size={15} /> Board
              </button>
              <button
                type="button"
                className={trackerView === "list" ? "active" : ""}
                aria-pressed={trackerView === "list"}
                onClick={() => chooseTrackerView("list")}
              >
                <List size={15} /> List
              </button>
            </div>
          </section>
        )}

        {applications.length === 0 ? (
          <section className="empty-state">
            <div className="empty-icon">
              <Kanban size={25} />
            </div>
            <h3>No applications yet</h3>
            <p>
              Track a job from the opportunity feed and it will appear here as
              a card you can move across the board.
            </p>
            <button className="primary-button" onClick={() => goTo("overview")}>
              Go to the opportunity feed
            </button>
          </section>
        ) : trackerView === "board" ? (
          <section className="kanban-board full" aria-label="Application board">
            {KANBAN_STATUSES.filter(
              (statusKey) =>
                !hideEmpty ||
                draggedAppId !== null ||
                applications.some((a) => a.status === statusKey),
            ).map((statusKey) => {
              const columnApps = applications.filter(
                (a) => a.status === statusKey,
              );
              const isLocked = LOCKED_STATUSES.includes(statusKey);
              const isCurrent = draggedApplication?.status === statusKey;
              const isAllowedDrop =
                draggedApplication != null &&
                !isCurrent &&
                (draggedApplication.allowed_next ?? []).includes(statusKey);
              const isGatedDrop =
                draggedApplication != null &&
                !isCurrent &&
                (draggedApplication.gated_next ?? []).includes(statusKey);
              const isBlockedDrop =
                draggedApplication != null &&
                !isCurrent &&
                !isAllowedDrop &&
                !isGatedDrop;

              const columnClass = [
                "kanban-column",
                isLocked ? "is-locked" : "",
                isAllowedDrop ? "can-drop" : "",
                isGatedDrop ? "gated-drop" : "",
                isBlockedDrop ? "no-drop" : "",
                dragOverStatus === statusKey && isAllowedDrop ? "drag-over" : "",
              ]
                .filter(Boolean)
                .join(" ");

              return (
                <div
                  key={statusKey}
                  className={columnClass}
                  data-status={statusKey}
                  onDragOver={(e) => handleColumnDragOver(e, statusKey)}
                  onDragLeave={() => {
                    if (dragOverStatus === statusKey) setDragOverStatus(null);
                  }}
                  onDrop={(e) => void handleColumnDrop(e, statusKey)}
                >
                  <div className="kanban-column-head">
                    <span className="kanban-dot" />
                    <h3>{statusLabel(statusKey)}</h3>
                    {isLocked && (
                      <Lock
                        size={12}
                        className="kanban-lock"
                        aria-label="Locked until the approval workflow is built"
                      />
                    )}
                    <span className="kanban-count">{columnApps.length}</span>
                  </div>

                  <div className="kanban-column-body">
                    {columnApps.length === 0 && (
                      <div className="kanban-empty">
                        {isAllowedDrop
                          ? "Drop here"
                          : isGatedDrop
                            ? "Locked"
                            : isLocked
                              ? "Locked until approval workflow"
                              : "Nothing here"}
                      </div>
                    )}

                    {columnApps.map((application) => {
                      const nextStatuses = application.allowed_next ?? [];
                      const linkedJob = jobsById.get(String(application.job_id));
                      const age = timeInStage(application);
                      const historyCount = (application.status_history ?? [])
                        .length;

                      return (
                        <article
                          key={application.id}
                          className={`kanban-card ${
                            draggedAppId === application.id ? "is-dragging" : ""
                          }`}
                          draggable
                          onDragStart={(e) =>
                            handleCardDragStart(e, application.id)
                          }
                          onDragEnd={handleCardDragEnd}
                        >
                          <div className="kanban-card-top">
                            <div className="kanban-card-title">
                              <strong>{application.job_title}</strong>
                              <span>
                                {application.company}
                                {linkedJob?.location
                                  ? ` · ${linkedJob.location}`
                                  : ""}
                              </span>
                            </div>

                            {linkedJob && (
                              <span
                                className={`match ${
                                  linkedJob.match_score >= 70
                                    ? "high"
                                    : linkedJob.match_score >= 40
                                      ? "mid"
                                      : "low"
                                }`}
                              >
                                {Math.round(linkedJob.match_score)}%
                              </span>
                            )}
                          </div>

                          {(application.resume_label || age) && (
                            <div className="kanban-meta">
                              {application.resume_label && (
                                <span title="Resume used">
                                  {application.resume_label}
                                </span>
                              )}
                              {age && <span>In stage: {age}</span>}
                            </div>
                          )}

                          <div className="kanban-card-controls">
                            <select
                              value=""
                              disabled={nextStatuses.length === 0}
                              aria-label={`Move ${application.job_title} to another status`}
                              onChange={(e) => {
                                void changeApplicationStatus(
                                  application,
                                  e.target.value,
                                );
                              }}
                            >
                              <option value="">
                                {nextStatuses.length
                                  ? "Move to..."
                                  : "Final status"}
                              </option>
                              {nextStatuses.map((status) => (
                                <option key={status} value={status}>
                                  {statusLabel(status)}
                                </option>
                              ))}
                            </select>

                            {linkedJob?.url && (
                              <a
                                href={linkedJob.url}
                                target="_blank"
                                rel="noreferrer"
                                className="icon-link small"
                                aria-label="Open job posting"
                                draggable={false}
                              >
                                <ArrowUpRight size={14} />
                              </a>
                            )}
                          </div>

                          <details className="status-history">
                            <summary>History ({historyCount})</summary>
                            <ol>
                              {(application.status_history ?? []).map(
                                (entry, i) => (
                                  <li key={`${entry.at}-${i}`}>
                                    <strong>
                                      {entry.from_status
                                        ? `${statusLabel(entry.from_status)} → ${statusLabel(entry.to_status)}`
                                        : `Created as ${statusLabel(entry.to_status)}`}
                                    </strong>
                                    <span>
                                      {new Date(entry.at).toLocaleString()} ·{" "}
                                      {entry.actor}
                                    </span>
                                    {entry.note && <em>{entry.note}</em>}
                                  </li>
                                ),
                              )}
                            </ol>
                          </details>
                        </article>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </section>
        ) : (
          <section className="application-list">
            {applications.map((application) => {
              const nextStatuses = application.allowed_next ?? [];

              return (
                <article className="application-row" key={application.id}>
                  <div className="application-symbol">
                    <BriefcaseBusiness size={17} />
                  </div>

                  <div className="application-name">
                    <strong>{application.job_title}</strong>
                    <span>{application.company}</span>
                    {application.resume_label && (
                      <small className="muted-note">
                        Resume: {application.resume_label}
                      </small>
                    )}
                  </div>

                  <span className={`status status-${application.status}`}>
                    {statusLabel(application.status)}
                  </span>

                  <select
                    value=""
                    disabled={nextStatuses.length === 0}
                    aria-label={`Move ${application.job_title} to next status`}
                    onChange={(e) => {
                      void changeApplicationStatus(application, e.target.value);
                    }}
                  >
                    <option value="">
                      {nextStatuses.length ? "Move to..." : "Final status"}
                    </option>

                    {nextStatuses.map((status) => (
                      <option key={status} value={status}>
                        {statusLabel(status)}
                      </option>
                    ))}
                  </select>

                  {(application.gated_next ?? []).length > 0 && (
                    <p className="locked-note">
                      Approval and submission are locked until the approval
                      workflow is built.
                    </p>
                  )}

                  <details className="status-history">
                    <summary>
                      History ({(application.status_history ?? []).length})
                    </summary>
                    <ol>
                      {(application.status_history ?? []).map((entry, i) => (
                        <li key={`${entry.at}-${i}`}>
                          <strong>
                            {entry.from_status
                              ? `${statusLabel(entry.from_status)} → ${statusLabel(entry.to_status)}`
                              : `Created as ${statusLabel(entry.to_status)}`}
                          </strong>
                          <span>
                            {new Date(entry.at).toLocaleString()} · {entry.actor}
                          </span>
                          {entry.note && <em>{entry.note}</em>}
                        </li>
                      ))}
                    </ol>
                  </details>
                </article>
              );
            })}
          </section>
        )}

          </>
        )}

        <footer>
          CAREERPILOT AI <span>·</span> Thoughtful automation, human decisions.
        </footer>
      </main>

      {showJobForm && (
        <div
          className="modal-backdrop"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setShowJobForm(false);
          }}
        >
          <form className="modal" onSubmit={addJob}>
            <div className="modal-head">
              <div>
                <div className="eyebrow">OPPORTUNITY INTAKE</div>
                <h2>Add a job</h2>
              </div>
              <button
                type="button"
                className="close-button"
                onClick={() => setShowJobForm(false)}
              >
                <X size={18} />
              </button>
            </div>

            <details
              className="paste-import"
              open={pasteOpen}
              onToggle={(e) => setPasteOpen(e.currentTarget.open)}
            >
              <summary>Paste a job posting to fill this form</summary>
              <textarea
                rows={5}
                value={pasteText}
                onChange={(e) => setPasteText(e.target.value)}
                placeholder="Paste the whole job posting here: title, company, location, requirements, salary..."
              />
              <div className="paste-import-row">
                <button
                  type="button"
                  className="secondary-button"
                  disabled={importing || pasteText.trim().length < 20}
                  onClick={() => void importFromText()}
                >
                  {importing ? "Reading..." : "Fill the form"}
                </button>
                <small>Nothing is saved until you press Save opportunity.</small>
              </div>
              {importMessage && <p className="import-message">{importMessage}</p>}
            </details>

            <label>
              Job title
              <input
                required
                value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })}
                placeholder="AI Engineer Intern"
              />
            </label>

            <label>
              Company
              <input
                required
                value={form.company}
                onChange={(e) => setForm({ ...form, company: e.target.value })}
                placeholder="Company name"
              />
            </label>

            <label>
              Location
              <input
                value={form.location}
                onChange={(e) => setForm({ ...form, location: e.target.value })}
                placeholder="Remote / Bengaluru"
              />
            </label>

            <label>
              Job posting URL
              <input
                type="url"
                value={form.url}
                onChange={(e) => setForm({ ...form, url: e.target.value })}
                placeholder="https://..."
              />
            </label>

            <label>
              Required skills <small>(comma-separated)</small>
              <input
                value={form.skills}
                onChange={(e) => setForm({ ...form, skills: e.target.value })}
                placeholder="Python, AI, FastAPI"
              />
            </label>

            <details className="form-more">
              <summary>More details (optional, improves the match score)</summary>

              <label>
                Nice-to-have skills <small>(comma-separated)</small>
                <input
                  value={form.nice_to_have_skills}
                  onChange={(e) =>
                    setForm({ ...form, nice_to_have_skills: e.target.value })
                  }
                  placeholder="Docker, Jest"
                />
              </label>

              <div className="grid-2">
                <label>
                  Work style
                  <select
                    value={form.work_arrangement}
                    onChange={(e) =>
                      setForm({ ...form, work_arrangement: e.target.value })
                    }
                  >
                    <option value="">Not stated</option>
                    <option value="remote">Remote</option>
                    <option value="hybrid">Hybrid</option>
                    <option value="onsite">On-site</option>
                  </select>
                </label>

                <label>
                  Experience level
                  <select
                    value={form.experience_level}
                    onChange={(e) =>
                      setForm({ ...form, experience_level: e.target.value })
                    }
                  >
                    <option value="">Not stated</option>
                    <option value="intern">Intern</option>
                    <option value="entry">Entry level</option>
                    <option value="mid">Mid level</option>
                    <option value="senior">Senior</option>
                    <option value="lead">Lead</option>
                  </select>
                </label>
              </div>

              <div className="grid-2">
                <label>
                  Salary from
                  <input
                    type="number"
                    min={0}
                    value={form.salary_min}
                    onChange={(e) =>
                      setForm({ ...form, salary_min: e.target.value })
                    }
                  />
                </label>
                <label>
                  Salary up to
                  <input
                    type="number"
                    min={0}
                    value={form.salary_max}
                    onChange={(e) =>
                      setForm({ ...form, salary_max: e.target.value })
                    }
                  />
                </label>
                <label>
                  Currency <small>(INR, USD...)</small>
                  <input
                    maxLength={3}
                    value={form.salary_currency}
                    onChange={(e) =>
                      setForm({ ...form, salary_currency: e.target.value })
                    }
                  />
                </label>
                <label>
                  Per
                  <select
                    value={form.salary_period}
                    onChange={(e) =>
                      setForm({ ...form, salary_period: e.target.value })
                    }
                  >
                    <option value="year">Year</option>
                    <option value="month">Month</option>
                  </select>
                </label>
              </div>
            </details>

            <label>
              Description
              <textarea
                rows={3}
                value={form.description}
                onChange={(e) =>
                  setForm({ ...form, description: e.target.value })
                }
                placeholder="Paste the job description here..."
              />
            </label>

            {formError && (
              <div className="form-error" role="alert">
                {formError}
              </div>
            )}

            <div className="modal-actions">
              <button
                type="button"
                className="secondary-button"
                onClick={() => setShowJobForm(false)}
              >
                Cancel
              </button>
              <button className="primary-button" disabled={busy}>
                {busy ? "Saving..." : "Save opportunity"}
              </button>
            </div>
          </form>
        </div>
      )}

      {showProfile && (
        <div
          className="modal-backdrop"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setShowProfile(false);
          }}
        >
          <form className="modal wide" onSubmit={saveProfile} noValidate>
            <div className="modal-head">
              <div>
                <div className="eyebrow">YOUR RESUME PROFILE</div>
                <h2>Profile & resume</h2>
              </div>
              <button
                type="button"
                className="close-button"
                onClick={() => setShowProfile(false)}
              >
                <X size={18} />
              </button>
            </div>

            <div className="resume-upload">
              <label className="upload-button">
                <Upload size={15} />
                {parsing || resumeBusy || fillingId !== null
                  ? "Reading resume..."
                  : "Upload resume (PDF) and auto-fill"}
                <input
                  type="file"
                  accept="application/pdf"
                  disabled={parsing || resumeBusy || fillingId !== null}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) {
                      // Saved resumes are kept under "My resumes". With the
                      // limit reached it can only fill the form.
                      if (resumes.length >= MAX_RESUMES) parseResume(file);
                      else uploadResumeFile(file, true);
                    }
                    e.target.value = "";
                  }}
                />
              </label>
              {parseMessage && <small className="reason ok">{parseMessage}</small>}
              <small className="muted-note">
                Fields marked <span className="req">*</span> are required.
              </small>
            </div>

            <h3 className="section-title">My resumes</h3>
            <small className="muted-note">
              Keep up to {MAX_RESUMES} PDF resumes and choose which one is
              active. New applications use the active resume. The upload
              button at the top saves the file here and fills the form from
              it. With {MAX_RESUMES} resumes saved, it only fills the form and
              does not keep the file.
            </small>

            {resumes.length === 0 && (
              <p className="muted-note">No resumes saved yet.</p>
            )}

            {resumes.map((resume) => (
              <div className="resume-card" key={resume.id}>
                <div className="resume-head">
                  <strong>{resume.label}</strong>
                  {resume.is_active && (
                    <span className="badge-active">Active</span>
                  )}
                </div>
                {fillingId === resume.id && (
                  <small className="reason ok">
                    Reading this resume with AI. The first time can take a few
                    seconds; after that it is instant.
                  </small>
                )}
                <small className="muted-note">
                  {resume.filename} · {resume.page_count} page
                  {resume.page_count === 1 ? "" : "s"} ·{" "}
                  {formatSize(resume.size)}
                </small>
                <div className="resume-actions">
                  <a
                    className="secondary-button"
                    href={`${API}/api/resumes/${resume.id}/file`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    View
                  </a>
                  {!resume.is_active && (
                    <button
                      type="button"
                      className="secondary-button"
                      disabled={fillingId !== null}
                      onClick={() => activateResume(resume)}
                    >
                      Make active
                    </button>
                  )}
                  {resume.is_active && (
                    <>
                      <button
                        type="button"
                        className="secondary-button"
                        disabled={fillingId !== null}
                        onClick={() => fillProfileFromResume(resume)}
                      >
                        Fill profile from this resume
                      </button>
                      <button
                        type="button"
                        className="secondary-button"
                        disabled={fillingId !== null}
                        onClick={() => fillProfileFromResume(resume, true)}
                      >
                        Re-read
                      </button>
                    </>
                  )}
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() => renameResume(resume)}
                  >
                    Rename
                  </button>
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() => deleteResume(resume)}
                  >
                    Delete
                  </button>
                </div>
              </div>
            ))}

            <div className="resume-upload">
              <label className="upload-button">
                <Upload size={15} />
                {resumeBusy ? "Saving resume..." : "Add a resume (PDF)"}
                <input
                  type="file"
                  accept="application/pdf"
                  disabled={resumeBusy || resumes.length >= MAX_RESUMES}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) uploadResumeFile(file);
                    e.target.value = "";
                  }}
                />
              </label>
              {resumes.length >= MAX_RESUMES && (
                <small className="muted-note">
                  You have reached the limit. Delete one to add another.
                </small>
              )}
              {resumeMessage && <small className="reason ok">{resumeMessage}</small>}
            </div>
            {resumeError && (
              <div className="form-error" role="alert">
                {resumeError}
              </div>
            )}

            <h3 className="section-title">Contact</h3>
            <div className="grid-2">
              <Field label="Full name" required error={profileErrors.full_name}>
                <input
                  value={profileForm.full_name}
                  onChange={(e) => setProfileForm({ ...profileForm, full_name: e.target.value })}
                  placeholder="Your name"
                />
              </Field>
              <Field label="Email" required error={profileErrors.email}>
                <input
                  type="email"
                  value={profileForm.email}
                  onChange={(e) => setProfileForm({ ...profileForm, email: e.target.value })}
                  placeholder="you@example.com"
                />
              </Field>
              <Field label="Phone" required error={profileErrors.phone}>
                <input
                  value={profileForm.phone}
                  onChange={(e) => setProfileForm({ ...profileForm, phone: e.target.value })}
                  placeholder="+91 98765 43210"
                />
              </Field>
              <Field label="Location" required error={profileErrors.location}>
                <input
                  value={profileForm.location}
                  onChange={(e) => setProfileForm({ ...profileForm, location: e.target.value })}
                  placeholder="City, State"
                />
              </Field>
              <Field label="GitHub" hint="optional">
                <input
                  value={profileForm.github}
                  onChange={(e) => setProfileForm({ ...profileForm, github: e.target.value })}
                  placeholder="github.com/username"
                />
              </Field>
              <Field label="LinkedIn" hint="optional">
                <input
                  value={profileForm.linkedin}
                  onChange={(e) => setProfileForm({ ...profileForm, linkedin: e.target.value })}
                  placeholder="linkedin.com/in/username"
                />
              </Field>
            </div>
            <Field label="Portfolio / website" hint="optional">
              <input
                value={profileForm.portfolio}
                onChange={(e) => setProfileForm({ ...profileForm, portfolio: e.target.value })}
                placeholder="https://"
              />
            </Field>

            <h3 className="section-title" id="profile-career">
              Career summary
            </h3>
            <Field
              label="Target roles"
              required
              hint="comma-separated"
              error={profileErrors.target_roles}
            >
              <input
                value={profileForm.target_roles}
                onChange={(e) => setProfileForm({ ...profileForm, target_roles: e.target.value })}
                placeholder="Front-End Developer, React Developer"
              />
            </Field>
            <Field
              label="Professional summary"
              required
              error={profileErrors.experience_summary}
            >
              <textarea
                rows={3}
                value={profileForm.experience_summary}
                onChange={(e) =>
                  setProfileForm({ ...profileForm, experience_summary: e.target.value })
                }
                placeholder="2-3 lines about you, your strengths and what you are looking for"
              />
            </Field>
            <Field label="Skills" required hint="comma-separated" error={profileErrors.skills}>
              <input
                value={profileForm.skills}
                onChange={(e) => setProfileForm({ ...profileForm, skills: e.target.value })}
                placeholder="React.js, JavaScript, Python, SQL"
              />
            </Field>

            <h3 className="section-title">Work experience & internships</h3>
            {profileErrors.experience_or_projects && (
              <small className="field-error block">{profileErrors.experience_or_projects}</small>
            )}
            {profileForm.work_experience.map((w, i) => (
              <div className="entry-card" key={`work-${i}`}>
                <div className="entry-head">
                  <strong>Experience #{i + 1}</strong>
                  <button type="button" className="link-button" onClick={() => removeItem("work_experience", i)}>
                    Remove
                  </button>
                </div>
                <div className="grid-2">
                  <Field label="Job title" required error={profileErrors[`work_experience.${i}.title`]}>
                    <input value={w.title} onChange={(e) => setItem("work_experience", i, "title", e.target.value)} />
                  </Field>
                  <Field label="Company" required error={profileErrors[`work_experience.${i}.company`]}>
                    <input value={w.company} onChange={(e) => setItem("work_experience", i, "company", e.target.value)} />
                  </Field>
                  <Field label="Duration" required error={profileErrors[`work_experience.${i}.duration`]}>
                    <input
                      value={w.duration}
                      onChange={(e) => setItem("work_experience", i, "duration", e.target.value)}
                      placeholder="Feb 2025 - Mar 2025"
                    />
                  </Field>
                  <Field label="Location" hint="optional">
                    <input value={w.location} onChange={(e) => setItem("work_experience", i, "location", e.target.value)} />
                  </Field>
                </div>
                <Field
                  label="What you did"
                  required
                  hint="one point per line"
                  error={profileErrors[`work_experience.${i}.description`]}
                >
                  <textarea
                    rows={3}
                    value={w.description}
                    onChange={(e) => setItem("work_experience", i, "description", e.target.value)}
                  />
                </Field>
              </div>
            ))}
            <button type="button" className="add-button" onClick={() => addItem("work_experience")}>
              <Plus size={14} /> Add experience
            </button>

            <h3 className="section-title">Projects</h3>
            {profileForm.projects.map((p, i) => (
              <div className="entry-card" key={`project-${i}`}>
                <div className="entry-head">
                  <strong>Project #{i + 1}</strong>
                  <button type="button" className="link-button" onClick={() => removeItem("projects", i)}>
                    Remove
                  </button>
                </div>
                <div className="grid-2">
                  <Field label="Project name" required error={profileErrors[`projects.${i}.name`]}>
                    <input value={p.name} onChange={(e) => setItem("projects", i, "name", e.target.value)} />
                  </Field>
                  <Field label="Your role" hint="optional">
                    <input value={p.role} onChange={(e) => setItem("projects", i, "role", e.target.value)} />
                  </Field>
                  <Field label="Duration" hint="optional">
                    <input value={p.duration} onChange={(e) => setItem("projects", i, "duration", e.target.value)} />
                  </Field>
                  <Field label="Technologies" hint="optional">
                    <input
                      value={p.technologies}
                      onChange={(e) => setItem("projects", i, "technologies", e.target.value)}
                      placeholder="React, Supabase"
                    />
                  </Field>
                </div>
                <Field
                  label="Description"
                  required
                  hint="one point per line"
                  error={profileErrors[`projects.${i}.description`]}
                >
                  <textarea
                    rows={3}
                    value={p.description}
                    onChange={(e) => setItem("projects", i, "description", e.target.value)}
                  />
                </Field>
              </div>
            ))}
            <button type="button" className="add-button" onClick={() => addItem("projects")}>
              <Plus size={14} /> Add project
            </button>

            <h3 className="section-title">
              Education <span className="req">*</span>
            </h3>
            {profileErrors.education && (
              <small className="field-error block">{profileErrors.education}</small>
            )}
            {profileForm.education.map((x, i) => (
              <div className="entry-card" key={`edu-${i}`}>
                <div className="entry-head">
                  <strong>Education #{i + 1}</strong>
                  <button type="button" className="link-button" onClick={() => removeItem("education", i)}>
                    Remove
                  </button>
                </div>
                <div className="grid-2">
                  <Field label="Institution" required error={profileErrors[`education.${i}.institution`]}>
                    <input value={x.institution} onChange={(e) => setItem("education", i, "institution", e.target.value)} />
                  </Field>
                  <Field label="Degree / course" required error={profileErrors[`education.${i}.degree`]}>
                    <input value={x.degree} onChange={(e) => setItem("education", i, "degree", e.target.value)} />
                  </Field>
                  <Field label="Years" hint="optional">
                    <input
                      value={x.duration}
                      onChange={(e) => setItem("education", i, "duration", e.target.value)}
                      placeholder="2022 - 2025"
                    />
                  </Field>
                  <Field label="GPA / percentage" hint="optional">
                    <input value={x.grade} onChange={(e) => setItem("education", i, "grade", e.target.value)} />
                  </Field>
                </div>
              </div>
            ))}
            <button type="button" className="add-button" onClick={() => addItem("education")}>
              <Plus size={14} /> Add education
            </button>

            <h3 className="section-title">Certifications</h3>
            {profileForm.certifications.map((c, i) => (
              <div className="entry-card" key={`cert-${i}`}>
                <div className="entry-head">
                  <strong>Certification #{i + 1}</strong>
                  <button type="button" className="link-button" onClick={() => removeItem("certifications", i)}>
                    Remove
                  </button>
                </div>
                <div className="grid-2">
                  <Field label="Certification name" required error={profileErrors[`certifications.${i}.name`]}>
                    <input value={c.name} onChange={(e) => setItem("certifications", i, "name", e.target.value)} />
                  </Field>
                  <Field label="Issued by" hint="optional">
                    <input value={c.issuer} onChange={(e) => setItem("certifications", i, "issuer", e.target.value)} />
                  </Field>
                </div>
              </div>
            ))}
            <button type="button" className="add-button" onClick={() => addItem("certifications")}>
              <Plus size={14} /> Add certification
            </button>

            <h3 className="section-title">Achievements</h3>
            <Field label="Awards, hackathons, recognitions" hint="optional, one per line">
              <textarea
                rows={3}
                value={profileForm.achievements}
                onChange={(e) => setProfileForm({ ...profileForm, achievements: e.target.value })}
              />
            </Field>

            {formError && (
              <div className="form-error" role="alert">
                {formError}
              </div>
            )}

            <div className="modal-actions">
              <button
                type="button"
                className="secondary-button"
                onClick={() => setShowProfile(false)}
              >
                Cancel
              </button>
              <button className="primary-button" disabled={busy || parsing}>
                {busy ? "Saving..." : "Save profile"}
              </button>
            </div>
          </form>
        </div>
      )}

      {showPreferences && (
        <div
          className="modal-backdrop"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setShowPreferences(false);
          }}
        >
          <form className="modal" onSubmit={savePreferences} noValidate>
            <div className="modal-head">
              <div>
                <div className="eyebrow">WHAT YOU ARE LOOKING FOR</div>
                <h2>Job preferences</h2>
              </div>
              <button
                type="button"
                className="close-button"
                onClick={() => setShowPreferences(false)}
              >
                <X size={18} />
              </button>
            </div>

            <small className="muted-note">
              Everything here is optional. Your target roles are kept in your
              profile. Preferences are saved only for now and do not change
              match scores yet.
            </small>

            <Field label="Preferred locations" hint="separate with commas">
              <input
                value={preferencesForm.locations}
                placeholder="e.g. Bengaluru, Pune, Remote"
                onChange={(e) =>
                  setPreferencesForm({
                    ...preferencesForm,
                    locations: e.target.value,
                  })
                }
              />
            </Field>

            <Field label="Experience level">
              <select
                value={preferencesForm.experience_level}
                onChange={(e) =>
                  setPreferencesForm({
                    ...preferencesForm,
                    experience_level: e.target.value,
                  })
                }
              >
                {Object.entries(EXPERIENCE_LEVELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>

            <div className="check-group-label">Work arrangement</div>
            <div className="check-group">
              {Object.entries(WORK_ARRANGEMENTS).map(([value, label]) => (
                <label key={value} className="check-item">
                  <input
                    type="checkbox"
                    checked={preferencesForm.work_arrangements.includes(value)}
                    onChange={() => toggleArrangement(value)}
                  />
                  {label}
                </label>
              ))}
            </div>

            <h3 className="section-title">Salary expectation</h3>
            <div className="grid-2">
              <Field label="Minimum" error={preferencesErrors.salary_min}>
                <input
                  type="number"
                  min={0}
                  value={preferencesForm.salary_min}
                  onChange={(e) =>
                    setPreferencesForm({
                      ...preferencesForm,
                      salary_min: e.target.value,
                    })
                  }
                />
              </Field>
              <Field label="Maximum" error={preferencesErrors.salary_max}>
                <input
                  type="number"
                  min={0}
                  value={preferencesForm.salary_max}
                  onChange={(e) =>
                    setPreferencesForm({
                      ...preferencesForm,
                      salary_max: e.target.value,
                    })
                  }
                />
              </Field>
              <Field
                label="Currency"
                hint="e.g. INR"
                error={preferencesErrors.salary_currency}
              >
                <input
                  maxLength={3}
                  value={preferencesForm.salary_currency}
                  onChange={(e) =>
                    setPreferencesForm({
                      ...preferencesForm,
                      salary_currency: e.target.value.toUpperCase(),
                    })
                  }
                />
              </Field>
              <Field label="Per">
                <select
                  value={preferencesForm.salary_period}
                  onChange={(e) =>
                    setPreferencesForm({
                      ...preferencesForm,
                      salary_period: e.target.value,
                    })
                  }
                >
                  <option value="year">Year</option>
                  <option value="month">Month</option>
                </select>
              </Field>
            </div>

            {preferencesError && (
              <div className="form-error" role="alert">
                {preferencesError}
              </div>
            )}

            <div className="modal-actions">
              <button
                type="button"
                className="secondary-button"
                onClick={() => setShowPreferences(false)}
              >
                Cancel
              </button>
              <button className="primary-button" disabled={preferencesBusy}>
                {preferencesBusy ? "Saving..." : "Save preferences"}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

export default App;