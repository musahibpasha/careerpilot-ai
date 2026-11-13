import { useEffect, useState } from "react";
import { apiFetch as fetch } from "./api";
import type {
  Application,
  ApplicationMaterials as Materials,
  Job,
} from "./App";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CircleAlert,
  ExternalLink,
  FileText,
  LoaderCircle,
  Sparkles,
  X,
} from "lucide-react";

const API = import.meta.env.VITE_API_URL || (import.meta.env.DEV ? "http://127.0.0.1:8000" : "");
type Profile = {
  full_name: string;
  email: string;
  phone: string;
  location: string;
  github: string;
  linkedin: string;
  portfolio: string;
  experience_summary: string;
  skills: string[];
  work_experience: { title: string; company: string; duration: string; location: string; description: string }[];
  projects: { name: string; role: string; duration: string; technologies: string; description: string }[];
  education: { institution: string; degree: string; duration: string; grade: string }[];
  certifications: { name: string; issuer: string }[];
  achievements: string[];
};

type ApplyStatus =
  | "not_applied"
  | "applied"
  | "interview"
  | "assessment"
  | "rejected"
  | "offer_received"
  | "withdrawn";

const STATUSES: { value: ApplyStatus; label: string }[] = [
  { value: "not_applied", label: "Not Applied" },
  { value: "applied", label: "Applied" },
  { value: "interview", label: "Interview" },
  { value: "assessment", label: "Assessment" },
  { value: "rejected", label: "Rejected" },
  { value: "offer_received", label: "Offer Received" },
  { value: "withdrawn", label: "Withdrawn" },
];

const blankProfile: Profile = {
  full_name: "",
  email: "",
  phone: "",
  location: "",
  github: "",
  linkedin: "",
  portfolio: "",
  experience_summary: "",
  skills: [],
  work_experience: [],
  projects: [],
  education: [],
  certifications: [],
  achievements: [],
};

function applyStorageKey(applicationId: string) {
  return `careerpilot-apply-materials:${applicationId}`;
}

function readStoredMaterials(applicationId: string): Materials | null {
  try {
    const stored = window.localStorage.getItem(applyStorageKey(applicationId));
    return stored ? JSON.parse(stored) as Materials : null;
  } catch {
    return null;
  }
}

