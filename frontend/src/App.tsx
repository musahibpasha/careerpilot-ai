import { useEffect, useMemo, useRef, useState } from "react";
import ApplicationAssistant, { type AssistantMaterials } from "./ApplicationAssistant";
import { apiFetch as fetch } from "./api";
import {
  Activity,
  ArrowUpRight,
  Bot,
  BriefcaseBusiness,
  CheckCircle2,
  Circle,
  CircleAlert,
  EyeOff,
  FileText,
  Kanban,
  LayoutDashboard,
  Lightbulb,
  List,
  Lock,
  MapPin,
  Menu,
  Plus,
  RotateCcw,
  Search,
  Send,
  Settings,
  ShieldCheck,
  Sparkles,
  Target,
  Upload,
  User,
  X,
} from "lucide-react";

const API = import.meta.env.VITE_API_URL || (import.meta.env.DEV ? "http://127.0.0.1:8000" : "");

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

type Page = "dashboard" | "jobs" | "applications" | "copilot";

const PAGE_HASH: Record<Page, string> = {
  dashboard: "#/",
  jobs: "#/jobs",
  applications: "#/applications",
  copilot: "#/copilot",
};

// Simple page switching with the URL hash, so refresh and the browser Back
// button keep working without adding a router library.
function pageFromHash(): Page {
  const h = window.location.hash;
  if (h.startsWith("#/jobs")) return "jobs";
  if (h.startsWith("#/applications")) return "applications";
  if (h.startsWith("#/copilot")) return "copilot";
  return "dashboard";
}

// One message in the Career Copilot thread. "derived" marks answers computed
// from the app's own data (no AI model involved).
type ChatMessage = {
  id: number;
  role: "user" | "assistant";
  text: string;
  lines?: string[];
  derived?: boolean;
  cta?: { label: string; run: () => void };
};

type Job = {
  id: string;
  title: string;
  company: string;
  location: string | null;
  source: string;
  source_job_id?: string | null;
  url: string | null;
  description: string;
  skills: string[];
  nice_to_have_skills?: string[];
  employment_types?: string[];
  published_at?: string | null;
  work_arrangement?: string;
  experience_level?: string;
  match_score: number;
  match_reasons: string[];
  match_confidence?: "" | "low" | "medium" | "high";
  match_breakdown?: MatchFactor[];
  feedback?: "" | "not_interested";
  feedback_reason?: string;
};

type ApplicationPlan = {
  job_id: string;
  title: string;
  company: string;
  score: number;
  confidence: "low" | "medium" | "high";
  fit_summary: string;
  strengths: string[];
  gaps: string[];
  positioning: string[];
  resume_focus: string[];
  cover_letter_angle: string;
  interview_questions: string[];
};

type ResumeBulletSuggestion = {
  section: string;
  source_text: string;
  tailored_text: string;
};

type ApplicationMaterials = {
  job_id: string;
  title: string;
  company: string;
  cover_letter: string;
  resume_summary: string;
  skills_to_prioritize: string[];
  bullet_suggestions: ResumeBulletSuggestion[];
  caveats: string[];
};

type ProfileDocument = {
  full_name: string;
  email: string;
  phone: string;
  location: string;
  github: string;
  linkedin: string;
  portfolio: string;
  skills: string[];
  experience_summary: string;
  work_experience: WorkForm[];
  projects: ProjectForm[];
  education: EduForm[];
  certifications: CertForm[];
  achievements: string[];
};

type DiscoveredJob = {
  source: string;
  source_job_id: string;
  title: string;
  company: string;
  location: string | null;
  url: string;
  application_url?: string | null;
  description: string;
  skills: string[];
  nice_to_have_skills: string[];
  employment_types: string[];
  published_at: string | null;
  work_arrangement: string;
  experience_level: string;
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string;
  salary_period: "year" | "month";
};

type JobSearchSource =
  | "linkedin"
  | "indeed"
  | "glassdoor"
  | "zip_recruiter"
  | "bayt"
  | "naukri"
  | "bdjobs"
  | "hellowork"
  | "jobicy";

const JOB_SEARCH_SOURCES: {
  id: JobSearchSource;
  label: string;
  mark: string;
  description: string;
}[] = [
  { id: "linkedin", label: "LinkedIn", mark: "in", description: "Professional network" },
  { id: "indeed", label: "Indeed", mark: "I", description: "Global job listings" },
  { id: "glassdoor", label: "Glassdoor", mark: "G", description: "Jobs and company reviews" },
  { id: "zip_recruiter", label: "ZipRecruiter", mark: "Z", description: "Personalized job search" },
  { id: "bayt", label: "Bayt", mark: "B", description: "Middle East opportunities" },
  { id: "naukri", label: "Naukri", mark: "N", description: "Jobs across India" },
  { id: "bdjobs", label: "BDJobs", mark: "BD", description: "Jobs across Bangladesh" },
  { id: "hellowork", label: "HelloWork", mark: "H", description: "Jobs across France" },
  { id: "jobicy", label: "Jobicy", mark: "J", description: "Remote-first listings" },
];

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

