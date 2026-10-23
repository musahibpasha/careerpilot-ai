import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  BriefcaseBusiness,
  CheckCircle2,
  CircleAlert,
  Gauge,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  Target,
  Upload,
  X,
} from "lucide-react";

const API = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

type Job = {
  id: string;
  title: string;
  company: string;
  location: string | null;
  source: string;
  url: string | null;
  description: string;
  skills: string[];
  match_score: number;
  match_reasons: string[];
};

type Application = {
  id: string;
  job_id: string;
  status: string;
  allowed_next: string[];
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
  const [showJobForm, setShowJobForm] = useState(false);
  const [showProfile, setShowProfile] = useState(false);
  const [busy, setBusy] = useState(false);
  const [parsing, setParsing] = useState(false);
  const [parseMessage, setParseMessage] = useState("");
  const [notice, setNotice] = useState("");
  const [formError, setFormError] = useState("");

  const [form, setForm] = useState({
    title: "",
    company: "",
    location: "",
    url: "",
    description: "",
    skills: "",
  });

  const [profileForm, setProfileForm] = useState<ProfileForm>(emptyProfileForm());
  const [profileErrors, setProfileErrors] = useState<Record<string, string>>({});

  async function load() {
    // Load each part separately so one failing request (e.g. the profile)
    // can never hide the jobs and the application tracker.
    const get = async (path: string) => {
      const response = await fetch(`${API}${path}`);
      if (!response.ok) throw new Error(`${path} returned HTTP ${response.status}`);
      return response.json();
    };

    const [jobsResult, applicationsResult, profileResult] =
      await Promise.allSettled([
        get("/api/jobs"),
        get("/api/applications"),
        get("/api/profile"),
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
    setFormError("");
  }, [showJobForm, showProfile]);

  const visibleJobs = useMemo(
    () =>
      jobs.filter((j) =>
        `${j.title} ${j.company} ${j.location || ""}`
          .toLowerCase()
          .includes(query.toLowerCase()),
      ),
    [jobs, query],
  );

  const appliedCount = applications.filter((a) =>
    ["applied", "interview", "offer", "rejected"].includes(a.status),
  ).length;

  const reviewCount = applications.filter(
    (a) => a.status === "needs_review",
  ).length;

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
          skills: form.skills
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
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
      setForm({
        title: "",
        company: "",
        location: "",
        url: "",
        description: "",
        skills: "",
      });

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
      const status = existingApplication.status
        .replace("_", " ")
        .replace(/\b\w/g, (letter) => letter.toUpperCase());

      setNotice(
        `${job.company} is already tracked with status "${status}". Check the Application tracker below.`,
      );

      // Bring the existing application tracker into view.
      document.getElementById("applications")?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });

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

      setNotice(`Tracking started for ${job.company}.`);
      await load();

      document.getElementById("applications")?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
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
        `${API}/api/applications/${application.id}/status?status=${encodeURIComponent(nextStatus)}`,
        { method: "PATCH" },
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

        <a className="nav active">
          <Gauge size={18} /> Overview
        </a>

        <a className="nav" onClick={() => setShowProfile(true)}>
          <Target size={18} /> My profile
        </a>

        <a className="nav" href="#applications">
          <BriefcaseBusiness size={18} /> Applications
          <span className="nav-count">{applications.length}</span>
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
            <div className="eyebrow">YOUR CAREER, IN CONTROL</div>
            <h1>Job hunt overview</h1>
            <p>
              Find the right opportunities. Spend less time on repetitive work.
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

          <div className="stat-card">
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

                  <MatchReasons reasons={job.match_reasons} />
                </div>

                <div className="job-actions">
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

        <section className="section-head application-heading" id="applications">
          <div>
            <h2>Application tracker</h2>
            <p>Keep every opportunity moving forward.</p>
          </div>
        </section>

        <section className="application-list">
          {applications.length === 0 ? (
            <div className="simple-empty">
              No applications tracked yet. Save a job from the opportunity feed
              to begin.
            </div>
          ) : (
            applications.map((application) => {
              const nextStatuses = application.allowed_next ?? [];

              return (
                <article className="application-row" key={application.id}>
                  <div className="application-symbol">
                    <BriefcaseBusiness size={17} />
                  </div>

                  <div className="application-name">
                    <strong>{application.job_title}</strong>
                    <span>{application.company}</span>
                  </div>

                  <span className={`status status-${application.status}`}>
                    {application.status.replace("_", " ")}
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
                        {status
                          .split("_")
                          .map(
                            (word) =>
                              word.charAt(0).toUpperCase() + word.slice(1),
                          )
                          .join(" ")}
                      </option>
                    ))}
                  </select>
                </article>
              );
            })
          )}
        </section>

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
                {parsing ? "Reading resume..." : "Upload resume (PDF) to auto-fill"}
                <input
                  type="file"
                  accept="application/pdf"
                  disabled={parsing}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) parseResume(file);
                    e.target.value = "";
                  }}
                />
              </label>
              {parseMessage && <small className="reason ok">{parseMessage}</small>}
              <small className="muted-note">
                Fields marked <span className="req">*</span> are required.
              </small>
            </div>

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

            <h3 className="section-title">Career summary</h3>
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
    </div>
  );
}

export default App;