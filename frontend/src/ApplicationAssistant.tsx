import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowUpRight,
  Check,
  CheckCircle2,
  CircleAlert,
  Copy,
  Building2,
  Globe2,
  Lock,
  PanelsTopLeft,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";

const API = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

type Step = "job" | "kit" | "fill" | "review" | "done";
const STEP_LABELS: Record<Step, string> = {
  job: "Job",
  kit: "Kit",
  fill: "Fill",
  review: "Review",
  done: "Done",
};

type Row = {
  key: string;
  group: "fact" | "answer" | "draft";
  label: string;
  value: string;
  source: string;
  verification: string;
};
type DraftMeta = {
  key: string;
  label: string;
  text: string;
  source: string;
  can_restore: boolean;
  needs_input: boolean;
  reason: string;
};
type FieldResult = {
  label: string;
  state: "filled" | "skipped" | "needs_input" | "failed";
  key: string;
  reason: string;
  required: boolean;
};
type Run = {
  id: string;
  host: string;
  adapter: string;
  status: "running" | "prepared" | "needs_you" | "failed";
  pause_reason: string;
  fields: FieldResult[];
  started_at: string;
  finished_at: string | null;
  blocked_writes: number;
  browser_open: boolean;
  submission_enabled: boolean;
  error_category?: string;
};
type ResumeOption = {
  id: string;
  label: string;
  filename: string;
  is_active: boolean;
};
export type AssistantMaterials = {
  cover_letter: string;
  resume_summary: string;
  skills_to_prioritize: string[];
  bullet_suggestions: { section: string; source_text: string; tailored_text: string }[];
  caveats: string[];
};
type AssistantState = {
  application_id: string;
  job: {
    id: string;
    title: string;
    company: string;
    location: string | null;
    url: string;
    source: string;
    match_score: number;
    match_confidence: string;
    match_reasons: string[];
  };
  status: string;
  status_history: { from_status: string | null; to_status: string; at: string; actor: string; note: string }[];
  resume: ResumeOption | null;
  available_resumes: ResumeOption[];
  materials: AssistantMaterials | null;
  application_url: string;
  linkedin_job: boolean;
  manual_checklist: string[];
  kit: { rows: Row[]; drafts: Record<string, DraftMeta> };
  review: { hash: string; confirmed: boolean; confirmed_at: string | null; stale: boolean };
  steps: { current: Step; done: Record<Step, boolean>; order: Step[] };
  runs: Run[];
  submitted_at: string | null;
  generation: { used: string; fallback_reason: string; kept_edited: string[] } | null;
};

const SOURCE_LABELS: Record<string, string> = {
  profile: "From profile",
  derived: "From profile",
  user: "Entered by you",
  user_edited: "Edited by you",
  ai: "AI-generated",
  rule_based: "Template",
  missing: "Needs your input",
};
const STATUS_TEXT: Record<string, string> = {
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
const PAUSE_TEXT: Record<string, string> = {
  login_required: "The page asks you to log in. Log in yourself, then start again.",
  glassdoor_interactive_required: "Glassdoor autofill needs a visible browser window and an interactive wait period. Set the assistant browser to visible and allow it to stay open, then try again.",
  glassdoor_wait_timeout: "The Glassdoor browser timed out before an application form appeared. Your local sign-in session is kept; start the run again when ready.",
  glassdoor_profile_in_use: "Another Glassdoor browser run is already open. Finish or close that run before starting a second one.",
  glassdoor_site_blocked: "Glassdoor's security page blocked this browser session. CareerPilot won't bypass it. Open the listing in Chrome yourself and use the Application Kit to review or copy answers.",
  captcha: "A CAPTCHA is on the page. Complete it yourself; CareerPilot does not solve or bypass it.",
  unsupported_form: "No fillable form was found here. This page's structure is not supported; use the kit manually.",
  required_fields_need_input: "Some required fields need your input before this form is complete.",
  field_failed: "At least one field could not be filled. Check the list below.",
  navigation_timeout: "The employer page took too long to open. Check the URL and try again.",
  navigation_failed: "The page could not be opened. Check the address and your connection.",
  unsafe_redirect: "The page tried to redirect outside its supported employer site. CareerPilot blocked that navigation.",
  browser_unavailable:
    "The browser could not start. Run `python -m playwright install chromium` in the backend environment (see the README).",
  chrome_unavailable:
    "Google Chrome could not start. Install Chrome Stable, then restart CareerPilot. The Glassdoor session uses a separate local Chrome profile.",
  unsafe_destination: "The application page resolves to a private or internal address and was blocked.",
  unexpected_error: "The browser run stopped unexpectedly. The form was not submitted. Try opening the employer page directly; if this happens again, check the backend log for the run's error category.",
};
const FIELD_ORDER = { failed: 0, needs_input: 1, filled: 2, skipped: 3 } as const;

function platformFor(host: string) {
  const normalised = host.toLowerCase();
  if (normalised === "greenhouse.io" || normalised.endsWith(".greenhouse.io")) {
    return { label: "Greenhouse", icon: Building2 };
  }
  if (normalised === "lever.co" || normalised.endsWith(".lever.co")) {
    return { label: "Lever", icon: PanelsTopLeft };
  }
  if (normalised === "glassdoor.com" || normalised.endsWith(".glassdoor.com") ||
      normalised === "glassdoor.co.in" || normalised.endsWith(".glassdoor.co.in")) {
    return { label: "Glassdoor", icon: Globe2 };
  }
  return { label: "Employer site", icon: Globe2 };
}

function supportedPlatformForUrl(raw: string) {
  try {
    const url = new URL(raw);
    if (url.protocol !== "https:" || (url.port && url.port !== "443") || url.username || url.password) {
      return null;
    }
    const host = url.hostname.toLowerCase();
    if (host === "greenhouse.io" || host.endsWith(".greenhouse.io")) {
      return { label: "Greenhouse", icon: Building2 };
    }
    if (host === "lever.co" || host.endsWith(".lever.co")) {
      return { label: "Lever", icon: PanelsTopLeft };
    }
    if (host === "glassdoor.com" || host.endsWith(".glassdoor.com") ||
        host === "glassdoor.co.in" || host.endsWith(".glassdoor.co.in")) {
      return { label: "Glassdoor", icon: Globe2 };
    }
  } catch {
    return null;
  }
  return null;
}

function sourceLabel(source: string) {
  const labels: Record<string, string> = {
    jobicy: "Jobicy listing",
    linkedin: "LinkedIn listing",
    indeed: "Indeed listing",
    glassdoor: "Glassdoor listing",
    zip_recruiter: "ZipRecruiter listing",
    bayt: "Bayt listing",
    naukri: "Naukri listing",
    bdjobs: "BDJobs listing",
    hellowork: "HelloWork listing",
  };
  return labels[source] ?? "Saved job link";
}

const ANSWER_HELP: Record<string, string> = {
  notice_period: "e.g. 30 days",
  expected_salary: "e.g. 8 LPA",
  work_authorization: "Yes / No",
  visa_sponsorship: "Yes / No",
};

function errText(data: unknown, fallback: string): string {
  const d = (data as { detail?: unknown })?.detail;
  if (typeof d === "string") return d;
  if (d && typeof d === "object" && typeof (d as { message?: string }).message === "string") {
    return (d as { message: string }).message;
  }
  return fallback;
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    /* empty body */
  }
  if (!res.ok) throw new Error(errText(data, `Request failed (${res.status}).`));
  return data as T;
}