// "Match analysis" dialog: the score breakdown for one job. Everything shown
// comes from the job's saved match_breakdown; suggestions are derived from the
// gaps in that breakdown and are not AI generated.
function MatchAnalysis({
  job,
  busy,
  onClose,
  onTrack,
  plan,
  planLoading,
  onLoadPlan,
  materials,
  materialsLoading,
  onGenerateMaterials,
  onUpdateMaterials,
  onCopyText,
  onDownloadCoverLetter,
  onDownloadResume,
}: {
  job: Job;
  busy: boolean;
  onClose: () => void;
  onTrack: () => void;
  plan?: ApplicationPlan | null;
  planLoading?: boolean;
  onLoadPlan?: () => void;
  materials?: ApplicationMaterials | null;
  materialsLoading?: boolean;
  onGenerateMaterials?: () => void;
  onUpdateMaterials?: (updates: Partial<ApplicationMaterials>) => void;
  onCopyText?: (text: string, label: string) => void;
  onDownloadCoverLetter?: () => void;
  onDownloadResume?: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const factors = job.match_breakdown ?? [];
  const judged = factors.filter((f) => f.status !== "unknown");
  const unknown = factors.filter((f) => f.status === "unknown");
  const skills = factors.find((f) => f.key === "skills");
  const matched = skills?.details?.matched ?? [];
  const missingMust = skills?.details?.missing_must_have ?? [];
  const missingNice = skills?.details?.missing_nice_to_have ?? [];

  const score = Math.round(job.match_score);
  const cls = score >= 70 ? "high" : score >= 40 ? "mid" : "low";
  const verdict = score >= 70 ? "Strong fit" : score >= 40 ? "Partial fit" : "Weak fit";

  const suggestions: { title: string; text: string }[] = [
    ...missingMust.slice(0, 2).map((s) => ({
      title: `Close the ${s} gap`,
      text: `If you have experience with ${s}, add it to your profile so it counts. If not, a small project or course would show it.`,
    })),
    ...missingNice.slice(0, 1).map((s) => ({
      title: `Nice to have: ${s}`,
      text: `Not required, but adding ${s} would strengthen this match.`,
    })),
    ...factors
      .filter((f) => f.key !== "skills" && (f.status === "poor" || f.status === "partial"))
      .slice(0, 2)
      .map((f) => ({ title: f.label, text: f.reason })),
  ].slice(0, 4);

  return (
    <div
      className="drawer-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="analysis" role="dialog" aria-modal="true" aria-label={`Match analysis for ${job.title}`}>
        <div className="analysis-top">
          <div className="company-logo">{job.company.slice(0, 1).toUpperCase()}</div>
          <div>
            <h2>{job.title}</h2>
            <p>
              {job.company}
              {job.location ? ` · ${job.location}` : ""}
            </p>
          </div>
          <button type="button" className="close-button" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>

        <div className="analysis-summary">
          <div
            className={`ring lg ${cls}`}
            style={{ "--p": score } as React.CSSProperties}
            role="img"
            aria-label={`${score}% match`}
          >
            <span>{score}%</span>
          </div>
          <div>
            <strong>{verdict}</strong>
            <p>
              Calculated from your profile and preferences.
              {job.match_confidence ? ` ${CONFIDENCE_HELP[job.match_confidence]}` : ""}
            </p>
            {job.match_confidence && (
              <span className={`confidence ${job.match_confidence}`} style={{ marginTop: 8, display: "inline-block" }}>
                {job.match_confidence} confidence
              </span>
            )}
          </div>
        </div>

        {judged.length > 0 ? (
          <>
            <h3>Why it matches</h3>
            {judged.map((f) => (
              <div className="factor" key={f.key}>
                <div className="factor-head">
                  <span>{f.label}</span>
                  <span className="factor-score">
                    {Math.round(f.score ?? 0)}%<small> · weight {Math.round(f.weight)}</small>
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
          </>
        ) : (
          <>
            <h3>Why it matches</h3>
            <MatchReasons reasons={job.match_reasons} />
            <p className="analysis-note">
              This job was scored before score explanations existed. Use Re-score all jobs on Find
              jobs to see the full breakdown.
            </p>
          </>
        )}

        {(matched.length > 0 || missingMust.length > 0 || missingNice.length > 0) && (
          <>
            <h3>Skills</h3>
            <div className="skill-list">
              {matched.map((s) => (
                <span key={`m-${s}`} className="matched">
                  {s}
                </span>
              ))}
              {missingMust.map((s) => (
                <span key={`x-${s}`} className="missing">
                  Missing: {s}
                </span>
              ))}
              {missingNice.map((s) => (
                <span key={`n-${s}`}>Nice to have: {s}</span>
              ))}
            </div>
          </>
        )}

        {suggestions.length > 0 && (
          <>
            <h3>
              Suggested improvements{" "}
              <span className="tag-derived">
                <CheckCircle2 size={12} /> Derived from this score
              </span>
            </h3>
            <div className="suggest-grid">
              {suggestions.map((s) => (
                <div className="suggest-card" key={s.title}>
                  <strong>
                    <Lightbulb size={15} /> {s.title}
                  </strong>
                  <p>{s.text}</p>
                </div>
              ))}
            </div>
          </>
        )}

        {unknown.length > 0 && (
          <p className="analysis-note">
            Not counted: {unknown.map((f) => f.label).join(", ")} (not enough information to judge).
          </p>
        )}

        {plan && (
          <>
            <h3>Application prep</h3>
            <div className="analysis-note" style={{ marginTop: 0 }}>
              <strong>{plan.score.toFixed(1)}% fit</strong> · {plan.confidence} confidence
            </div>
            <p>{plan.fit_summary}</p>
            {plan.strengths.length > 0 && (
              <div className="skill-list">
                {plan.strengths.map((s) => (
                  <span key={`plan-strength-${s}`} className="matched">
                    {s}
                  </span>
                ))}
              </div>
            )}
            {plan.gaps.length > 0 && (
              <>
                <h4>Key gaps</h4>
                <div className="skill-list">
                  {plan.gaps.map((s) => (
                    <span key={`plan-gap-${s}`} className="missing">
                      Missing: {s}
                    </span>
                  ))}
                </div>
              </>
            )}
            <div className="suggest-grid">
              <div className="suggest-card">
                <strong>Positioning</strong>
                <ul>
                  {plan.positioning.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
              <div className="suggest-card">
                <strong>Resume focus</strong>
                <ul>
                  {plan.resume_focus.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            </div>
            <div className="suggest-card" style={{ marginTop: 12 }}>
              <strong>Suggested cover-letter angle</strong>
              <p>{plan.cover_letter_angle}</p>
            </div>
            {plan.interview_questions.length > 0 && (
              <div className="suggest-card" style={{ marginTop: 12 }}>
                <strong>Interview prep</strong>
                <ul>
                  {plan.interview_questions.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}

        <h3>Cover letter and tailored resume drafts</h3>
        <p className="analysis-note" style={{ marginTop: 0 }}>
          Drafts use your saved profile and this job posting. The configured AI provider receives
          those details. Review and edit everything; your PDF is not changed and nothing is sent
          to an employer.
        </p>
        {materials ? (
          <>
            <section className="draft-field">
              <div className="draft-field-head">
                <label htmlFor={`cover-letter-${job.id}`}>Cover letter</label>
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => onCopyText?.(materials.cover_letter, "Cover letter")}
                >
                  Copy
                </button>
              </div>
              <textarea
                id={`cover-letter-${job.id}`}
                rows={12}
                value={materials.cover_letter}
                onChange={(event) =>
                  onUpdateMaterials?.({ cover_letter: event.target.value })
                }
              />
            </section>
            <section className="draft-field">
              <div className="draft-field-head">
                <label htmlFor={`resume-summary-${job.id}`}>Tailored resume summary</label>
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => onCopyText?.(materials.resume_summary, "Resume summary")}
                >
                  Copy
                </button>
              </div>
              <textarea
                id={`resume-summary-${job.id}`}
                rows={4}
                value={materials.resume_summary}
                onChange={(event) =>
                  onUpdateMaterials?.({ resume_summary: event.target.value })
                }
              />
            </section>
            {materials.skills_to_prioritize.length > 0 && (
              <>
                <h4>Relevant profile skills to emphasize</h4>
                <div className="skill-list">
                  {materials.skills_to_prioritize.map((skill) => (
                    <span key={skill} className="matched">{skill}</span>
                  ))}
                </div>
              </>
            )}
            {materials.bullet_suggestions.map((suggestion, index) => {
              const fieldId = `resume-bullet-${job.id}-${index}`;
              return (
                <section className="draft-field" key={`${suggestion.section}-${index}`}>
                  <div className="draft-field-head">
                    <label htmlFor={fieldId}>Resume bullet · {suggestion.section}</label>
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() =>
                        onCopyText?.(suggestion.tailored_text, "Resume bullet")
                      }
                    >
                      Copy
                    </button>
                  </div>
                  <small>Based on: {suggestion.source_text}</small>
                  <textarea
                    id={fieldId}
                    rows={3}
                    value={suggestion.tailored_text}
                    onChange={(event) => {
                      const bullet_suggestions = materials.bullet_suggestions.map(
                        (item, itemIndex) =>
                          itemIndex === index
                            ? { ...item, tailored_text: event.target.value }
                            : item,
                      );
                      onUpdateMaterials?.({ bullet_suggestions });
                    }}
                  />
                </section>
              );
            })}
            {materials.caveats.length > 0 && (
              <div className="analysis-note">
                <strong>Review before using</strong>
                <ul>
                  {materials.caveats.map((caveat) => (
                    <li key={caveat}>{caveat}</li>
                  ))}
                </ul>
              </div>
            )}
            <div className="draft-export-actions">
              <button
                type="button"
                className="primary-button"
                onClick={() => onDownloadCoverLetter?.()}
              >
                <FileText size={15} /> Download cover letter PDF
              </button>
              <button
                type="button"
                className="secondary-button"
                onClick={() => void onCopyText?.(materials.cover_letter, "Cover letter")}
              >
                Copy cover letter
              </button>
              <button
                type="button"
                className="secondary-button"
                onClick={() => onDownloadResume?.()}
              >
                <FileText size={15} /> Export tailored resume
              </button>
              <small>
                The cover letter downloads as PDF. The tailored resume export is printable HTML;
                open it in a browser and choose Print → Save as PDF.
              </small>
            </div>
          </>
        ) : (
          <p className="analysis-note">
            Generate a job-specific cover letter and resume wording from your confirmed profile.
          </p>
        )}

        <div className="analysis-actions">
          <button
            type="button"
            className="secondary-button"
            onClick={() => onLoadPlan?.()}
            disabled={planLoading || busy}
          >
            {planLoading ? "Loading plan..." : plan ? "Refresh plan" : "Build application plan"}
          </button>
          <button
            type="button"
            className="secondary-button"
            onClick={() => onGenerateMaterials?.()}
            disabled={materialsLoading || busy}
          >
            {materialsLoading
              ? "Generating AI drafts..."
              : materials
              ? "Regenerate cover letter and resume drafts"
              : "Generate cover letter and resume drafts"}
          </button>
          <button className="primary-button" onClick={onTrack} disabled={busy}>
            Track job
          </button>
          {job.url && (
            <a className="secondary-button" href={job.url} target="_blank" rel="noreferrer">
              Open posting <ArrowUpRight size={14} />
            </a>
          )}
        </div>
        <p className="analysis-note">
          Tracking a job does not apply to it. Submitting needs your explicit approval.
        </p>
      </div>
    </div>
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
  updated_at: string;
  job_title: string;
  company: string;
  notes: string;
};

type Profile = {
  id?: string;
  full_name: string;
  location?: string;
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
  const [dashboardLoadState, setDashboardLoadState] = useState<
    "loading" | "ready" | "error"
  >("loading");
  const [dashboardLoadError, setDashboardLoadError] = useState("");
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
  const [jobSource, setJobSource] = useState<JobSearchSource>("linkedin");
  const [linkedinKeywords, setLinkedinKeywords] = useState("software engineer");
  const [linkedinLocation, setLinkedinLocation] = useState("Bengaluru, India");
  const [linkedinWorkArrangement, setLinkedinWorkArrangement] = useState("any");
  const [linkedinEmploymentType, setLinkedinEmploymentType] = useState("Full-time");
  const [linkedinExperience, setLinkedinExperience] = useState("any");
  const [linkedinSalaryMin, setLinkedinSalaryMin] = useState("");
  const [linkedinSalaryMax, setLinkedinSalaryMax] = useState("");
  const [linkedinSalaryCurrency, setLinkedinSalaryCurrency] = useState("INR");
  const [linkedinPostedWithin, setLinkedinPostedWithin] = useState("any");
  const [linkedinSort, setLinkedinSort] = useState("relevance");
  const [discoveryWhat, setDiscoveryWhat] = useState("software engineer");
  const [discoveryWhere, setDiscoveryWhere] = useState("Bengaluru, India");
  const [discoveryGeo, setDiscoveryGeo] = useState("apac");
  const [discoveryResults, setDiscoveryResults] = useState<DiscoveredJob[]>([]);
  const [discoveryPage, setDiscoveryPage] = useState(1);
  const [discoveryNextCursor, setDiscoveryNextCursor] = useState<string | null>(null);
  const [discoveryHasMore, setDiscoveryHasMore] = useState(false);
  const [discoveryBusy, setDiscoveryBusy] = useState(false);
  const [discoveryMessage, setDiscoveryMessage] = useState("");
  const [discoveryError, setDiscoveryError] = useState(false);
  const discoveryRequestId = useRef(0);
  const [savingDiscoveryId, setSavingDiscoveryId] = useState<string | null>(null);
  const [pasteText, setPasteText] = useState("");
  const [pasteOpen, setPasteOpen] = useState(true);
  const [importing, setImporting] = useState(false);
  const [importMessage, setImportMessage] = useState("");
  const [showProfile, setShowProfile] = useState(false);
  const [busy, setBusy] = useState(false);
  const [matchingAction, setMatchingAction] = useState<
    "job" | "profile" | "preferences" | "rescore" | null
  >(null);
  const [updatingApplicationIds, setUpdatingApplicationIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [transitioningApplicationId, setTransitioningApplicationId] = useState<
    string | null
  >(null);
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

  const [analysisJob, setAnalysisJob] = useState<Job | null>(null);
  const [assistantJobId, setAssistantJobId] = useState<string | null>(null);
  const [analysisPlan, setAnalysisPlan] = useState<Record<string, ApplicationPlan>>({});
  const [analysisPlanLoading, setAnalysisPlanLoading] = useState(false);
  const [analysisMaterials, setAnalysisMaterials] = useState<
    Record<string, ApplicationMaterials>
  >({});
  const [materialsLoadingId, setMaterialsLoadingId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [chat, setChat] = useState<ChatMessage[]>([
    {
      id: 0,
      role: "assistant",
      text: "Hi! I can summarise your matches, skill gaps and approval queue from the data in this app. Pick a quick action below.",
    },
  ]);
  const threadRef = useRef<HTMLDivElement>(null);
  const previousPageRef = useRef(page);

  useEffect(() => {
    if (previousPageRef.current === page) return;
    previousPageRef.current = page;
    window.requestAnimationFrame(() => {
      document.querySelector<HTMLElement>(".main h1")?.focus({ preventScroll: true });
    });
  }, [page]);

  useEffect(() => {
    if (!sidebarOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSidebarOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [sidebarOpen]);

  useEffect(() => {
    threadRef.current?.scrollTo({
      top: threadRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [chat.length]);

  async function load(showDashboardLoading = false) {
    if (showDashboardLoading) {
      setDashboardLoadState("loading");
      setDashboardLoadError("");
      setNotice("");
    }

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
      const coreDashboardUnavailable =
        jobsResult.status === "rejected" ||
        applicationsResult.status === "rejected";
      if (showDashboardLoading && coreDashboardUnavailable) {
        setDashboardLoadError(
          `Some dashboard information could not be loaded (${problems.join("; ")}). ` +
            "Check that FastAPI and MongoDB are running, then try again.",
        );
        setDashboardLoadState("error");
      } else {
        if (showDashboardLoading) setDashboardLoadState("ready");
        setNotice(
          `Some data could not be loaded (${problems.join("; ")}). ` +
            "Check that MongoDB and the FastAPI backend are running (see README).",
        );
      }
    } else if (showDashboardLoading) {
      setDashboardLoadState("ready");
    }
  }

  function clearDiscoveryResults() {
    discoveryRequestId.current += 1;
    setDiscoveryBusy(false);
    setDiscoveryResults([]);
    setDiscoveryPage(1);
    setDiscoveryNextCursor(null);
    setDiscoveryHasMore(false);
    setDiscoveryMessage("");
    setDiscoveryError(false);
  }

  async function searchJobicy(cursor: string | null = null, pageNumber = 1) {
    const requestId = ++discoveryRequestId.current;
    setDiscoveryBusy(true);
    setDiscoveryMessage("");
    setDiscoveryError(false);
    try {
      const params = new URLSearchParams({
        what: discoveryWhat.trim(),
        where: discoveryWhere.trim(),
        geo: discoveryGeo,
      });
      if (cursor) params.set("cursor", cursor);
      const response = await fetch(`${API}/api/jobs/discover?${params}`);
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(errorText(data, `Job search failed (HTTP ${response.status}).`));
      }
      if (
        !Array.isArray(data?.results) ||
        typeof data?.has_more !== "boolean" ||
        typeof data?.geo !== "string"
      ) {
        throw new Error("Jobicy returned an invalid search response.");
      }
      if (requestId !== discoveryRequestId.current) return;
      setDiscoveryResults(
        data.results.map((job: Omit<DiscoveredJob, "source">) => ({
          ...job,
          source: "jobicy",
        })),
      );
      setDiscoveryPage(pageNumber);
      setDiscoveryHasMore(data.has_more);
      setDiscoveryNextCursor(
        typeof data.next_cursor === "string" ? data.next_cursor : null,
      );
      if (data.results.length === 0) {
        setDiscoveryMessage(
          data.has_more
            ? "No listings on this page match that location. Try the next page or broaden the location."
            : "No recent listings matched those search criteria.",
        );
      }
    } catch (e) {
      if (requestId !== discoveryRequestId.current) return;
      setDiscoveryResults([]);
      setDiscoveryHasMore(false);
      setDiscoveryNextCursor(null);
      setDiscoveryError(true);
      setDiscoveryMessage(e instanceof Error ? e.message : "Could not search Jobicy.");
    } finally {
      if (requestId === discoveryRequestId.current) {
        setDiscoveryBusy(false);
      }
    }
  }

  async function searchJobBoard() {
    const requestId = ++discoveryRequestId.current;
    setDiscoveryBusy(true);
    setDiscoveryMessage("");
    setDiscoveryError(false);
    try {
      const params = new URLSearchParams({
        site: jobSource,
        what: linkedinKeywords.trim(),
        where: linkedinLocation.trim(),
      });
      const jobType = {
        "Full-time": "fulltime",
        "Part-time": "parttime",
        Internship: "internship",
        Contract: "contract",
        Temporary: "temporary",
      }[linkedinEmploymentType];
      if (jobType) params.set("job_type", jobType);
      if (linkedinWorkArrangement === "remote") params.set("is_remote", "true");
      const hoursOld = {
        "24-hours": "24",
        week: "168",
        month: "720",
      }[linkedinPostedWithin];
      if (hoursOld) params.set("hours_old", hoursOld);

      const response = await fetch(`${API}/api/jobs/discover/board?${params}`);
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(errorText(data, `Job search failed (HTTP ${response.status}).`));
      }
      if (!Array.isArray(data?.results) || data?.source !== jobSource) {
        throw new Error(`${jobSource} returned an invalid search response.`);
      }
      if (requestId !== discoveryRequestId.current) return;
      setDiscoveryResults(
        data.results.map((job: Omit<DiscoveredJob, "source">) => ({
          ...job,
          source: data.source,
        })),
      );
      setDiscoveryPage(1);
      setDiscoveryHasMore(false);
      setDiscoveryNextCursor(null);
      if (data.results.length === 0) {
        setDiscoveryMessage("No listings matched those search criteria. Try broader keywords or another source.");
      }
    } catch (e) {
      if (requestId !== discoveryRequestId.current) return;
      setDiscoveryResults([]);
      setDiscoveryHasMore(false);
      setDiscoveryNextCursor(null);
      setDiscoveryError(true);
      setDiscoveryMessage(e instanceof Error ? e.message : "Could not search this job board.");
    } finally {
      if (requestId === discoveryRequestId.current) setDiscoveryBusy(false);
    }
  }

  function selectJobSource(source: JobSearchSource) {
    if (source !== jobSource) clearDiscoveryResults();
    setJobSource(source);
  }

  function useProfileForLinkedInSearch() {
    const roles = (profile?.target_roles ?? []).map((role) => role.trim()).filter(Boolean);
    const skills = (profile?.skills ?? []).map((skill) => skill.trim()).filter(Boolean);
    const suggestedKeywords = roles.length
      ? roles.slice(0, 3).join(" OR ")
      : skills.slice(0, 5).join(" ");

    if (suggestedKeywords) setLinkedinKeywords(suggestedKeywords);
    setLinkedinLocation(
      preferencesForm.locations.trim() ||
        profile?.location?.trim() ||
        "Bengaluru, India",
    );

    if (preferencesForm.work_arrangements.length === 1) {
      setLinkedinWorkArrangement(preferencesForm.work_arrangements[0]);
    }

    if (preferencesForm.experience_level === "intern") {
      setLinkedinExperience("internship");
    } else if (preferencesForm.experience_level === "entry") {
      setLinkedinExperience("entry");
    }

    setLinkedinSalaryMin(preferencesForm.salary_min);
    setLinkedinSalaryMax(preferencesForm.salary_max);
    if (preferencesForm.salary_currency) {
      setLinkedinSalaryCurrency(preferencesForm.salary_currency);
    }
  }

  async function saveDiscoveredJob(job: DiscoveredJob) {
    setSavingDiscoveryId(job.source_job_id);
    setDiscoveryMessage("");
    try {
      const response = await fetch(`${API}/api/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: job.title,
          company: job.company,
          location: job.location,
          source: job.source,
          source_job_id: job.source_job_id,
          url: job.url,
          application_url: job.application_url,
          description: job.description,
          skills: job.skills,
          nice_to_have_skills: job.nice_to_have_skills,
          employment_types: job.employment_types,
          published_at: job.published_at,
          work_arrangement: job.work_arrangement,
          experience_level: job.experience_level,
          salary_min: job.salary_min,
          salary_max: job.salary_max,
          salary_currency: job.salary_currency,
          salary_period: job.salary_period,
        }),
      });
      const data = await response.json().catch(() => null);
      if (response.status === 409) {
        setDiscoveryMessage("This Jobicy listing is already saved.");
        return;
      }
      if (!response.ok) {
        throw new Error(errorText(data, `Could not save this listing (HTTP ${response.status}).`));
      }
      setJobs((current) => [data, ...current.filter((saved) => saved.id !== data.id)]);
      setNotice(`Saved ${job.title} and calculated its match score.`);
      setDiscoveryMessage("Listing saved to Find Jobs.");
    } catch (e) {
      setDiscoveryMessage(e instanceof Error ? e.message : "Could not save this listing.");
    } finally {
      setSavingDiscoveryId(null);
    }
  }

  useEffect(() => {
    void load(true);
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
      const titleOrCompanyFound = (data.found as string[]).some(
        (key) => key === "title" || key === "company",
      );
      const baseMessage =
        found.length && titleOrCompanyFound
          ? `Filled in: ${found.join(", ")}.` +
              (missing.length ? ` Not found: ${missing.join(", ")}.` : "") +
              " Please check the details below before saving."
          : found.length
            ? `I read the posting and found ${found.join(", ")}, but couldn't clearly identify the job title or company. Please fill those in by hand, then check the other details before saving.`
            : "I could not recognise any details. Add them by hand below.";
      const warnings = Array.isArray(data.warnings)
        ? data.warnings.filter((warning: unknown): warning is string => typeof warning === "string")
        : [];
      setImportMessage([baseMessage, ...warnings].join(" "));
    } catch (e) {
      setImportMessage(e instanceof Error ? e.message : "Could not read that posting.");
    } finally {
      setImporting(false);
    }
  }

  // Jobs saved before explainable matching have no breakdown yet.
  async function rescoreJobs(): Promise<boolean> {
    setBusy(true);
    setMatchingAction("rescore");
    try {
      const response = await fetch(`${API}/api/jobs/rescore`, { method: "POST" });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(errorText(data, `HTTP ${response.status}`));
      }
      if (typeof data?.rescored !== "number") {
        throw new Error("The API returned an invalid re-score result.");
      }
      setNotice(`Match scores updated for ${data.rescored} job${data.rescored === 1 ? "" : "s"}.`);
      await load();
      return true;
    } catch (e) {
      setNotice(
        `Could not re-score jobs (${e instanceof Error ? e.message : "error"}).`,
      );
      return false;
    } finally {
      setMatchingAction(null);
      setBusy(false);
    }
  }

  async function addJob(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMatchingAction("job");
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

      const matchScore =
        typeof data?.match_score === "number"
          ? ` Match score: ${Math.round(data.match_score)}%.`
          : "";
      setNotice(`Job added and scored.${matchScore}`);
      await load();
    } catch (e) {
      const message = e instanceof Error ? e.message : "Something went wrong.";
      setNotice(message);
      setFormError(message);
    } finally {
      setMatchingAction(null);
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
    setMatchingAction("profile");

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
      setMatchingAction(null);
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

  async function openResumeFile(resume: ResumeFile) {
    const preview = window.open("about:blank", "_blank");
    if (!preview) {
      setNotice("Allow pop-ups to view this resume.");
      return;
    }

    try {
      const response = await fetch(`${API}/api/resumes/${resume.id}/file`);
      if (!response.ok) {
        const detail = await response.text();
        throw new Error(detail || "Could not load this resume.");
      }
      const objectUrl = URL.createObjectURL(await response.blob());
      preview.opener = null;
      preview.location.href = objectUrl;
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
    } catch (reason) {
      preview.close();
      setNotice(reason instanceof Error ? reason.message : "Could not load this resume.");
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
    setMatchingAction("preferences");

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
      setMatchingAction(null);
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

    if (updatingApplicationIds.has(application.id)) return;

    // Do not submit a status that is not allowed by the API response.
    if (!application.allowed_next?.includes(nextStatus)) {
      setNotice("That status transition is not allowed.");
      return;
    }

    setUpdatingApplicationIds((current) => new Set(current).add(application.id));
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
        const message = errorText(data, "Could not update application status.");
        await load();
        setNotice(message);
        return;
      }

      setNotice("Application status updated.");
      setTransitioningApplicationId(application.id);
      await load();
      window.setTimeout(() => {
        setTransitioningApplicationId((current) =>
          current === application.id ? null : current,
        );
      }, 850);
    } catch {
      await load();
      setNotice("Could not reach the API.");
    } finally {
      setUpdatingApplicationIds((current) => {
        const next = new Set(current);
        next.delete(application.id);
        return next;
      });
    }
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
    window.location.hash = PAGE_HASH[next];
    setPage(next);
    setSidebarOpen(false);
    window.scrollTo({
      top: 0,
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "auto"
        : "smooth",
    });
  }

  const firstName = profile?.full_name?.trim().split(/\s+/)[0] ?? "";

  const pageTitle: Record<Page, { eyebrow: string; title: string; sub: string }> = {
    dashboard: {
      eyebrow: "DASHBOARD",
      title: firstName ? `Welcome back, ${firstName}` : "Welcome back",
      sub:
        jobs.length === 0
          ? "Add your first job to start scoring opportunities."
          : `${jobs.length} job${jobs.length === 1 ? "" : "s"} saved · ${reviewCount} awaiting your review`,
    },
    jobs: {
      eyebrow: "FIND JOBS",
      title: "Find jobs",
      sub: "Discover, match, and save opportunities.",
    },
    applications: {
      eyebrow: "YOUR PIPELINE",
      title: "Applications",
      sub: "Drag a card to another column, or use its Move to menu.",
    },
    copilot: {
      eyebrow: "AI COPILOT",
      title: "ThoughtbotLabs Copilot",
      sub: "Quick answers about your matches, gaps and approvals.",
    },
  };

  function openProfile(section?: string) {
    setSidebarOpen(false);
    setShowProfile(true);
    if (section) {
      window.setTimeout(
        () =>
          document
            .getElementById(section)
            ?.scrollIntoView({ behavior: "smooth", block: "start" }),
        60,
      );
    }
  }

  function openPreferences() {
    setSidebarOpen(false);
    setShowPreferences(true);
  }

  const scoreClass = (score: number) =>
    score >= 70 ? "high" : score >= 40 ? "mid" : "low";

  const activeSavedFilterCount = [
    statusFilter !== "all",
    minMatch > 0,
    sourceFilter !== "all",
    sortBy !== "best",
  ].filter(Boolean).length;

  async function loadApplicationPlan(job: Job, force = false) {
    const cached = analysisPlan[job.id];
    if (!force && cached) return cached;

    setAnalysisPlanLoading(true);
    try {
      const response = await fetch(`${API}/api/jobs/${job.id}/application-plan`);
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(errorText(data, "Could not build the application plan."));
      }
      const plan = data as ApplicationPlan;
      setAnalysisPlan((current) => ({ ...current, [job.id]: plan }));
      return plan;
    } catch (e) {
      setNotice(
        e instanceof Error ? e.message : "Could not build the application plan.",
      );
      return null;
    } finally {
      setAnalysisPlanLoading(false);
    }
  }

  useEffect(() => {
    if (!analysisJob) return;
    void loadApplicationPlan(analysisJob);
  }, [analysisJob?.id]);

  async function generateApplicationMaterials(job: Job) {
    setMaterialsLoadingId(job.id);
    setNotice("");
    try {
      const response = await fetch(
        `${API}/api/jobs/${job.id}/application-materials`,
        { method: "POST" },
      );
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(errorText(data, "Could not generate application drafts."));
      }
      if (
        typeof data?.cover_letter !== "string" ||
        typeof data?.resume_summary !== "string" ||
        !Array.isArray(data?.bullet_suggestions)
      ) {
        throw new Error("The API returned an invalid application-draft response.");
      }
      setAnalysisMaterials((current) => ({ ...current, [job.id]: data }));
    } catch (e) {
      setNotice(
        e instanceof Error ? e.message : "Could not generate application drafts.",
      );
    } finally {
      setMaterialsLoadingId(null);
    }
  }

  function updateApplicationMaterials(
    jobId: string,
    updates: Partial<ApplicationMaterials>,
  ) {
    setAnalysisMaterials((current) => {
      const existing = current[jobId];
      return existing
        ? { ...current, [jobId]: { ...existing, ...updates } }
        : current;
    });
  }

  async function copyGeneratedText(text: string, label: string) {
    if (!navigator.clipboard?.writeText) {
      setNotice("Clipboard access is unavailable. Select the draft text and copy it manually.");
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      setNotice(`${label} copied to clipboard.`);
    } catch {
      setNotice("Could not access the clipboard. Select the draft text and copy it manually.");
    }
  }

  async function downloadCoverLetterPdf(
    job: Job,
    materials: ApplicationMaterials,
  ) {
    try {
      const response = await fetch(
        `${API}/api/jobs/${job.id}/cover-letter.pdf`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ cover_letter: materials.cover_letter }),
        },
      );
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new Error(errorText(data, "Could not download the cover-letter PDF."));
      }

      const pdf = await response.blob();
      if (pdf.type !== "application/pdf") {
        throw new Error("The API returned an invalid cover-letter document.");
      }
      const objectUrl = URL.createObjectURL(pdf);
      const link = document.createElement("a");
      const filename = `${job.title} - ${job.company} - cover letter.pdf`
        .replace(/[<>:"/\\|?*\u0000-\u001F]/g, "-")
        .slice(0, 150);
      link.href = objectUrl;
      link.download = filename || "cover-letter.pdf";
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
      setNotice("Cover-letter PDF downloaded.");
    } catch (e) {
      setNotice(
        e instanceof Error ? e.message : "Could not download the cover-letter PDF.",
      );
    }
  }

  async function downloadTailoredResume(
    job: Job,
    materials: ApplicationMaterials,
  ) {
    try {
      const response = await fetch(`${API}/api/profile`);
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(errorText(data, "Could not load your saved profile."));
      }

      const entries = (value: unknown): Record<string, unknown>[] =>
        Array.isArray(value)
          ? value.filter(
              (item): item is Record<string, unknown> =>
                typeof item === "object" && item !== null,
            )
          : [];
      const profile: ProfileDocument = {
        full_name: asText(data?.full_name),
        email: asText(data?.email),
        phone: asText(data?.phone),
        location: asText(data?.location),
        github: asText(data?.github),
        linkedin: asText(data?.linkedin),
        portfolio: asText(data?.portfolio),
        skills: asList(data?.skills),
        experience_summary: asText(data?.experience_summary),
        work_experience: entries(data?.work_experience).map((item) => ({
          title: asText(item.title),
          company: asText(item.company),
          duration: asText(item.duration),
          location: asText(item.location),
          description: asText(item.description),
        })),
        projects: entries(data?.projects).map((item) => ({
          name: asText(item.name),
          role: asText(item.role),
          duration: asText(item.duration),
          technologies: asInline(item.technologies),
          description: asText(item.description),
        })),
        education: entries(data?.education).map((item) => ({
          institution: asText(item.institution),
          degree: asText(item.degree),
          duration: asText(item.duration),
          grade: asText(item.grade),
        })),
        certifications: entries(data?.certifications).map((item) => ({
          name: asText(item.name),
          issuer: asText(item.issuer),
        })),
        achievements: asList(data?.achievements),
      };

      if (!profile.full_name) {
        throw new Error("Add your name to your saved profile before exporting a resume.");
      }

      let staleSuggestions = 0;
      const tailoredDescription = (section: string, original: string) =>
        materials.bullet_suggestions
          .filter((suggestion) => suggestion.section === section)
          .reduce((description, suggestion) => {
            if (!description.includes(suggestion.source_text)) {
              staleSuggestions += 1;
              return description;
            }
            return description.replace(
              suggestion.source_text,
              suggestion.tailored_text,
            );
          }, original);

      const escapeHtml = (value: string) =>
        value.replace(/[&<>"']/g, (char) => {
          const entities: Record<string, string> = {
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#39;",
          };
          return entities[char];
        });
      const lines = (text: string) =>
        text
          .split(/\r?\n/)
          .map((line) => line.trim())
          .filter(Boolean)
          .map((line) => `<li>${escapeHtml(line)}</li>`)
          .join("");
      const section = (title: string, body: string) =>
        body ? `<section><h2>${escapeHtml(title)}</h2>${body}</section>` : "";

      const contacts = [
        profile.email,
        profile.phone,
        profile.location,
        profile.linkedin,
        profile.github,
        profile.portfolio,
      ]
        .filter(Boolean)
        .map(escapeHtml)
        .join(" <span>·</span> ");
      const prioritized = new Set(
        materials.skills_to_prioritize.map((skill) => skill.toLocaleLowerCase()),
      );
      const orderedSkills = [
        ...profile.skills.filter((skill) => prioritized.has(skill.toLocaleLowerCase())),
        ...profile.skills.filter((skill) => !prioritized.has(skill.toLocaleLowerCase())),
      ];

      const experience = profile.work_experience
        .map((item) => {
          const name = `${item.title} at ${item.company}`;
          const description = tailoredDescription(name, item.description);
          return `<article><div class="entry-head"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.duration)}</span></div><div class="subhead">${escapeHtml(item.company)}${item.location ? ` · ${escapeHtml(item.location)}` : ""}</div>${description ? `<ul>${lines(description)}</ul>` : ""}</article>`;
        })
        .join("");
      const projects = profile.projects
        .map((item) => {
          const description = tailoredDescription(
            `${item.name} project`,
            item.description,
          );
          return `<article><div class="entry-head"><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.duration)}</span></div>${item.role ? `<div class="subhead">${escapeHtml(item.role)}</div>` : ""}${item.technologies ? `<p>${escapeHtml(item.technologies)}</p>` : ""}${description ? `<ul>${lines(description)}</ul>` : ""}</article>`;
        })
        .join("");
      const education = profile.education
        .map(
          (item) =>
            `<article><div class="entry-head"><strong>${escapeHtml(item.degree)}</strong><span>${escapeHtml(item.duration)}</span></div><div class="subhead">${escapeHtml(item.institution)}${item.grade ? ` · ${escapeHtml(item.grade)}` : ""}</div></article>`,
        )
        .join("");
      const certifications = profile.certifications
        .map(
          (item) =>
            `<li>${escapeHtml(item.name)}${item.issuer ? ` — ${escapeHtml(item.issuer)}` : ""}</li>`,
        )
        .join("");

      const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(profile.full_name)} - Resume for ${escapeHtml(job.title)}</title>
<style>
*{box-sizing:border-box}body{max-width:850px;margin:32px auto;padding:0 40px;color:#1b2430;font:10.5pt/1.45 Arial,sans-serif}
h1{margin:0;font-size:25pt}header{text-align:center;border-bottom:2px solid #263b55;padding:0 0 14px}
.target{margin:4px 0;font-size:12pt;color:#344a62}.contact{font-size:9pt;color:#48576a}
section{margin-top:17px}h2{font-size:11pt;text-transform:uppercase;letter-spacing:.08em;border-bottom:1px solid #b9c3cf;padding-bottom:4px;margin:0 0 8px}
p{margin:5px 0}article{margin:0 0 10px}.entry-head{display:flex;justify-content:space-between;gap:14px}.entry-head span{white-space:nowrap;color:#526174}.subhead{color:#344a62}
ul{margin:4px 0 0;padding-left:19px}li{margin:2px 0}.skills{line-height:1.6}
.print{position:fixed;top:14px;right:14px;padding:9px 13px;border:0;border-radius:6px;background:#283f5b;color:white;cursor:pointer}
@media print{body{max-width:none;margin:0;padding:0;font-size:10pt}.print{display:none}section{break-inside:avoid}article{break-inside:avoid}}
@media(max-width:600px){body{margin:20px auto;padding:0 18px}.entry-head{display:block}}
</style></head>
<body><button class="print" onclick="window.print()">Print / Save as PDF</button>
<header><h1>${escapeHtml(profile.full_name)}</h1><p class="target">${escapeHtml(job.title)} · ${escapeHtml(job.company)}</p><p class="contact">${contacts}</p></header>
${section("Professional Summary", `<p>${escapeHtml(materials.resume_summary)}</p>`)}
${section("Skills", `<p class="skills">${orderedSkills.map(escapeHtml).join(" · ")}</p>`)}
${section("Work Experience", experience)}
${section("Projects", projects)}
${section("Education", education)}
${section("Certifications", certifications ? `<ul>${certifications}</ul>` : "")}
${section("Achievements", profile.achievements.length ? `<ul>${lines(profile.achievements.join("\n"))}</ul>` : "")}
</body></html>`;

      const blob = new Blob([html], { type: "text/html;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      const filename = `${profile.full_name}-${job.company}-${job.title}`
        .normalize("NFKD")
        .replace(/[^a-zA-Z0-9]+/g, "-")
        .replace(/^-|-$/g, "")
        .slice(0, 100);
      link.href = url;
      link.download = `${filename || "tailored-resume"}.html`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);

      setNotice(
        staleSuggestions
          ? `Tailored resume downloaded. ${staleSuggestions} suggestion(s) were skipped because the saved profile changed; open the HTML file and choose Print / Save as PDF.`
          : "Tailored resume downloaded. Open the HTML file in a browser and choose Print / Save as PDF.",
      );
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Could not export the tailored resume.");
    }
  }

  // ---- Career Copilot: quick answers built from the data already in the app.
  // No AI model is called here; every answer is computed from saved jobs,
  // applications, profile and resumes, and labelled as such.
  function say(message: Omit<ChatMessage, "id">) {
    setChat((prev) => [...prev, { ...message, id: prev.length + 1 }]);
  }

  function runQuickAction(key: string) {
    const activeJobs = jobs.filter((j) => j.feedback !== "not_interested");

    if (key === "top") {
      say({ role: "user", text: "Show my best matches" });
      const top = [...activeJobs]
        .sort((a, b) => b.match_score - a.match_score)
        .slice(0, 3);
      say({
        role: "assistant",
        text: top.length
          ? "These saved jobs currently score highest against your profile:"
          : "You have no saved jobs yet. Add one and I can score it against your profile.",
        lines: top.map(
          (j) => `${j.title} at ${j.company} — ${Math.round(j.match_score)}% match`,
        ),
        derived: true,
        cta: top.length
          ? { label: "Open Find jobs", run: () => goTo("jobs") }
          : { label: "Add a job", run: () => setShowJobForm(true) },
      });
    } else if (key === "gaps") {
      say({ role: "user", text: "What skills am I missing?" });
      const counts = new Map<string, number>();
      for (const j of activeJobs) {
        const skills = j.match_breakdown?.find((f) => f.key === "skills");
        for (const s of skills?.details?.missing_must_have ?? []) {
          counts.set(s, (counts.get(s) ?? 0) + 1);
        }
      }
      const top = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5);
      say({
        role: "assistant",
        text: top.length
          ? "These must-have skills are missing most often across your saved jobs:"
          : jobs.length === 0
            ? "Add a few jobs first, then I can look for patterns."
            : "No missing must-have skills were found. If scores look out of date, re-score your jobs.",
        lines: top.map(
          ([skill, n]) => `${skill} — missing in ${n} job${n === 1 ? "" : "s"}`,
        ),
        derived: true,
      });
    } else if (key === "approval") {
      say({ role: "user", text: "What needs my approval?" });
      const waiting = applications.filter((a) =>
        ["under_review", "ready_for_approval"].includes(a.status),
      );
      say({
        role: "assistant",
        text: waiting.length
          ? "These applications are waiting for your review. Nothing is submitted on your behalf; use Automate to prepare and review one."
          : "Nothing is waiting for review. ThoughtLabs Pilot never submits an application for you.",
        lines: waiting.map(
          (a) => `${a.job_title} at ${a.company} — ${statusLabel(a.status)}`,
        ),
        derived: true,
        cta: { label: "Open Applications", run: () => goTo("applications") },
      });
    } else if (key === "profile") {
      say({ role: "user", text: "How complete is my profile?" });
      const todo: string[] = [];
      if (!profile?.skills?.length) todo.push("Add your skills so matching has something to compare");
      if (!profile?.target_roles?.length) todo.push("Add target roles to improve role-fit scoring");
      if (!resumes.some((r) => r.is_active)) todo.push("Upload a resume and mark one as active");
      say({
        role: "assistant",
        text: todo.length
          ? "A few things would make your scores more reliable:"
          : "Your profile has skills, target roles and an active resume. Keep your job preferences up to date for the best scores.",
        lines: todo,
        derived: true,
        cta: { label: "Open profile", run: () => openProfile() },
      });
    } else if (key === "rescore") {
      say({ role: "user", text: "Re-score my jobs" });
      void rescoreJobs().then((completed) =>
        say({
          role: "assistant",
          text: completed
            ? "Scores were recalculated from your current profile and preferences."
            : "I could not confirm that the scores were recalculated. Check the notice above and try again.",
          derived: true,
        }),
      );
    } else if (key === "add") {
      say({ role: "user", text: "Add a job from a posting" });
      say({
        role: "assistant",
        text: "Paste the posting into Add job and I will fill the form for you to review. Nothing is saved until you confirm.",
        cta: { label: "Add a job", run: () => setShowJobForm(true) },
      });
    }
  }

  // One job card, used by Find jobs (full) and the dashboard (compact).
  function renderJobCard(job: Job, compact = false) {
    const score = Math.round(job.match_score);
    const cls = scoreClass(job.match_score);
    const skillsFactor = job.match_breakdown?.find((f) => f.key === "skills");
    const missing = skillsFactor?.details?.missing_must_have ?? [];

    const ring = (
      <div
        className={`ring ${cls}`}
        style={{ "--p": score } as React.CSSProperties}
        role="img"
        aria-label={`${score}% match`}
      >
        <span>{score}%</span>
      </div>
    );

    if (compact) {
      return (
        <article className="job-card compact-job" key={job.id}>
          <div className="company-logo">{job.company.slice(0, 1).toUpperCase()}</div>
          <div className="job-info">
            <div className="job-title-row">
              <h3>{job.title}</h3>
            </div>
            <p>
              {job.company}
              {job.location ? ` · ${job.location}` : ""}
            </p>
            {missing.length > 0 && (
              <small className="reason gap">Missing: {missing.slice(0, 3).join(", ")}</small>
            )}
          </div>
          <button
            type="button"
            className="ghost-button"
            onClick={() => setAnalysisJob(job)}
            aria-label={`Match analysis for ${job.title}`}
            style={{ padding: 0, borderRadius: "50%" }}
          >
            {ring}
          </button>
          <button
            type="button"
            className="secondary-button"
            onClick={() => setAssistantJobId(job.id)}
            aria-label={`Automate application for ${job.title}`}
          >
            <Bot size={14} /> Automate
          </button>
        </article>
      );
    }

    return (
      <article className="job-card" key={job.id}>
        <div className="company-logo">{job.company.slice(0, 1).toUpperCase()}</div>

        <div className="job-info">
          <div className="job-title-row">
            <h3>{job.title}</h3>
          </div>

          <p>{job.company}</p>

          <div className="job-meta">
            {job.location && (
              <span>
                <MapPin size={13} /> {job.location}
              </span>
            )}
            {job.work_arrangement && <span>{job.work_arrangement}</span>}
            {job.experience_level && <span>{job.experience_level}</span>}
            {(job.employment_types ?? []).map((type) => (
              <span key={type}>{type}</span>
            ))}
            {job.published_at && !Number.isNaN(new Date(job.published_at).getTime()) && (
              <span>Posted {new Date(job.published_at).toLocaleDateString()}</span>
            )}
            <span>Source: {job.source}</span>
          </div>

          <div className="skill-list">
            {job.skills.slice(0, 6).map((skill) => (
              <span key={skill} className={missing.includes(skill) ? "missing" : ""}>
                {skill}
              </span>
            ))}
          </div>

          {!job.match_breakdown?.length && <MatchReasons reasons={job.match_reasons} />}

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
          <div className="job-score">{ring}</div>
          <button
            type="button"
            className="secondary-button analyze-button"
            onClick={() => setAnalysisJob(job)}
          >
            <Sparkles size={14} /> Match analysis
          </button>
          <button
            type="button"
            className="secondary-button"
            onClick={() => setAssistantJobId(job.id)}
          >
            <Bot size={14} /> Automate
          </button>
          <button
            className="primary-button"
            onClick={() => void trackJob(job)}
            disabled={busy}
          >
            Track job
          </button>
          <div className="row">
            <button
              type="button"
              className="ghost-button"
              aria-expanded={dismissingId === job.id}
              onClick={() => setDismissingId(dismissingId === job.id ? null : job.id)}
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
          </div>
        </div>
      </article>
    );
  }

  const pipelineCounts = KANBAN_STATUSES.map(
    (s) => [s, applications.filter((a) => a.status === s).length] as const,
  ).filter(([, n]) => n > 0);

  const recentApplications = [...applications]
    .sort((a, b) => {
      const timestamp = (application: Application) => {
        const value = Date.parse(application.updated_at);
        return Number.isFinite(value) ? value : 0;
      };
      return timestamp(b) - timestamp(a);
    })
    .slice(0, 4);

  const recommended = [...jobs]
    .filter((j) => j.feedback !== "not_interested")
    .sort((a, b) => b.match_score - a.match_score)
    .slice(0, 3);

  const setup = [
    { label: "Add skills to your profile", done: !!profile?.skills?.length },
    { label: "Upload a resume and set one active", done: resumes.some((r) => r.is_active) },
    { label: "Save at least one job", done: jobs.length > 0 },
    { label: "Track an application", done: applications.length > 0 },
  ];

  const navItems: {
    key: string;
    label: string;
    icon: React.ReactNode;
    page?: Page;
    onClick?: () => void;
    count?: number;
  }[] = [
    { key: "dashboard", label: "Dashboard", icon: <LayoutDashboard size={18} />, page: "dashboard" },
    { key: "jobs", label: "Find Jobs", icon: <Search size={18} />, page: "jobs" },
    { key: "resume", label: "Resume", icon: <FileText size={18} />, onClick: () => openProfile("my-resumes") },
    { key: "applications", label: "Applications", icon: <Kanban size={18} />, page: "applications", count: applications.length },
    { key: "copilot", label: "AI Copilot", icon: <Bot size={18} />, page: "copilot" },
    { key: "profile", label: "Profile", icon: <User size={18} />, onClick: () => openProfile() },
    { key: "settings", label: "Settings", icon: <Settings size={18} />, onClick: openPreferences },
  ];

  const t = pageTitle[page];

  return (
    <div className="app-shell">
      {sidebarOpen && (
        <button
          type="button"
          className="scrim"
          aria-label="Close navigation menu"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <aside
        className={`sidebar ${sidebarOpen ? "open" : ""}`}
        aria-label="Main navigation"
      >
        <div className="brand">
          <div className="brand-mark">
            <Sparkles size={20} />
          </div>
          <div>
            <strong>ThoughtLabs</strong>
            <span>AI job-search copilot</span>
          </div>
        </div>

        <div className="workspace-label">Workspace</div>

        <nav id="main-navigation">
          {navItems.map((item) =>
            item.page ? (
              <a
                key={item.key}
                className={`nav ${page === item.page ? "active" : ""}`}
                href={PAGE_HASH[item.page]}
                aria-current={page === item.page ? "page" : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  goTo(item.page as Page);
                }}
              >
                {item.icon} {item.label}
                {item.count !== undefined && <span className="nav-count">{item.count}</span>}
              </a>
            ) : (
              <button key={item.key} type="button" className="nav" onClick={item.onClick}>
                {item.icon} {item.label}
              </button>
            ),
          )}
        </nav>

        <div className="sidebar-bottom">
          <div className="secure-icon">
            <ShieldCheck size={18} />
          </div>
          <strong>You approve every application</strong>
          <p>AI prepares. You decide. Nothing is submitted without your approval.</p>
          <span className="status-pill">
            <i /> Approval gate enabled
          </span>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div className="topbar-lead">
            <button
              type="button"
              className="mobile-nav-toggle"
              onClick={() => setSidebarOpen(true)}
              aria-expanded={sidebarOpen}
              aria-controls="main-navigation"
              aria-label="Open menu"
            >
              <Menu size={20} />
            </button>
            <div>
              <div className="eyebrow">{t.eyebrow}</div>
              <h1 tabIndex={-1}>{t.title}</h1>
              <p>{t.sub}</p>
            </div>
          </div>

          <div className="topbar-right">
            {(page === "dashboard" || page === "jobs") && (
              <button className="primary-button" onClick={() => setShowJobForm(true)}>
                <Plus size={16} /> Add job
              </button>
            )}
            <button className="profile-button" onClick={() => openProfile()}>
              <span className="avatar">
                {profile?.full_name ? profile.full_name.slice(0, 1).toUpperCase() : "Y"}
              </span>
              <span>{profile?.full_name || "Your profile"}</span>
            </button>
          </div>
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

        {matchingAction && (
          <div className="matching-progress" role="status" aria-live="polite">
            <span className="matching-orbit" aria-hidden="true">
              <Sparkles size={15} />
            </span>
            <span>
              <strong>
                {matchingAction === "job"
                  ? "Scoring your new opportunity"
                  : matchingAction === "profile"
                    ? "Refreshing matches from your profile"
                    : matchingAction === "preferences"
                      ? "Refreshing matches from your preferences"
                      : "Re-scoring saved opportunities"}
              </strong>
              <small>
                The matching service is comparing saved job details with your current profile and preferences.
              </small>
            </span>
            <span className="matching-progress-track" aria-hidden="true">
              <span />
            </span>
          </div>
        )}

        {page === "dashboard" && (
          <div className="dashboard-page page-view">
            {dashboardLoadState === "loading" ? (
              <section className="dashboard-loading" role="status" aria-live="polite">
                <p>Loading your dashboard...</p>
                <div className="dashboard-skeleton" aria-hidden="true">
                  <div className="stats-grid">
                    {[0, 1, 2, 3].map((item) => (
                      <div className="stat-card skeleton-stat" key={item}>
                        <span className="skeleton-line skeleton-label" />
                        <span className="skeleton-line skeleton-value" />
                        <span className="skeleton-line skeleton-caption" />
                      </div>
                    ))}
                  </div>
                  <div className="dash-grid">
                    <section className="panel skeleton-panel">
                      <span className="skeleton-line skeleton-heading" />
                      {[0, 1, 2].map((item) => (
                        <div className="skeleton-job" key={item}>
                          <span className="skeleton-block" />
                          <span className="skeleton-line" />
                        </div>
                      ))}
                    </section>
                    <aside className="dash-side">
                      {[0, 1, 2].map((item) => (
                        <section className="panel skeleton-panel" key={item}>
                          <span className="skeleton-line skeleton-heading" />
                          <span className="skeleton-line" />
                          <span className="skeleton-line skeleton-caption" />
                        </section>
                      ))}
                    </aside>
                  </div>
                </div>
              </section>
            ) : dashboardLoadState === "error" ? (
              <section className="dashboard-load-error dashboard-section-enter" role="alert">
                <span className="empty-icon">
                  <CircleAlert size={22} />
                </span>
                <h2>Dashboard data is unavailable</h2>
                <p>{dashboardLoadError}</p>
                <button
                  className="primary-button"
                  onClick={() => void load(true)}
                >
                  <RotateCcw size={15} /> Try again
                </button>
              </section>
            ) : (
              <>
            <section className="stats-grid dashboard-section-enter dashboard-section-enter--1">
              <div
                className="stat-card clickable"
                role="button"
                tabIndex={0}
                onClick={() => goTo("jobs")}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") goTo("jobs");
                }}
              >
                <div className="stat-top">
                  <span>Jobs saved</span>
                  <span className="stat-icon blue">
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
                  <span>Awaiting your review</span>
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

            <div className="dash-grid dashboard-section-enter dashboard-section-enter--2" style={{ marginTop: 20 }}>
              <section className="panel">
                <div className="panel-head">
                  <div>
                    <h2>Recommended for you</h2>
                    <p>Your highest-scoring saved jobs. Scores are calculated from your profile and preferences.</p>
                  </div>
                  <button className="link-button" onClick={() => goTo("jobs")}>
                    View all <ArrowUpRight size={14} />
                  </button>
                </div>

                {!profile?.skills?.length && (
                  <div className="rescore-banner">
                    <span>Your profile has no skills yet, so scores are low confidence.</span>
                    <button className="secondary-button" onClick={() => openProfile()}>
                      Add skills
                    </button>
                  </div>
                )}

                {recommended.length === 0 ? (
                  <div className="empty-state">
                    <div className="empty-icon">
                      <BriefcaseBusiness size={25} />
                    </div>
                    <h3>Your next opportunity starts here</h3>
                    <p>Add a job posting to score it against your profile.</p>
                    <button className="primary-button" onClick={() => setShowJobForm(true)}>
                      <Plus size={16} /> Add your first job
                    </button>
                  </div>
                ) : (
                  <div className="jobs-list">{recommended.map((j) => renderJobCard(j, true))}</div>
                )}
              </section>

              <aside className="dash-side">
                <section className="panel approval-card">
                  <div className="approval-note">
                    <div className="secure-icon">
                      <ShieldCheck size={18} />
                    </div>
                    <div>
                      <h2>Human approval required</h2>
                      <p style={{ marginTop: 4, fontSize: 13 }}>
                        ThoughtLabs Pilot never submits an application for you. Review and approve it in
                        the Application Assistant, then submit it yourself on the employer's site.
                      </p>
                    </div>
                  </div>
                  <span className="status-pill">
                    <i /> Approval gate enabled
                  </span>
                </section>

                <section className="panel">
                  <div className="panel-head">
                    <h2>Pipeline</h2>
                    <button className="link-button" onClick={() => goTo("applications")}>
                      Open board
                    </button>
                  </div>
                  {pipelineCounts.length === 0 ? (
                    <p className="muted-note">No applications tracked yet.</p>
                  ) : (
                    pipelineCounts.map(([s, n]) => (
                      <div className="pipeline-row" key={s}>
                        <span>{statusLabel(s)}</span>
                        <strong>{n}</strong>
                        <span
                          className="pipeline-track"
                          role="img"
                          aria-label={`${n} of ${applications.length} applications are ${statusLabel(s)}`}
                        >
                          <span
                            style={{
                              width: `${Math.round((n / applications.length) * 100)}%`,
                            }}
                          />
                        </span>
                      </div>
                    ))
                  )}
                </section>

                <section className="panel">
                  <div className="panel-head">
                    <h2>Get set up</h2>
                  </div>
                  <ul className="checklist" style={{ marginTop: 0 }}>
                    {setup.map((s) => (
                      <li key={s.label} className={s.done ? "done" : ""}>
                        {s.done ? <CheckCircle2 size={16} /> : <Circle size={16} />}
                        {s.label}
                      </li>
                    ))}
                  </ul>
                </section>
              </aside>
            </div>

            <section className="panel recent-applications dashboard-section-enter dashboard-section-enter--3">
              <div className="panel-head">
                <div>
                  <h2>Recent applications</h2>
                  <p>Your latest tracked opportunities and their current status.</p>
                </div>
                <button className="link-button" onClick={() => goTo("applications")}>
                  View tracker <ArrowUpRight size={14} />
                </button>
              </div>

              {recentApplications.length === 0 ? (
                <div className="recent-empty">
                  <p>Applications you track will appear here.</p>
                  <button className="secondary-button" onClick={() => goTo("jobs")}>
                    Find jobs
                  </button>
                </div>
              ) : (
                <div className="recent-list">
                  {recentApplications.map((application) => {
                    const updated = timeInStage(application);
                    return (
                      <article
                        className={`recent-row ${
                          transitioningApplicationId === application.id
                            ? "status-transitioned"
                            : ""
                        }`}
                        key={application.id}
                      >
                        <span className="recent-icon">
                          <BriefcaseBusiness size={17} />
                        </span>
                        <div className="recent-job">
                          <strong>{application.job_title}</strong>
                          <span>{application.company}</span>
                        </div>
                        <div className="recent-status">
                          <span className={`status status-${application.status}`}>
                            {statusLabel(application.status)}
                          </span>
                          {updated && <small>Updated {updated}</small>}
                          {(application.allowed_next ?? []).length > 0 ? (
                            <select
                              value=""
                              disabled={updatingApplicationIds.has(application.id)}
                              aria-label={`Move ${application.job_title} to another status`}
                              onChange={(e) =>
                                void changeApplicationStatus(application, e.target.value)
                              }
                            >
                              <option value="">Move to...</option>
                              {application.allowed_next.map((status) => (
                                <option key={status} value={status}>
                                  {statusLabel(status)}
                                </option>
                              ))}
                            </select>
                          ) : (
                            <small>
                              {(application.gated_next ?? []).length > 0
                                ? "Approval workflow required"
                                : "Final status"}
                            </small>
                          )}
                        </div>
                      </article>
                    );
                  })}
                </div>
              )}
            </section>
              </>
            )}
          </div>
        )}

        {page === "jobs" && (
          <div className="page-view jobs-page">
            <section className="job-source-card" aria-label="Job source">
              <div className="job-source-heading">
                <span className="job-source-icon">
                  <BriefcaseBusiness size={18} />
                </span>
                <div>
                  <h2>Choose a job board</h2>
                  <p>Search listings from one source at a time.</p>
                </div>
              </div>
              <div className="job-source-options" role="group" aria-label="Job boards">
                {JOB_SEARCH_SOURCES.map((source) => (
                  <button
                    key={source.id}
                    type="button"
                    className={`job-source-option ${jobSource === source.id ? "selected" : ""}`}
                    aria-pressed={jobSource === source.id}
                    onClick={() => selectJobSource(source.id)}
                  >
                    <span className="source-option-title">
                      <span className={`source-brand-icon ${source.id.replace("_", "-")}-brand`}>
                        {source.mark}
                      </span>
                      {source.label}
                      {jobSource === source.id && <CheckCircle2 size={14} />}
                    </span>
                    <small>{source.description}</small>
                  </button>
                ))}
              </div>
              <p className="source-disclaimer">
                Job boards use JobSpy; Jobicy uses its public jobs API. Listings stay here until you save them. Availability and filters vary by source; application form filling remains review-first and is only supported on select employer sites.
              </p>
            </section>

            {jobSource !== "jobicy" ? (
            <>
            <section className="panel search-card linkedin-search-card">
              <div className="panel-head criteria-panel-head">
                <div>
                  <h2>Search criteria</h2>
                  <p>Find listings on {JOB_SEARCH_SOURCES.find((source) => source.id === jobSource)?.label}.</p>
                </div>
                <button
                  className="secondary-button"
                  type="button"
                  onClick={useProfileForLinkedInSearch}
                  disabled={!profile || (!profile.target_roles.length && !profile.skills.length)}
                >
                  Use my profile
                </button>
              </div>

              <div className="search-criteria-grid linkedin-criteria-grid">
                <label>
                  Job title or keywords
                  <input
                    value={linkedinKeywords}
                    onChange={(event) => setLinkedinKeywords(event.target.value)}
                    placeholder="e.g. Frontend Developer OR React"
                    maxLength={300}
                  />
                </label>
                <label>
                  Country and city
                  <input
                    value={linkedinLocation}
                    onChange={(event) => setLinkedinLocation(event.target.value)}
                    placeholder="e.g. Bengaluru, India"
                    maxLength={150}
                  />
                </label>
                <label>
                  Work arrangement
                  <select
                    value={linkedinWorkArrangement}
                    onChange={(event) => setLinkedinWorkArrangement(event.target.value)}
                  >
                    <option value="any">Any</option>
                    <option value="remote">Remote</option>
                    <option value="hybrid">Hybrid</option>
                    <option value="onsite">On-site</option>
                  </select>
                </label>
                <label>
                  Employment type
                  <select
                    value={linkedinEmploymentType}
                    onChange={(event) => setLinkedinEmploymentType(event.target.value)}
                  >
                    <option>Any</option>
                    <option>Full-time</option>
                    <option>Part-time</option>
                    <option>Internship</option>
                    <option>Contract</option>
                    <option>Temporary</option>
                  </select>
                </label>
                <label>
                  Experience level
                  <select
                    value={linkedinExperience}
                    onChange={(event) => setLinkedinExperience(event.target.value)}
                  >
                    <option value="any">Any</option>
                    <option value="internship">Internship</option>
                    <option value="entry">Entry level / fresher</option>
                    <option value="associate">Associate</option>
                    <option value="mid-senior">Mid-Senior</option>
                    <option value="director">Director</option>
                    <option value="executive">Executive</option>
                  </select>
                </label>
                <label className="salary-range-field">
                  Salary range
                  <span className="salary-range-inputs">
                    <input
                      type="number"
                      min="0"
                      value={linkedinSalaryMin}
                      onChange={(event) => setLinkedinSalaryMin(event.target.value)}
                      placeholder="Min"
                      aria-label="Minimum salary"
                    />
                    <span aria-hidden="true">–</span>
                    <input
                      type="number"
                      min="0"
                      value={linkedinSalaryMax}
                      onChange={(event) => setLinkedinSalaryMax(event.target.value)}
                      placeholder="Max"
                      aria-label="Maximum salary"
                    />
                    <input
                      className="salary-currency-input"
                      value={linkedinSalaryCurrency}
                      onChange={(event) =>
                        setLinkedinSalaryCurrency(event.target.value.toUpperCase())
                      }
                      maxLength={3}
                      aria-label="Salary currency"
                    />
                  </span>
                </label>
                <label>
                  Posted within
                  <select
                    value={linkedinPostedWithin}
                    onChange={(event) => setLinkedinPostedWithin(event.target.value)}
                  >
                    <option value="any">Any time</option>
                    <option value="24-hours">Past 24 hours</option>
                    <option value="week">Past week</option>
                    <option value="month">Past month</option>
                  </select>
                </label>
                <label>
                  Sort by
                  <select
                    value={linkedinSort}
                    onChange={(event) => setLinkedinSort(event.target.value)}
                  >
                    <option value="relevance">Relevance</option>
                    <option value="newest">Newest</option>
                  </select>
                </label>
                <div className="criteria-search-action">
                  <button
                    className="primary-button"
                    type="button"
                    disabled={discoveryBusy || linkedinKeywords.trim().length < 2}
                    onClick={() => void searchJobBoard()}
                  >
                    {discoveryBusy
                      ? "Searching..."
                      : `Search ${JOB_SEARCH_SOURCES.find((source) => source.id === jobSource)?.label} jobs`}
                    <Search size={14} />
                  </button>
                  <small>
                    Search results are temporary until you save a listing. Resume details are not sent to the job board.
                  </small>
                </div>
              </div>

              {profile && (profile.target_roles.length > 0 || profile.skills.length > 0) ? (
                <small>
                  Profile suggestions use only your saved target roles and skills; no
                  qualifications are generated.
                </small>
              ) : (
                <>
                  <small>
                    Add and save your confirmed target roles or skills in My Profile to
                    use profile-based suggestions. You can still edit the criteria and
                    search LinkedIn now.
                  </small>
                  <div className="row" style={{ marginTop: 8 }}>
                    <button
                      className="link-button"
                      type="button"
                      onClick={() => openProfile("profile-career")}
                    >
                      Review My Profile
                    </button>
                  </div>
                </>
              )}

              <small className="criteria-disclaimer">
                JobSpy searches this board using your keywords and location. Some boards may ignore filters they do not support; review each listing on its original site.
              </small>
              {discoveryBusy && (
                <p className="source-loading" role="status" aria-live="polite">
                  Searching {JOB_SEARCH_SOURCES.find((source) => source.id === jobSource)?.label}…
                </p>
              )}
              {discoveryMessage && (
                <div className={discoveryError ? "source-feedback error" : "source-feedback"} role={discoveryError ? "alert" : "status"}>
                  <span>{discoveryMessage}</span>
                  {discoveryError && (
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() => void searchJobBoard()}
                      disabled={discoveryBusy}
                    >
                      Try again
                    </button>
                  )}
                </div>
              )}
              {discoveryResults.length > 0 && (
                <>
                  <div className="source-results-heading">
                    <h3>{JOB_SEARCH_SOURCES.find((source) => source.id === jobSource)?.label} results</h3>
                    <span>{discoveryResults.length} listing{discoveryResults.length === 1 ? "" : "s"}</span>
                  </div>
                  <section className="jobs-list jobicy-results">
                    {discoveryResults.map((job) => {
                      const saved = jobs.some(
                        (existing) =>
                          existing.source === job.source &&
                          existing.source_job_id === job.source_job_id,
                      );
                      const publishedDate = job.published_at
                        ? new Date(job.published_at)
                        : null;
                      return (
                        <article className="job-card" key={`${job.source}-${job.source_job_id}`}>
                          <div className="company-logo">{job.company.slice(0, 1).toUpperCase()}</div>
                          <div className="job-info">
                            <h3>{job.title}</h3>
                            <p>{job.company}{job.location ? ` · ${job.location}` : ""}</p>
                            <div className="job-meta">
                              {job.work_arrangement && <span>{job.work_arrangement}</span>}
                              {job.employment_types.map((type) => <span key={type}>{type}</span>)}
                              {(job.salary_min != null || job.salary_max != null) && (
                                <span>
                                  {job.salary_currency ? `${job.salary_currency} ` : ""}
                                  {job.salary_min != null ? job.salary_min.toLocaleString() : "—"}
                                  –{job.salary_max != null ? job.salary_max.toLocaleString() : "—"}
                                  {" / "}{job.salary_period}
                                </span>
                              )}
                              {publishedDate && !Number.isNaN(publishedDate.getTime()) && (
                                <span>Posted {publishedDate.toLocaleDateString()}</span>
                              )}
                            </div>
                            {job.description && <p>{job.description.slice(0, 900)}</p>}
                            <small className="source-attribution">
                              Source: {JOB_SEARCH_SOURCES.find((source) => source.id === job.source)?.label ?? job.source}
                            </small>
                          </div>
                          <div className="job-actions">
                            <button
                              className="primary-button"
                              type="button"
                              disabled={saved || savingDiscoveryId === job.source_job_id}
                              onClick={() => void saveDiscoveredJob(job)}
                            >
                              {saved
                                ? "Saved"
                                : savingDiscoveryId === job.source_job_id
                                  ? "Saving..."
                                  : "Save job"}
                            </button>
                            <a href={job.url} target="_blank" rel="noreferrer" className="secondary-button">
                              Original posting <ArrowUpRight size={14} />
                            </a>
                          </div>
                        </article>
                      );
                    })}
                  </section>
                </>
              )}
            </section>
            </>
            ) : (
            <section className="panel search-card jobicy-search-card">
              <div className="panel-head criteria-panel-head">
                <div>
                  <h2>Search criteria</h2>
                  <p>Search recent public remote listings from Jobicy.</p>
                </div>
              </div>
              <form
                className="search-criteria-grid jobicy-criteria-grid"
                onSubmit={(event) => {
                  event.preventDefault();
                  void searchJobicy();
                }}
              >
                <label>
                  Keywords
                  <input
                    value={discoveryWhat}
                    onChange={(event) => {
                      setDiscoveryWhat(event.target.value);
                      clearDiscoveryResults();
                    }}
                    placeholder="e.g. software engineer"
                    minLength={2}
                    maxLength={100}
                    required
                  />
                </label>
                <label>
                  Location eligibility
                  <input
                    value={discoveryWhere}
                    onChange={(event) => {
                      setDiscoveryWhere(event.target.value);
                      clearDiscoveryResults();
                    }}
                    placeholder="e.g. Bengaluru, India"
                    maxLength={100}
                  />
                </label>
                <label>
                  Region
                  <select
                    value={discoveryGeo}
                    onChange={(event) => {
                      setDiscoveryGeo(event.target.value);
                      clearDiscoveryResults();
                    }}
                  >
                    <option value="apac">Asia-Pacific</option>
                    <option value="europe">Europe</option>
                    <option value="usa">USA</option>
                    <option value="canada">Canada</option>
                    <option value="uk">United Kingdom</option>
                  </select>
                </label>
                <button className="primary-button" type="submit" disabled={discoveryBusy}>
                  {discoveryBusy ? "Searching..." : "Search Jobicy"}
                </button>
              </form>
              <small className="source-disclaimer">
                Jobicy supplies remote listings and broad region filters; it does not
                offer an India- or city-specific filter. Location is matched locally
                against the eligibility text provided by the source. Results link to
                Jobicy, as required by its public API terms.
              </small>
              {discoveryBusy && (
                <p className="source-loading" role="status" aria-live="polite">
                  Searching recent Jobicy listings…
                </p>
              )}
              {discoveryMessage && (
                <div className={discoveryError ? "source-feedback error" : "source-feedback"} role={discoveryError ? "alert" : "status"}>
                  <span>{discoveryMessage}</span>
                  {discoveryError && (
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() => void searchJobicy()}
                      disabled={discoveryBusy}
                    >
                      Try again
                    </button>
                  )}
                </div>
              )}
              {discoveryResults.length > 0 && (
                <>
                  <div className="source-results-heading">
                    <h3>Jobicy results</h3>
                    <span>
                      Page {discoveryPage} · {discoveryResults.length} listing
                      {discoveryResults.length === 1 ? "" : "s"}
                    </span>
                  </div>
                  <section className="jobs-list jobicy-results">
                    {discoveryResults.map((job) => {
                      const saved = jobs.some(
                        (existing) =>
                          existing.source === "jobicy" &&
                          existing.source_job_id === job.source_job_id,
                      );
                      const publishedDate = job.published_at
                        ? new Date(job.published_at)
                        : null;
                      return (
                        <article className="job-card" key={job.source_job_id}>
                          <div className="company-logo">
                            {job.company.slice(0, 1).toUpperCase()}
                          </div>
                          <div className="job-info">
                            <h3>{job.title}</h3>
                            <p>
                              {job.company}
                              {job.location ? ` · Remote eligibility: ${job.location}` : ""}
                            </p>
                            <div className="job-meta">
                              <span>Remote</span>
                              {job.work_arrangement && <span>{job.work_arrangement}</span>}
                              {job.employment_types.map((type) => (
                                <span key={type}>{type}</span>
                              ))}
                              {job.experience_level && <span>{job.experience_level}</span>}
                              {(job.salary_min != null || job.salary_max != null) && (
                                <span>
                                  Salary: {job.salary_currency ? `${job.salary_currency} ` : ""}
                                  {job.salary_min != null ? job.salary_min.toLocaleString() : "—"}
                                  {"–"}
                                  {job.salary_max != null ? job.salary_max.toLocaleString() : "—"}
                                  {" / "}
                                  {job.salary_period}
                                </span>
                              )}
                              {publishedDate && !Number.isNaN(publishedDate.getTime()) && (
                                <span>Posted {publishedDate.toLocaleDateString()}</span>
                              )}
                            </div>
                            {job.description && (
                              <p>{job.description.slice(0, 900)}</p>
                            )}
                            {job.skills.length > 0 && (
                              <div className="skill-list">
                                {job.skills.slice(0, 6).map((skill) => (
                                  <span key={skill}>{skill}</span>
                                ))}
                              </div>
                            )}
                            <small className="source-attribution">Source: Jobicy</small>
                          </div>
                          <div className="job-actions">
                            <button
                              className="primary-button"
                              type="button"
                              disabled={saved || savingDiscoveryId === job.source_job_id}
                              onClick={() => void saveDiscoveredJob(job)}
                            >
                              {saved
                                ? "Saved"
                                : savingDiscoveryId === job.source_job_id
                                  ? "Saving..."
                                  : "Save job"}
                            </button>
                            <a
                              href={job.url}
                              target="_blank"
                              rel="noreferrer"
                              className="secondary-button"
                            >
                              Original posting <ArrowUpRight size={14} />
                            </a>
                          </div>
                        </article>
                      );
                    })}
                  </section>
                </>
              )}
              {discoveryHasMore && discoveryNextCursor && (
                <div className="row" style={{ justifyContent: "center", marginTop: 16 }}>
                  <button
                    className="secondary-button"
                    type="button"
                    disabled={discoveryBusy}
                    onClick={() =>
                      void searchJobicy(discoveryNextCursor, discoveryPage + 1)
                    }
                  >
                    {discoveryBusy ? "Loading..." : "Next page"}
                  </button>
                </div>
              )}
            </section>
            )}

            <section className="panel search-card saved-matches-card">
              <div className="job-results-heading">
                <div>
                  <h2><Sparkles size={18} /> Job matches</h2>
                  <p>
                    {visibleJobs.length} saved job{visibleJobs.length === 1 ? "" : "s"} matching your profile and preferences
                    {activeSavedFilterCount ? ` · ${activeSavedFilterCount} filter${activeSavedFilterCount === 1 ? "" : "s"} active` : ""}
                  </p>
                </div>
                <span className="match-count-badge">{visibleJobs.length}</span>
              </div>
              <div className="saved-job-search-controls">
              <label className="searchbox">
                <Search size={18} />
                <input
                  placeholder="Search by title, company or location"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  aria-label="Search jobs"
                />
              </label>

              <details className="advanced-filters saved-job-filters">
                <summary>
                  Filters and sorting
                  {activeSavedFilterCount > 0 && (
                    <span className="filter-count">{activeSavedFilterCount} active</span>
                  )}
                </summary>
                <div className="filter-bar">
                  <label>
                    Status
                    <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
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
                    <select value={minMatch} onChange={(e) => setMinMatch(Number(e.target.value))}>
                      <option value={0}>Any</option>
                      <option value={25}>25%+</option>
                      <option value={50}>50%+</option>
                      <option value={75}>75%+</option>
                    </select>
                  </label>

                  <label>
                    Source
                    <select value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)}>
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
                      onChange={(e) => setSortBy(e.target.value as "best" | "newest" | "company")}
                    >
                      <option value="best">Best match</option>
                      <option value="newest">Newest</option>
                      <option value="company">Company</option>
                    </select>
                  </label>
                </div>
              </details>
              </div>

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

            <section className="jobs-list saved-job-results-list">
              {visibleJobs.length === 0 ? (
                <div className="empty-state">
                  <div className="empty-icon">
                    <BriefcaseBusiness size={25} />
                  </div>
                  <h3>{jobs.length === 0 ? "Your next opportunity starts here" : "No jobs match these filters"}</h3>
                  <p>
                    {jobs.length === 0
                      ? "Search a job board or add a posting to start scoring opportunities against your profile."
                      : "Try clearing the search or lowering the minimum match."}
                  </p>
                  {jobs.length === 0 && (
                    <button className="primary-button" onClick={() => setShowJobForm(true)}>
                      <Plus size={16} /> Add your first job
                    </button>
                  )}
                  {jobs.length > 0 && (
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() => {
                        setQuery("");
                        setStatusFilter("all");
                        setMinMatch(0);
                        setSourceFilter("all");
                        setSortBy("best");
                      }}
                    >
                      Clear search and filters
                    </button>
                  )}
                </div>
              ) : (
                visibleJobs.map((job) => renderJobCard(job))
              )}
            </section>
            </section>

            {dismissTip && (
              <div className="rescore-banner" style={{ marginTop: 16 }}>
                <span>
                  You passed on {dismissTip.count} jobs because of “{dismissTip.label.toLowerCase()}
                  ”. Your {dismissTip.page === "profile" ? "profile" : "job preferences"} may need a
                  tweak so these score lower.
                </span>
                <button
                  className="secondary-button"
                  onClick={() =>
                    dismissTip.page === "profile" ? openProfile() : openPreferences()
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
          </div>
        )}

        {page === "copilot" && (
          <section className="copilot page-view">
            <div className="panel chat-card">
              <div className="chat-head">
                <span className="bot-avatar">
                  <Bot size={20} />
                </span>
                <div>
                  <strong>Thoughtlabs Copilot</strong>
                  <small>Answers come from your saved jobs, profile and applications</small>
                </div>
              </div>

              <div className="chat-thread" ref={threadRef} aria-live="polite">
                {chat.map((m) =>
                  m.role === "user" ? (
                    <div className="bubble-row user" key={m.id}>
                      <div className="bubble user">{m.text}</div>
                    </div>
                  ) : (
                    <div className="bubble-row" key={m.id}>
                      <span className="bot-avatar">
                        <Bot size={16} />
                      </span>
                      <div className="bubble assistant">
                        <div>{m.text}</div>
                        {m.lines && m.lines.length > 0 && (
                          <ul>
                            {m.lines.map((line) => (
                              <li key={line}>{line}</li>
                            ))}
                          </ul>
                        )}
                        {m.cta && (
                          <div className="bubble-actions">
                            <button className="secondary-button" onClick={m.cta.run}>
                              {m.cta.label}
                            </button>
                          </div>
                        )}
                        {m.derived && (
                          <div>
                            <span className="tag-derived">
                              <CheckCircle2 size={12} /> Computed from your data
                            </span>
                          </div>
                        )}
                      </div>
                    </div>
                  ),
                )}
              </div>

              <div className="chip-row" role="group" aria-label="Quick actions">
                <button className="chip" onClick={() => runQuickAction("top")}>
                  <Sparkles size={14} /> Best matches
                </button>
                <button className="chip" onClick={() => runQuickAction("gaps")}>
                  <Lightbulb size={14} /> Skill gaps
                </button>
                <button className="chip" onClick={() => runQuickAction("approval")}>
                  <ShieldCheck size={14} /> Needs my approval
                </button>
                <button className="chip" onClick={() => runQuickAction("profile")}>
                  <User size={14} /> Profile check
                </button>
                <button className="chip" onClick={() => runQuickAction("rescore")} disabled={busy}>
                  <RotateCcw size={14} /> Re-score jobs
                </button>
                <button className="chip" onClick={() => runQuickAction("add")}>
                  <Plus size={14} /> Add a job
                </button>
              </div>

              <form className="composer" onSubmit={(e) => e.preventDefault()}>
                <input
                  disabled
                  placeholder="Free-text chat is not available yet"
                  aria-label="Message the Copilot"
                />
                <button type="submit" disabled aria-label="Send">
                  <Send size={16} />
                </button>
              </form>
              <p className="composer-note">
                Free-text questions need a Copilot endpoint that the backend does not have yet.
                Quick actions work today.
              </p>
            </div>

            <aside className="dash-side">
              <section className="panel">
                <div className="panel-head">
                  <h2>What the Copilot can see</h2>
                </div>
                <div className="pipeline-row">
                  <span>Saved jobs</span>
                  <strong>{jobs.length}</strong>
                </div>
                <div className="pipeline-row">
                  <span>Applications</span>
                  <strong>{applications.length}</strong>
                </div>
                <div className="pipeline-row">
                  <span>Profile skills</span>
                  <strong>{profile?.skills?.length ?? 0}</strong>
                </div>
                <div className="pipeline-row">
                  <span>Active resume</span>
                  <strong>{resumes.find((r) => r.is_active)?.label ?? "None"}</strong>
                </div>
              </section>

              <section className="panel approval-card">
                <div className="approval-note">
                  <div className="secure-icon">
                    <ShieldCheck size={18} />
                  </div>
                  <div>
                    <h2>You stay in control</h2>
                    <p style={{ marginTop: 4, fontSize: 13 }}>
                      The Copilot only reads and summarises. It cannot apply to a job.
                    </p>
                  </div>
                </div>
              </section>
            </aside>
          </section>
        )}

        {page === "applications" && (
          <div className="page-view">
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
            <button className="primary-button" onClick={() => goTo("jobs")}>
              Find jobs
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
                        aria-label="Only the Application Assistant can set this status"
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
                              ? "Use the Application Assistant"
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
                          } ${
                            transitioningApplicationId === application.id
                              ? "status-transitioned"
                              : ""
                          }`}
                          draggable={!updatingApplicationIds.has(application.id)}
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
                              disabled={
                                nextStatuses.length === 0 ||
                                updatingApplicationIds.has(application.id)
                              }
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

                          <button
                            type="button"
                            className="secondary-button aa-prepare"
                            draggable={false}
                            onClick={() => setAssistantJobId(application.job_id)}
                          >
                            <Bot size={13} /> Automate
                          </button>

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
                <article
                  className={`application-row ${
                    transitioningApplicationId === application.id
                      ? "status-transitioned"
                      : ""
                  }`}
                  key={application.id}
                >
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
                    disabled={
                      nextStatuses.length === 0 ||
                      updatingApplicationIds.has(application.id)
                    }
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
                      Approval and submission are only available through the
                      Application Assistant.
                    </p>
                  )}

                  <button
                    type="button"
                    className="secondary-button aa-prepare"
                    onClick={() => setAssistantJobId(application.job_id)}
                  >
                    <Bot size={13} /> Automate
                  </button>

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
          </div>
        )}

        <footer>
          ThoughtLabs AI <span>·</span> ThoughtLabs automation, human decisions.
        </footer>
      </main>

      {assistantJobId && (
        <ApplicationAssistant
          jobId={assistantJobId}
          onClose={() => setAssistantJobId(null)}
          onChanged={() => void load()}
          onExportMaterials={(jobId, materials: AssistantMaterials, kind) => {
            const job = jobs.find((candidate) => candidate.id === jobId);
            if (!job) {
              setNotice("Could not find this job in the saved job list to export its materials.");
              return;
            }
            const exportMaterials: ApplicationMaterials = {
              job_id: jobId,
              title: job.title,
              company: job.company,
              ...materials,
            };
            if (kind === "cover_letter") {
              void downloadCoverLetterPdf(job, exportMaterials);
            } else {
              void downloadTailoredResume(job, exportMaterials);
            }
          }}
        />
      )}

      {analysisJob && (
        <MatchAnalysis
          job={jobsById.get(String(analysisJob.id)) ?? analysisJob}
          busy={busy}
          onClose={() => setAnalysisJob(null)}
          onTrack={() => {
            const job = analysisJob;
            setAnalysisJob(null);
            void trackJob(job);
          }}
          plan={analysisPlan[analysisJob.id] ?? null}
          planLoading={analysisPlanLoading}
          onLoadPlan={() => {
            void loadApplicationPlan(analysisJob, true);
          }}
          materials={analysisMaterials[analysisJob.id] ?? null}
          materialsLoading={materialsLoadingId === analysisJob.id}
          onGenerateMaterials={() => {
            void generateApplicationMaterials(analysisJob);
          }}
          onUpdateMaterials={(updates) =>
            updateApplicationMaterials(analysisJob.id, updates)
          }
          onCopyText={(text, label) => void copyGeneratedText(text, label)}
          onDownloadCoverLetter={() => {
            const materials = analysisMaterials[analysisJob.id];
            if (materials) void downloadCoverLetterPdf(analysisJob, materials);
          }}
          onDownloadResume={() => {
            const materials = analysisMaterials[analysisJob.id];
            if (materials) void downloadTailoredResume(analysisJob, materials);
          }}
        />
      )}

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
              <summary>Paste job details or use a job URL to fill this form</summary>
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
                  disabled={importing || (pasteText.trim().length < 20 && !form.url.trim())}
                  onClick={() => void importFromText()}
                >
                  {importing ? "Reading..." : "Fill the form"}
                </button>
                <small>Paste text above or enter the job URL below. Nothing is saved until you press Save opportunity.</small>
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

            <h3 className="section-title" id="my-resumes">
              My resumes
            </h3>
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
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() => void openResumeFile(resume)}
                  >
                    View
                  </button>
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