function saveBlob(blob: Blob, filename: string) {
  if (blob.type !== "application/pdf") throw new Error("The API returned an invalid PDF.");
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename.replace(/[<>:"/\\|?*\u0000-\u001F]/g, "-").slice(0, 150);
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function ApplyWorkflow({
  job,
  application,
  initialMaterials,
  materialsLoading,
  onGenerateMaterials,
  onMaterialsChange,
  onClose,
  onReturnDashboard,
  onApplicationUpdated,
  onSaved,
}: {
  job: Job;
  application: Application;
  initialMaterials: Materials | null;
  materialsLoading: boolean;
  onGenerateMaterials: (
    job: Job,
    target: "resume" | "cover_letter",
  ) => Promise<Materials | null>;
  onMaterialsChange: (materials: Materials) => void;
  onClose: () => void;
  onReturnDashboard: () => void;
  onApplicationUpdated: (application: Application) => void;
  onSaved: (application: Application) => void;
}) {
  const [step, setStep] = useState(0);
  const [profile, setProfile] = useState<Profile>(blankProfile);
  const [profileLoading, setProfileLoading] = useState(true);
  const [materials, setMaterials] = useState<Materials | null>(
    () => initialMaterials ?? application.apply_documents ?? null,
  );
  const [applyStatus, setApplyStatus] = useState<ApplyStatus>(
    application.apply_status ?? "not_applied",
  );
  const [notes, setNotes] = useState(application.notes ?? "");
  const [saving, setSaving] = useState(false);
  const [downloading, setDownloading] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const matching = job.match_breakdown?.find((factor) => factor.key === "skills")?.details;
  const matchedSkills = matching?.matched ?? [];
  const missingSkills = matching?.missing_must_have ?? [];
  const applicationUrl = job.application_url || job.url || "";

  useEffect(() => {
    let active = true;
    const profilePath = application.candidate_id
      ? `/api/candidates/${application.candidate_id}`
      : "/api/profile";
    fetch(`${API}${profilePath}`)
      .then(async (response) => {
        const data = await response.json().catch(() => null);
        if (!response.ok) throw new Error(data?.detail || "Could not load candidate profile.");
        if (active) setProfile({ ...blankProfile, ...data });
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "Could not load candidate profile.");
      })
      .finally(() => active && setProfileLoading(false));
    return () => { active = false; };
  }, [application.candidate_id]);

  useEffect(() => {
    let active = true;
    const stored = readStoredMaterials(application.id);
    if (initialMaterials) {
      setMaterials(initialMaterials);
    } else if (stored) {
      setMaterials(stored);
    } else if (application.apply_documents) {
      setMaterials(application.apply_documents);
    }
    return () => { active = false; };
  // The dialog is keyed by job id, so its initial artifacts are read once and
  // subsequent edits are written through onMaterialsChange.
  }, [job.id]);

  useEffect(() => {
    if (!materials) return;
    try {
      window.localStorage.setItem(applyStorageKey(application.id), JSON.stringify(materials));
    } catch {
      setError("Browser storage is unavailable. Save your application to preserve generated drafts.");
    }
  }, [application.id, materials]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function generate(target: "resume" | "cover_letter") {
    setError("");
    setNotice("");
    try {
      const generated = await onGenerateMaterials(job, target);
      if (!generated) return;
      const updated = materials
        ? target === "resume"
          ? {
              ...materials,
              resume_summary: generated.resume_summary,
              skills_to_prioritize: generated.skills_to_prioritize,
              bullet_suggestions: generated.bullet_suggestions,
              caveats: generated.caveats,
            }
          : { ...materials, cover_letter: generated.cover_letter }
        : generated;
      setMaterials(updated);
      onMaterialsChange(updated);
      setNotice(`${target === "resume" ? "Tailored resume" : "Cover letter"} draft generated. Review and edit before using.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not generate the requested draft.");
    }
  }

  function updateMaterials(updates: Partial<Materials>) {
    if (!materials) return;
    const updated = { ...materials, ...updates };
    setMaterials(updated);
    onMaterialsChange(updated);
  }

  async function downloadPdf(kind: "cover-letter" | "tailored-resume") {
    if (!materials) return;
    setDownloading(kind);
    setError("");
    try {
      const response = await fetch(`${API}/api/jobs/${job.id}/${kind}.pdf`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          kind === "cover-letter"
            ? { cover_letter: materials.cover_letter }
            : { materials },
        ),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new Error(data?.detail || "Could not download the PDF.");
      }
      const filename = `${job.title} - ${job.company} - ${kind}.pdf`;
      saveBlob(await response.blob(), filename);
      setNotice(`${kind === "cover-letter" ? "Cover letter" : "Tailored resume"} PDF downloaded.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not download the PDF.");
    } finally {
      setDownloading("");
    }
  }

  async function saveApplication() {
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch(`${API}/api/applications/${application.id}/apply`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          status: applyStatus,
          notes,
          apply_documents: materials,
        }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(data?.detail || "Could not save your application.");
      onApplicationUpdated(data as Application);
      setNotice("Application status, notes, and generated drafts saved.");
      onSaved(data as Application);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save your application.");
    } finally {
      setSaving(false);
    }
  }

  const progress = ["Resume & Documents", "Apply Manually"];

  return (
    <div className="apply-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="apply-dialog" role="dialog" aria-modal="true" aria-label={`Apply for ${job.title}`}>
        <header className="apply-header">
          <div className="apply-heading">
            <span className="apply-icon"><Sparkles size={18} /></span>
            <div>
              <p className="apply-eyebrow">ThoughtLabs CoPilot· Apply</p>
              <h2>{job.title} at {job.company}</h2>
            </div>
          </div>
          <button type="button" className="ghost-button" aria-label="Close Apply workflow" onClick={onClose}><X size={18} /></button>
        </header>

        <nav className="apply-progress" aria-label="Application workflow">
          {progress.map((label, index) => (
            <button type="button" key={label} className={`apply-progress-step ${index === step ? "current" : index < step ? "complete" : ""}`} onClick={() => setStep(index)}>
              <span>{index < step ? <Check size={13} /> : index + 1}</span>{label}
            </button>
          ))}
        </nav>

        <div className="apply-content" aria-live="polite">
          {error && <div className="apply-alert" role="alert"><CircleAlert size={16} />{error}</div>}
          {notice && <div className="apply-success" role="status"><Check size={16} />{notice}</div>}

          {step === 0 && (
            <div className="apply-step-content">
              <section className="apply-info-block">
                <div className="apply-section-heading"><h3>{application.candidate_id ? "Candidate profile and resume details" : "Profile and resume details"}</h3><span className="apply-match">{Math.round(job.match_score)}% match{job.match_confidence ? ` · ${job.match_confidence} confidence` : ""}</span></div>
                {profileLoading ? <p className="apply-muted">Loading saved profile…</p> : (
                  <>
                    <p><strong>{profile.full_name || "Name not provided"}</strong>{[profile.email, profile.phone, profile.location].filter(Boolean).length ? ` · ${[profile.email, profile.phone, profile.location].filter(Boolean).join(" · ")}` : ""}</p>
                    <p>{profile.linkedin || profile.github || profile.portfolio ? [profile.linkedin, profile.github, profile.portfolio].filter(Boolean).join(" · ") : ""}</p>
                    <h4>Professional summary</h4><p>{profile.experience_summary || "No professional summary in your profile."}</p>
                    <h4>Skills</h4><div className="apply-chips">{profile.skills.length ? profile.skills.map((skill) => <span key={skill}>{skill}</span>) : <span>None listed</span>}</div>
                    <h4>Matching skills</h4><div className="apply-chips">{matchedSkills.length ? matchedSkills.map((skill) => <span className="matched" key={skill}>{skill}</span>) : <span>No matching skills were identified.</span>}</div>
                    <h4>Missing required skills</h4><div className="apply-chips">{missingSkills.length ? missingSkills.map((skill) => <span className="missing" key={skill}>{skill}</span>) : <span>No missing skills reported.</span>}</div>
                    <h4>Work experience</h4>{profile.work_experience.length ? profile.work_experience.map((item, index) => <article className="apply-profile-entry" key={`${item.company}-${index}`}><strong>{item.title || "Role"}{item.company ? ` · ${item.company}` : ""}</strong><small>{[item.duration, item.location].filter(Boolean).join(" · ")}</small><p>{item.description || "No description saved."}</p></article>) : <p className="apply-muted">No work experience listed.</p>}
                    <h4>Projects</h4>{profile.projects.length ? profile.projects.map((item, index) => <article className="apply-profile-entry" key={`${item.name}-${index}`}><strong>{item.name || "Project"}{item.role ? ` · ${item.role}` : ""}</strong><small>{[item.duration, item.technologies].filter(Boolean).join(" · ")}</small><p>{item.description || "No description saved."}</p></article>) : <p className="apply-muted">No projects listed.</p>}
                    <h4>Education</h4>{profile.education.length ? profile.education.map((item, index) => <p key={`${item.institution}-${index}`}>{[item.degree, item.institution, item.duration, item.grade].filter(Boolean).join(" · ")}</p>) : <p className="apply-muted">No education listed.</p>}
                    <h4>Certifications</h4><p>{profile.certifications.length ? profile.certifications.map((item) => [item.name, item.issuer].filter(Boolean).join(" · ")).join("; ") : "No certifications listed."}</p>
                  </>
                )}
              </section>

              <section className="apply-info-block apply-document-section">
                <div className="apply-section-heading"><h3>AI tailored resume</h3>{materials && <button type="button" className="secondary-button" disabled={downloading !== ""} onClick={() => void downloadPdf("tailored-resume")}>{downloading === "tailored-resume" ? "Preparing PDF…" : <><FileText size={14} /> Download resume PDF</>}</button>}</div>
                {materials ? (
                  <>
                    <label className="apply-field">Tailored professional summary<textarea rows={4} value={materials.resume_summary} onChange={(event) => updateMaterials({ resume_summary: event.target.value })} /></label>
                    {materials.bullet_suggestions.map((bullet, index) => <label className="apply-field" key={`${bullet.section}-${index}`}>Tailored experience · {bullet.section}<small>Based on your profile: {bullet.source_text}</small><textarea rows={3} value={bullet.tailored_text} onChange={(event) => updateMaterials({ bullet_suggestions: materials.bullet_suggestions.map((item, itemIndex) => itemIndex === index ? { ...item, tailored_text: event.target.value } : item) })} /></label>)}
                    {materials.caveats.length > 0 && <p className="apply-muted">Review these details: {materials.caveats.join(" ")}</p>}
                  </>
                ) : <p className="apply-muted">Generate an editable resume draft using the same Match Analysis AI generation endpoint and prompts.</p>}
                <button type="button" className="secondary-button" disabled={materialsLoading || profileLoading} onClick={() => void generate("resume")}>{materialsLoading ? <><LoaderCircle size={14} className="apply-spin" /> Generating draft…</> : materials?.resume_summary ? "Regenerate tailored resume" : "Generate tailored resume"}</button>
              </section>

              <section className="apply-info-block apply-document-section">
                <div className="apply-section-heading"><h3>AI cover letter</h3>{materials && <button type="button" className="secondary-button" disabled={downloading !== ""} onClick={() => void downloadPdf("cover-letter")}>{downloading === "cover-letter" ? "Preparing PDF…" : <><FileText size={14} /> Download cover letter PDF</>}</button>}</div>
                {materials ? <label className="apply-field">Cover letter<textarea rows={10} value={materials.cover_letter} onChange={(event) => updateMaterials({ cover_letter: event.target.value })} /></label> : <p className="apply-muted">Generate an editable cover letter using the same Match Analysis AI generation endpoint and prompts.</p>}
                <button type="button" className="secondary-button" disabled={materialsLoading || profileLoading} onClick={() => void generate("cover_letter")}>{materialsLoading ? <><LoaderCircle size={14} className="apply-spin" /> Generating draft…</> : materials?.cover_letter ? "Regenerate cover letter" : "Generate cover letter"}</button>
              </section>
            </div>
          )}

          {step === 1 && (
            <div className="apply-step-content">
              <section className="apply-info-block">
                <h3>{job.title} · {job.company}</h3>
                <p className="apply-muted">Open the employer’s original application page in a separate tab. ThoughtLabs CoPilotstays open, and no external application is submitted automatically.</p>
                {applicationUrl ? <a className="primary-button apply-external-link" href={applicationUrl} target="_blank" rel="noopener noreferrer">Apply on Company Website <ExternalLink size={15} /></a> : <p className="apply-alert">No application URL is available for this job. Open the saved job posting to find the employer’s application page.</p>}
              </section>
              <section className="apply-info-block">
                <h3>Application status</h3>
                <label className="apply-field">Status<select value={applyStatus === "applied" ? "current-applied" : applyStatus} onChange={(event) => {
                  if (event.target.value !== "current-applied") setApplyStatus(event.target.value as ApplyStatus);
                }}>
                  {applyStatus === "applied" && <option value="current-applied" disabled>Applied (current)</option>}
                  {STATUSES.filter((status) => status.value !== "applied" || applyStatus !== "applied")
                    .map((status) => <option key={status.value} value={status.value}>{status.label}</option>)}
                </select></label>
                {application.applied_at && <p className="apply-muted">Applied on {new Date(application.applied_at).toLocaleDateString()}</p>}
                <label className="apply-field">Optional notes<textarea rows={5} maxLength={5000} value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="Add reminders or notes about this application…" /></label>
                {application.apply_updated_at && <p className="apply-muted">Last updated {new Date(application.apply_updated_at).toLocaleString()}</p>}
                <button type="button" className="primary-button" disabled={saving} onClick={() => void saveApplication()}>{saving ? "Saving…" : "Save status and notes"}</button>
              </section>
              {application.apply_status_history?.length > 0 && (
                <section className="apply-info-block">
                  <h3>Status history</h3>
                  <ol className="apply-history">{application.apply_status_history.map((entry, index) => <li key={`${entry.at}-${index}`}>{entry.from_status ? `${STATUSES.find((item) => item.value === entry.from_status)?.label ?? entry.from_status} → ` : ""}{STATUSES.find((item) => item.value === entry.to_status)?.label ?? entry.to_status}<small>{new Date(entry.at).toLocaleString()}</small></li>)}</ol>
                </section>
              )}
            </div>
          )}
        </div>

        <footer className="apply-footer">
          <button type="button" className="secondary-button" onClick={() => step === 0 ? onClose() : setStep((current) => Math.max(0, current - 1))}><ArrowLeft size={15} />{step === 0 ? "Close" : "Previous"}</button>
          <button type="button" className="link-button" onClick={onReturnDashboard}>Dashboard</button>
          {step < 1 && <button type="button" className="primary-button" onClick={() => setStep((current) => Math.min(1, current + 1))}>Next<ArrowRight size={15} /></button>}
        </footer>
      </section>
    </div>
  );
}