function SourceChip({ source }: { source: string }) {
  return <span className={`aa-chip aa-src-${source}`}>{SOURCE_LABELS[source] ?? source}</span>;
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="ghost-button aa-copy"
      disabled={!text}
      aria-label={`Copy ${label}`}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
        } catch {
          const t = document.createElement("textarea");
          t.value = text;
          document.body.appendChild(t);
          t.select();
          document.execCommand("copy");
          t.remove();
        }
        setDone(true);
        window.setTimeout(() => setDone(false), 1500);
      }}
    >
      {done ? <Check size={14} /> : <Copy size={14} />} {done ? "Copied" : "Copy"}
    </button>
  );
}

export default function ApplicationAssistant({
  jobId,
  onClose,
  onChanged,
  onExportMaterials,
}: {
  jobId: string;
  onClose: () => void;
  onChanged: () => void;
  onExportMaterials: (
    jobId: string,
    materials: AssistantMaterials,
    kind: "cover_letter" | "resume",
  ) => void;
}) {
  const [s, setS] = useState<AssistantState | null>(null);
  const [step, setStep] = useState<Step>("job");
  const [loadError, setLoadError] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [autoFillUrl, setAutoFillUrl] = useState<string | null>(null);
  const autoFillStartedForJob = useRef<string | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({}); // "fact:phone" | "answer:x" | "draft:y"
  const [applyUrl, setApplyUrl] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const [selfSubmitted, setSelfSubmitted] = useState(false);
  const [uploadResume, setUploadResume] = useState(false);
  const [authorizeManualWrite, setAuthorizeManualWrite] = useState(false);
  const [materialEdits, setMaterialEdits] = useState<Record<string, string>>({});
  const closeRef = useRef<HTMLButtonElement>(null);
  const materialsFingerprint = useRef("");

  const base = s ? `/api/applications/${s.application_id}/assistant` : "";

  const adopt = useCallback((next: AssistantState) => {
    setS(next);
    setApplyUrl((cur) =>
      cur ||
      (supportedPlatformForUrl(next.application_url) ? next.application_url : "") ||
      (supportedPlatformForUrl(next.job.url) ? next.job.url : ""),
    );
    if (next.status !== "approved" || !next.review.confirmed) {
      setAuthorizeManualWrite(false);
    }
    const fingerprint = JSON.stringify(next.materials ?? null);
    if (fingerprint !== materialsFingerprint.current) {
      materialsFingerprint.current = fingerprint;
      setMaterialEdits({});
    }
  }, []);

  useEffect(() => {
    let alive = true;
    api<AssistantState>(`/api/jobs/${jobId}/assistant`, { method: "POST" })
      .then((data) => {
        if (!alive) return;
        adopt(data);
        setStep(data.steps.current === "job" ? "fill" : data.steps.current);
        onChanged(); // the tracker may have gained the application
        if (
          !data.linkedin_job &&
          data.runs.length === 0 &&
          !["submitted", "interview", "offer", "rejected", "withdrawn"].includes(data.status)
        ) {
          setAutoFillUrl(
            [data.application_url, data.job.url].find((url) =>
              supportedPlatformForUrl(url),
            ) ?? null,
          );
        }
      })
      .catch((e: Error) => alive && setLoadError(e.message));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  useEffect(() => {
    if (
      !s ||
      !autoFillUrl ||
      s.job.id !== jobId ||
      autoFillStartedForJob.current === jobId
    ) {
      return;
    }

    autoFillStartedForJob.current = jobId;
    setBusy("auto-fill");
    setError("");
    let alive = true;
    void (async () => {
      try {
        await api(`/api/applications/${s.application_id}/assistant/fill`, {
          method: "POST",
          body: JSON.stringify({ url: autoFillUrl, upload_resume: false }),
        });
        const next = await api<AssistantState>(
          `/api/applications/${s.application_id}/assistant`,
        );
        if (alive) {
          adopt(next);
          setStep("fill");
        }
      } catch (e) {
        if (alive) setError((e as Error).message);
      } finally {
        if (alive) setBusy("");
      }
    })();
    return () => {
      alive = false;
    };
  }, [s, autoFillUrl, jobId, adopt]);

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Poll the browser-assistance run while it is in progress.
  const running = !!s?.runs.some((r) => r.status === "running");
  useEffect(() => {
    if (!running || !base) return;
    const t = window.setInterval(async () => {
      try {
        adopt(await api<AssistantState>(base));
        setError("");
      } catch (e) {
        setError((e as Error).message);
      }
    }, 1500);
    return () => window.clearInterval(t);
  }, [running, base, adopt]);

  async function run(label: string, fn: () => Promise<AssistantState | void>, ok = "") {
    setBusy(label);
    setError("");
    setNotice("");
    try {
      const next = await fn();
      if (next) adopt(next);
      if (ok) setNotice(ok);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  function goto(next: Step) {
    setStep(next);
    setError("");
    setNotice("");
    if (base) {
      void api(`${base}/step`, { method: "PUT", body: JSON.stringify({ step: next }) })
        .catch((e: Error) => setError(e.message));
    }
  }

  const dirty = Object.keys(edits).length > 0;
  const materialsDirty = Object.keys(materialEdits).length > 0;
  const materials = s?.materials;

  function materialValue(key: string, stored: string) {
    return key in materialEdits ? materialEdits[key] : stored;
  }

  function saveMaterials() {
    if (!materials) return;
    const body = {
      ...materials,
      cover_letter: materialValue("cover_letter", materials.cover_letter),
      resume_summary: materialValue("resume_summary", materials.resume_summary),
      bullet_suggestions: materials.bullet_suggestions.map((bullet, index) => ({
        ...bullet,
        tailored_text: materialValue(`bullet:${index}`, bullet.tailored_text),
      })),
    };
    return run(
      "materials",
      async () => {
        const next = await api<AssistantState>(`${base}/materials`, {
          method: "PUT",
          body: JSON.stringify(body),
        });
        setMaterialEdits({});
        setReviewed(false);
        return next;
      },
      "Tailored materials saved for this application.",
    );
  }

  function selectResume(resumeId: string) {
    setUploadResume(false);
    return run("resume", () =>
      api<AssistantState>(`${base}/resume`, {
        method: "PUT",
        body: JSON.stringify({ resume_id: resumeId || null }),
      }),
    );
  }

  function saveKit() {
    const body = { facts: {} as Record<string, string>, answers: {} as Record<string, string>, drafts: {} as Record<string, string> };
    for (const [k, v] of Object.entries(edits)) {
      const [group, key] = k.split(":");
      if (group === "fact") body.facts[key] = v;
      else if (group === "answer") body.answers[key] = v;
      else body.drafts[key] = v;
    }
    return run(
      "save",
      async () => {
        const next = await api<AssistantState>(`${base}/kit`, { method: "PUT", body: JSON.stringify(body) });
        setEdits({});
        setReviewed(false);
        return next;
      },
      "Saved to this application.",
    );
  }

  function valueOf(group: string, key: string, stored: string) {
    const k = `${group}:${key}`;
    return k in edits ? edits[k] : stored;
  }
  function edit(group: string, key: string, value: string) {
    setEdits((e) => ({ ...e, [`${group}:${key}`]: value }));
  }

  if (loadError) {
    return (
      <div className="aa-overlay" role="dialog" aria-modal="true" aria-label="Application Assistant">
        <div className="aa-shell">
          <div className="aa-banner aa-bad" role="alert">
            <CircleAlert size={16} /> {loadError}
          </div>
          <button type="button" className="secondary-button" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    );
  }

  const rows = s?.kit.rows ?? [];
  const missing = rows.filter((r) => r.source === "missing");
  const latestRun = s?.runs[s.runs.length - 1];
  const platform = latestRun
    ? platformFor(latestRun.host)
    : supportedPlatformForUrl(applyUrl) ?? { label: "Greenhouse, Lever, or Glassdoor", icon: Globe2 };
  const applyUrlSupported = !!supportedPlatformForUrl(applyUrl);
  const reviewBlocked = s?.status === "submitted" || ["interview", "offer", "rejected", "withdrawn"].includes(s?.status ?? "");

  return (
    <div className="aa-overlay" role="dialog" aria-modal="true" aria-label="Application Assistant">
      <div className="aa-shell">
        <header className="aa-head">
          <div>
            <p className="aa-eyebrow">Application Assistant</p>
            <h2>{s ? `${s.job.title} at ${s.job.company}` : "Loading..."}</h2>
          </div>
          {s && <span className={`status status-${s.status}`}>{STATUS_TEXT[s.status] ?? s.status}</span>}
          <button ref={closeRef} type="button" className="ghost-button aa-close" onClick={onClose} aria-label="Close Application Assistant">
            <X size={18} />
          </button>
        </header>

        {s && (
          <nav className="aa-steps" aria-label="Application steps">
            {s.steps.order.map((k, i) => (
              <button
                type="button"
                key={k}
                className={`aa-step ${step === k ? "is-current" : ""} ${s.steps.done[k] ? "is-done" : ""}`}
                aria-current={step === k ? "step" : undefined}
                onClick={() => goto(k)}
              >
                <span className="aa-step-dot">{s.steps.done[k] ? <Check size={12} /> : i + 1}</span>
                {STEP_LABELS[k]}
              </button>
            ))}
          </nav>
        )}

        <div aria-live="polite">
          {error && (
            <div className="aa-banner aa-bad" role="alert">
              <CircleAlert size={16} /> {error}
            </div>
          )}
          {notice && (
            <div className="aa-banner aa-good" role="status">
              <CheckCircle2 size={16} /> {notice}
            </div>
          )}
        </div>

        {!s && <p className="aa-muted">Opening your application workspace...</p>}

        {/* ---------------- JOB ---------------- */}
        {s && step === "job" && (
          <section className="aa-card" aria-label="Job">
            <div className="aa-row">
              <div>
                <h3>{s.job.title}</h3>
                <p className="aa-muted">
                  {s.job.company}
                  {s.job.location ? ` · ${s.job.location}` : ""}
                </p>
              </div>
              <span className={`match ${s.job.match_score >= 70 ? "high" : s.job.match_score >= 40 ? "mid" : "low"}`}>
                {Math.round(s.job.match_score)}% match{s.job.match_confidence ? ` · ${s.job.match_confidence} confidence` : ""}
              </span>
            </div>
            {s.job.match_reasons.length > 0 ? (
              <ul className="aa-list">
                {s.job.match_reasons.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            ) : (
              <p className="aa-muted">No match explanation is stored for this job yet.</p>
            )}

            <div className="aa-links">
              {s.job.url ? (
                <a href={s.job.url} target="_blank" rel="noreferrer" className="secondary-button">
                  Open saved job posting <ArrowUpRight size={14} />
                </a>
              ) : (
                <span className="aa-muted">This job has no saved posting link.</span>
              )}
            </div>

            <label className="aa-field">
              <span>Employer application page (optional)</span>
              <input
                type="url"
                value={applyUrl}
                placeholder="Greenhouse, Lever, or Glassdoor job URL"
                onChange={(e) => setApplyUrl(e.target.value)}
                onBlur={() => {
                  const value = applyUrl.trim();
                  if (value !== s.application_url && (!value || supportedPlatformForUrl(value))) {
                    void run("url", () =>
                    api<AssistantState>(`${base}/kit`, { method: "PUT", body: JSON.stringify({ application_url: applyUrl.trim() }) }),
                    );
                  }
                }}
              />
            </label>
            {s.application_url && supportedPlatformForUrl(s.application_url) && (
              <a href={s.application_url} target="_blank" rel="noreferrer" className="aa-inline-link">
                Open employer application page <ArrowUpRight size={13} />
              </a>
            )}
            {s.job.url && !supportedPlatformForUrl(s.job.url) && !supportedPlatformForUrl(s.application_url) && (
              <p className="aa-hint">
                No supported application URL was included with this {sourceLabel(s.job.source).toLowerCase()}.
                Open the listing and follow its Apply link. Greenhouse, Lever, and Glassdoor are supported; use the Application Kit manually on other sites.
              </p>
            )}

            {s.available_resumes.length > 0 ? (
              <label className="aa-field">
                <span>Resume for this application</span>
                <select
                  value={s.resume?.id ?? ""}
                  disabled={!!busy}
                  onChange={(e) => void selectResume(e.target.value)}
                >
                  {s.resume === null && <option value="">No resume selected</option>}
                  {s.available_resumes.map((resume) => (
                    <option key={resume.id} value={resume.id}>
                      {resume.label || resume.filename}{resume.is_active ? " (active)" : ""}
                    </option>
                  ))}
                </select>
                <span className="aa-hint">CareerPilot will only attach this file if you explicitly approve the upload in the Fill step.</span>
              </label>
            ) : (
              <div className="aa-banner aa-warn">
                <CircleAlert size={16} /> No resume is selected. Upload and activate one in your profile before you apply.
              </div>
            )}

            {s.linkedin_job && (
              <div className="aa-banner aa-warn">
                <Lock size={16} /> LinkedIn jobs are never filled automatically. Use the kit and the manual checklist in the Fill step.
              </div>
            )}
            <div className="aa-actions">
              <button type="button" className="primary-button" onClick={() => goto("kit")}>
                Continue to kit
              </button>
            </div>
          </section>
        )}

        {/* ---------------- KIT ---------------- */}
        {s && step === "kit" && (
          <section aria-label="Application kit">
            <div className="aa-card">
              <div className="aa-row">
                <div>
                  <h3>Your details</h3>
                  <p className="aa-muted">Copied from your confirmed profile. Edits here apply to this application only.</p>
                </div>
                <button type="button" className="primary-button" disabled={!dirty || !!busy} onClick={() => void saveKit()}>
                  {busy === "save" ? "Saving..." : dirty ? "Save changes" : "Saved"}
                </button>
              </div>
              {rows
                .filter((r) => r.group === "fact")
                .map((r) => {
                  const v = valueOf("fact", r.key, r.value);
                  return (
                    <div className="aa-kit-row" key={r.key}>
                      <label htmlFor={`f-${r.key}`}>{r.label}</label>
                      <input id={`f-${r.key}`} value={v} placeholder="Needs your input" onChange={(e) => edit("fact", r.key, e.target.value)} />
                      <SourceChip source={`fact:${r.key}` in edits ? "user_edited" : r.source} />
                      <CopyButton text={v} label={r.label} />
                    </div>
                  );
                })}
            </div>

            <div className="aa-card">
              <h3>Questions only you can answer</h3>
              <p className="aa-muted">
                CareerPilot never guesses these. Answer the ones you are comfortable with; they are reused when filling this application.
              </p>
              {rows
                .filter((r) => r.group === "answer")
                .map((r) => {
                  const v = valueOf("answer", r.key, r.value);
                  return (
                    <div className="aa-kit-row" key={r.key}>
                      <label htmlFor={`a-${r.key}`}>{r.label}</label>
                      <input id={`a-${r.key}`} value={v} placeholder={`Needs your input (${ANSWER_HELP[r.key] ?? ""})`} onChange={(e) => edit("answer", r.key, e.target.value)} />
                      <SourceChip source={v.trim() ? "user" : "missing"} />
                      <CopyButton text={v} label={r.label} />
                    </div>
                  );
                })}
              <p className="aa-muted">Demographic questions (gender, ethnicity, disability, veteran status) are always left for you on the form.</p>
            </div>

            <div className="aa-card">
              <div className="aa-row">
                <div>
                  <h3>Draft answers for this job</h3>
                  <p className="aa-muted">Written only from your profile and this posting. Check every sentence is true before you use it.</p>
                </div>
                <button type="button" className="secondary-button" disabled={!!busy} onClick={() => void run("gen", () => api<AssistantState>(`${base}/drafts/generate`, { method: "POST", body: "{}" }))}>
                  <Sparkles size={14} /> {busy === "gen" ? "Generating..." : "Generate drafts"}
                </button>
              </div>
              {s.generation && (
                <div className={`aa-banner ${s.generation.used === "ai" ? "aa-good" : "aa-warn"}`} role="status">
                  {s.generation.used === "ai" ? "Drafts written by the AI service from your profile." : s.generation.fallback_reason}
                  {s.generation.kept_edited.length > 0 && " Drafts you edited were kept."}
                </div>
              )}
              {Object.values(s.kit.drafts).map((d) => {
                const v = valueOf("draft", d.key, d.text);
                return (
                  <div className="aa-draft" key={d.key}>
                    <div className="aa-row">
                      <label htmlFor={`d-${d.key}`}>{d.label}</label>
                      <SourceChip source={`draft:${d.key}` in edits ? "user_edited" : d.source} />
                    </div>
                    <textarea id={`d-${d.key}`} rows={4} value={v} placeholder="Needs your input" onChange={(e) => edit("draft", d.key, e.target.value)} />
                    {!v.trim() && d.reason && <p className="aa-hint">{d.reason}</p>}
                    <div className="aa-actions">
                      <CopyButton text={v} label={d.label} />
                      {d.can_restore && !(`draft:${d.key}` in edits) && (
                        <button type="button" className="ghost-button" disabled={!!busy} onClick={() => void run("restore", () => api<AssistantState>(`${base}/drafts/${d.key}/restore`, { method: "POST" }), "Restored the generated version.")}>
                          <RotateCcw size={14} /> Restore original
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="aa-card">
              <div className="aa-row">
                <div>
                  <h3>Tailored application materials</h3>
                  <p className="aa-muted">Generate editable, profile-grounded drafts for this job. Check every claim before using them.</p>
                </div>
                <button
                  type="button"
                  className="secondary-button"
                  disabled={!!busy}
                  onClick={() => void run(
                    "materials-generate",
                    () => api<AssistantState>(`${base}/materials/generate`, { method: "POST" }),
                    "Tailored materials generated. Review and edit them before use.",
                  )}
                >
                  <Sparkles size={14} /> {busy === "materials-generate" ? "Generating..." : "Generate materials"}
                </button>
              </div>
              {materials ? (
                <>
                  <label className="aa-field">
                    <span>Cover letter</span>
                    <textarea
                      rows={8}
                      value={materialValue("cover_letter", materials.cover_letter)}
                      onChange={(e) => setMaterialEdits((editsNow) => ({ ...editsNow, cover_letter: e.target.value }))}
                    />
                  </label>
                  <div className="aa-actions">
                    <CopyButton text={materialValue("cover_letter", materials.cover_letter)} label="cover letter" />
                    <button
                      type="button"
                      className="secondary-button"
                      disabled={materialsDirty || !!busy}
                      onClick={() => onExportMaterials(s.job.id, materials, "cover_letter")}
                    >
                      Download cover-letter PDF
                    </button>
                  </div>
                  <label className="aa-field">
                    <span>Tailored resume summary</span>
                    <textarea
                      rows={4}
                      value={materialValue("resume_summary", materials.resume_summary)}
                      onChange={(e) => setMaterialEdits((editsNow) => ({ ...editsNow, resume_summary: e.target.value }))}
                    />
                  </label>
                  <div>
                    <strong>Skills to prioritize</strong>
                    <p className="aa-muted">{materials.skills_to_prioritize.join(", ") || "No confirmed matching skills were suggested."}</p>
                  </div>
                  {materials.bullet_suggestions.map((bullet, index) => (
                    <label className="aa-field" key={`${bullet.section}-${index}`}>
                      <span>Suggested rewrite · {bullet.section}</span>
                      <span className="aa-hint">Based on: {bullet.source_text}</span>
                      <textarea
                        rows={3}
                        value={materialValue(`bullet:${index}`, bullet.tailored_text)}
                        onChange={(e) => setMaterialEdits((editsNow) => ({ ...editsNow, [`bullet:${index}`]: e.target.value }))}
                      />
                    </label>
                  ))}
                  {materials.caveats.length > 0 && (
                    <div className="aa-banner aa-warn">
                      Please verify: {materials.caveats.join(" · ")}
                    </div>
                  )}
                  <button
                    type="button"
                    className="primary-button"
                    disabled={!materialsDirty || !!busy}
                    onClick={() => void saveMaterials()}
                  >
                    {busy === "materials" ? "Saving..." : materialsDirty ? "Save material edits" : "Materials saved"}
                  </button>
                  <button
                    type="button"
                    className="secondary-button"
                    disabled={materialsDirty || !!busy}
                    onClick={() => onExportMaterials(s.job.id, materials, "resume")}
                  >
                    Download tailored resume (HTML)
                  </button>
                  <p className="aa-hint">To attach this tailored version, print the downloaded HTML to PDF, upload that PDF under My Resumes, then select it for this application.</p>
                </>
              ) : (
                <p className="aa-muted">No tailored materials yet. Generating them requires a saved profile and configured AI service.</p>
              )}
            </div>
            <div className="aa-actions">
              <button type="button" className="secondary-button" onClick={() => goto("job")}>Back</button>
              <button type="button" className="primary-button" disabled={dirty || materialsDirty} onClick={() => goto("fill")}>
                {dirty || materialsDirty ? "Save changes to continue" : "Continue to fill"}
              </button>
            </div>
          </section>
        )}

        {/* ---------------- FILL ---------------- */}
        {s && step === "fill" && (
          <section className="aa-card" aria-label="Fill">
            {s.linkedin_job ? (
              <>
                <h3>Fill this LinkedIn application by hand</h3>
                <div className="aa-banner aa-warn">
                  <Lock size={16} /> CareerPilot does not automate LinkedIn. Your kit is ready to copy from.
                </div>
                <ol className="aa-list aa-checklist">
                  {s.manual_checklist.map((c, i) => (
                    <li key={i}>{c}</li>
                  ))}
                </ol>
              </>
            ) : (
              <>
                <h3>Prepare the employer application</h3>
                <p className="aa-muted">
                  Greenhouse and Lever forms, and Glassdoor job listings, can start autofill automatically. For Glassdoor, sign in yourself in its dedicated local browser and open the application form; only matching fields are filled. You review every field and submit on the employer's site yourself.
                </p>
                {busy === "auto-fill" && (
                  <div className="aa-banner aa-run-running" role="status">
                    {latestRun?.adapter === "glassdoor"
                      ? "Glassdoor is open in its dedicated local browser. Sign in yourself, then open the job's application form; CareerPilot will fill matching fields and stop for your review."
                      : "Opening the verified employer application and filling confirmed fields…"}
                  </div>
                )}
                <div className="aa-platform"><platform.icon size={16} /> {platform.label}</div>
                <label className="aa-field">
                  <span>Employer application URL</span>
                  <input type="url" value={applyUrl} placeholder="Paste a Greenhouse, Lever, or Glassdoor job URL" onChange={(e) => setApplyUrl(e.target.value)} />
                </label>
                {applyUrl && !applyUrlSupported && (
                  <div className="aa-banner aa-warn">
                    This is not a supported Greenhouse, Lever, or Glassdoor URL. Open the saved listing below, follow its employer Apply link, then paste the application URL here. You can still use the Application Kit to fill other sites manually.
                  </div>
                )}
                {s.job.url && (
                  <p className="aa-hint">
                    Source listing: <a className="aa-inline-link" href={s.job.url} target="_blank" rel="noreferrer">{sourceLabel(s.job.source)} <ArrowUpRight size={12} /></a>
                  </p>
                )}
                {s.resume && applyUrlSupported && (
                  <label className="aa-check">
                    <input type="checkbox" checked={uploadResume} onChange={(e) => setUploadResume(e.target.checked)} />
                    Attach {s.resume.label || s.resume.filename} to this application page. I understand this sends the selected PDF to the employer's website.
                  </label>
                )}
                <div className="aa-actions">
                  {s.job.url && (
                    <a
                      href={s.job.url}
                      target="_blank"
                      rel="noreferrer"
                      className="secondary-button"
                    >
                      Open job listing <ArrowUpRight size={14} />
                    </a>
                  )}
                  <button
                    type="button"
                    className="primary-button"
                    disabled={!applyUrlSupported || !!busy || !!running || reviewBlocked}
                    onClick={() => void run("fill", async () => {
                      await api(`${base}/fill`, {
                        method: "POST",
                        body: JSON.stringify({ url: applyUrl.trim(), upload_resume: uploadResume }),
                      });
                      setUploadResume(false);
                      return api<AssistantState>(base);
                    })}
                  >
                    {running ? "Opening and filling..." : `Open & fill on ${platform.label}`}
                  </button>
                </div>
              </>
            )}

            {latestRun && !s.linkedin_job && (
              <div className="aa-run" aria-live="polite">
                <div className="aa-row">
                  <strong>Latest run · {latestRun.host}</strong>
                  <span className={`aa-chip aa-run-${latestRun.status}`}>
                    {{ running: "Running", prepared: "Prepared", needs_you: "Needs you", failed: "Failed" }[latestRun.status]}
                  </span>
                </div>
                {latestRun.status === "running" && (
                  <p className="aa-muted">
                    {latestRun.adapter === "glassdoor"
                      ? "The local Glassdoor browser is waiting for you to sign in and open the application form. Your session stays in that local profile; CareerPilot does not collect credentials or click Submit."
                      : "Filling in the browser window... this updates automatically."}
                  </p>
                )}
                {latestRun.pause_reason && (
                  <div className="aa-banner aa-warn">
                    {PAUSE_TEXT[latestRun.pause_reason] ?? latestRun.pause_reason}
                    {latestRun.error_category && <span> (Diagnostic: {latestRun.error_category.replace(/_/g, " ")})</span>}
                  </div>
                )}
                {latestRun.status === "prepared" && (
                  <div className="aa-banner aa-good">Matched fields are filled. Nothing has been submitted; inspect the open employer form and review every value in CareerPilot.</div>
                )}
                {latestRun.browser_open && !latestRun.submission_enabled && (
                  <div className="aa-banner aa-warn">Form writes are blocked. They remain blocked until you confirm your review and explicitly enable manual submission.</div>
                )}
                {latestRun.submission_enabled && (
                  <div className="aa-banner aa-warn">Manual form requests are enabled. CareerPilot will not click Submit; use the employer's button yourself only after checking the form.</div>
                )}
                {latestRun.blocked_writes > 0 && (
                  <div className="aa-banner aa-warn">
                    CareerPilot blocked {latestRun.blocked_writes} write request{latestRun.blocked_writes === 1 ? "" : "s"}; no form submission was sent.
                  </div>
                )}
                {latestRun.fields.length > 0 && (
                  <table className="aa-table">
                    <thead>
                      <tr><th>Field</th><th>Result</th><th>Why</th></tr>
                    </thead>
                    <tbody>
                      {[...latestRun.fields]
                        .sort((a, b) => FIELD_ORDER[a.state] - FIELD_ORDER[b.state])
                        .map((f, i) => (
                          <tr key={i}>
                            <td>{f.label}{f.required ? " *" : ""}</td>
                            <td><span className={`aa-chip aa-field-${f.state}`}>{{ filled: "Filled", skipped: "Skipped", needs_input: "Needs your input", failed: "Failed" }[f.state]}</span></td>
                            <td className="aa-muted">{f.reason || "Matched to your kit"}</td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}
            <div className="aa-actions">
              <button type="button" className="secondary-button" onClick={() => goto("kit")}>Back</button>
              <button type="button" className="primary-button" onClick={() => goto("review")}>Continue to review</button>
            </div>
          </section>
        )}

        {/* ---------------- REVIEW ---------------- */}
        {s && step === "review" && (
          <section className="aa-card" aria-label="Review">
            <h3>Review exactly what you will submit</h3>
            <p className="aa-muted">
              {s.job.title} at {s.job.company}
              {s.job.url && (<> · <a className="aa-inline-link" href={s.job.url} target="_blank" rel="noreferrer">posting</a></>)}
              {" · "}Resume: {s.resume ? s.resume.label || s.resume.filename : "none selected"}
            </p>
            {missing.length > 0 && (
              <div className="aa-banner aa-warn"><CircleAlert size={16} /> {missing.length} item(s) still say "Needs your input". That is fine if the form does not ask for them.</div>
            )}
            <table className="aa-table">
              <thead><tr><th>Answer</th><th>Value</th><th>Source</th><th>Check</th></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.key}>
                    <td>{r.label}</td>
                    <td className="aa-value">{r.value || <em className="aa-muted">Needs your input</em>}</td>
                    <td><SourceChip source={r.source} /></td>
                    <td className="aa-muted">{r.verification}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {materials && (
              <details className="aa-material-review" open>
                <summary>Tailored materials included in this review</summary>
                <h4>Cover letter</h4>
                <p className="aa-value">{materials.cover_letter}</p>
                <h4>Resume summary</h4>
                <p className="aa-value">{materials.resume_summary}</p>
                {materials.bullet_suggestions.map((bullet, index) => (
                  <div key={`${bullet.section}-${index}`}>
                    <h4>Resume suggestion · {bullet.section}</h4>
                    <p className="aa-muted">Source: {bullet.source_text}</p>
                    <p className="aa-value">{bullet.tailored_text}</p>
                  </div>
                ))}
                {materials.caveats.length > 0 && <p className="aa-hint">Verify: {materials.caveats.join(" · ")}</p>}
              </details>
            )}

            {s.review.stale && (
              <div className="aa-banner aa-warn">Your answers changed after you last confirmed them. Review and confirm again.</div>
            )}
            {s.review.confirmed ? (
              <div className="aa-banner aa-good" role="status">
                <ShieldCheck size={16} /> You confirmed these exact answers{s.review.confirmed_at ? ` on ${new Date(s.review.confirmed_at).toLocaleString()}` : ""}. Nothing has been submitted.
              </div>
            ) : (
              <>
                <label className="aa-check">
                  <input type="checkbox" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} />
                  I have read every answer above and they are accurate. I understand CareerPilot will not submit anything for me.
                </label>
                <div className="aa-actions">
                  <button
                    type="button"
                    className="primary-button"
                    disabled={!reviewed || !!busy || reviewBlocked}
                    onClick={() => void run("review", () => api<AssistantState>(`${base}/review`, { method: "POST", body: JSON.stringify({ hash: s.review.hash, acknowledged: true }) }), "Review confirmed.")}
                  >
                    {busy === "review" ? "Confirming..." : "Confirm my review"}
                  </button>
                </div>
              </>
            )}
            <div className="aa-actions">
              <button type="button" className="secondary-button" onClick={() => goto("fill")}>Back</button>
              <button type="button" className="primary-button" disabled={!s.review.confirmed} onClick={() => goto("done")}>Continue</button>
            </div>
          </section>
        )}

        {/* ---------------- DONE ---------------- */}
        {s && step === "done" && (
          <section className="aa-card" aria-label="Done">
            {s.status === "submitted" ? (
              <>
                <h3>Recorded as submitted</h3>
                <div className="aa-banner aa-good" role="status">
                  <CheckCircle2 size={16} /> You told us you submitted this application{s.submitted_at ? ` on ${new Date(s.submitted_at).toLocaleString()}` : ""}.
                </div>
                <p className="aa-muted">CareerPilot did not submit it and has no confirmation from the employer's site. Keep any confirmation email they send you.</p>
              </>
            ) : s.review.confirmed ? (
              <>
                <h3>Submit it yourself, then tell us</h3>
                <ol className="aa-list aa-checklist">
                  <li>{latestRun?.browser_open ? "Inspect the prepared form in the open CareerPilot browser and check every field once more." : "Open the employer's application page and fill or check every field yourself."}</li>
                  <li>Only if you want to submit from CareerPilot's browser, explicitly enable the site's form requests below. Then click the site's own Submit button yourself.</li>
                  <li>Come back here and record it.</li>
                </ol>
                {latestRun?.adapter !== "local_test" && latestRun?.browser_open && !latestRun.submission_enabled && (
                  <div className="aa-card">
                    <label className="aa-check">
                      <input type="checkbox" checked={authorizeManualWrite} onChange={(e) => setAuthorizeManualWrite(e.target.checked)} />
                      I reviewed the prepared form. Allow this employer page to send form requests so I can click Submit myself. CareerPilot will not click it, but the site may react to enabling requests.
                    </label>
                    <button
                      type="button"
                      className="primary-button"
                      disabled={!authorizeManualWrite || !!busy}
                      onClick={() => void run(
                        "authorize-submit",
                        () => api<AssistantState>(`${base}/runs/${latestRun.id}/authorize-submit`, { method: "POST" }),
                        "Form requests enabled. Review the browser again, then click Submit yourself if you are ready.",
                      )}
                    >
                      {busy === "authorize-submit" ? "Enabling..." : "Enable manual form requests"}
                    </button>
                  </div>
                )}
                {latestRun?.submission_enabled && (
                  <div className="aa-banner aa-warn">Form requests are enabled. CareerPilot did not submit the form. Click Submit yourself only after your final check.</div>
                )}
                {s.application_url && supportedPlatformForUrl(s.application_url) && (
                  <a href={s.application_url} target="_blank" rel="noreferrer" className="secondary-button aa-inline-btn">Open employer application page <ArrowUpRight size={14} /></a>
                )}
                <label className="aa-check">
                  <input type="checkbox" checked={selfSubmitted} onChange={(e) => setSelfSubmitted(e.target.checked)} />
                  I submitted this application myself on the employer's site.
                </label>
                <div className="aa-actions">
                  <button type="button" className="primary-button" disabled={!selfSubmitted || !!busy} onClick={() => void run("submitted", () => api<AssistantState>(`${base}/submitted`, { method: "POST", body: JSON.stringify({ confirm: true }) }))}>
                    {busy === "submitted" ? "Recording..." : "I submitted it"}
                  </button>
                </div>
              </>
            ) : (
              <>
                <h3>Not ready yet</h3>
                <div className="aa-banner aa-warn"><CircleAlert size={16} /> Confirm your review before recording a submission.</div>
                <div className="aa-actions"><button type="button" className="primary-button" onClick={() => goto("review")}>Go to review</button></div>
              </>
            )}
            <details className="status-history aa-history">
              <summary>History ({s.status_history.length})</summary>
              <ol>
                {s.status_history.map((h, i) => (
                  <li key={i}>
                    <strong>{h.from_status ? `${STATUS_TEXT[h.from_status] ?? h.from_status} → ${STATUS_TEXT[h.to_status] ?? h.to_status}` : `Created as ${STATUS_TEXT[h.to_status] ?? h.to_status}`}</strong>
                    <span>{new Date(h.at).toLocaleString()} · {h.actor}</span>
                    {h.note && <em>{h.note}</em>}
                  </li>
                ))}
              </ol>
            </details>
            <div className="aa-actions"><button type="button" className="secondary-button" onClick={() => goto("review")}>Back</button></div>
          </section>
        )}
      </div>
    </div>
  );
}
