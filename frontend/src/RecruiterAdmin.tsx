import { useEffect, useState, type FormEvent } from "react";
import { sendPasswordResetEmail } from "firebase/auth";
import { Mail, RefreshCw, UserPlus, UsersRound } from "lucide-react";
import { apiFetch } from "./api";
import { firebaseAuth } from "./firebase";

const API = import.meta.env.VITE_API_URL || (import.meta.env.DEV ? "http://127.0.0.1:8000" : "");

type Recruiter = {
  uid: string;
  display_name: string;
  email: string;
  created_at: string;
  created_by_email: string;
  workspace: {
    candidates: number;
    jobs: number;
    applications: number;
    resumes: number;
  };
};

function messageFrom(data: unknown, fallback: string): string {
  if (
    typeof data === "object" &&
    data !== null &&
    "detail" in data &&
    typeof data.detail === "string"
  ) {
    return data.detail;
  }
  return fallback;
}

export default function RecruiterAdmin() {
  const [recruiters, setRecruiters] = useState<Recruiter[]>([]);
  const [selected, setSelected] = useState<Recruiter | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [sendingUid, setSendingUid] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [listVersion, setListVersion] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    void apiFetch(`${API}/api/admin/recruiters`)
      .then(async (response) => {
        const data = await response.json().catch(() => null);
        if (!response.ok) {
          throw new Error(messageFrom(data, "Could not load recruiter accounts."));
        }
        if (active) {
          setRecruiters(data.items as Recruiter[]);
          setSelected((current) =>
            current ? (data.items as Recruiter[]).find((item) => item.uid === current.uid) ?? null : null,
          );
        }
      })
      .catch((reason: unknown) => {
        if (active) {
          setError(reason instanceof Error ? reason.message : "Could not load recruiter accounts.");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [listVersion]);

  async function sendSetupEmail(recruiter: Recruiter) {
    if (!firebaseAuth) {
      setError("Firebase authentication is not configured.");
      return false;
    }
    setSendingUid(recruiter.uid);
    setError("");
    try {
      await sendPasswordResetEmail(firebaseAuth, recruiter.email);
      setNotice(`Password setup email sent to ${recruiter.email}.`);
      return true;
    } catch (reason) {
      const code =
        typeof reason === "object" && reason !== null && "code" in reason
          ? String(reason.code)
          : "";
      setError(
        code === "auth/too-many-requests"
          ? "Too many email requests. Wait a little before trying again."
          : "The recruiter account exists, but Firebase could not send its password setup email. Try sending it again.",
      );
      return false;
    } finally {
      setSendingUid("");
    }
  }

  async function createRecruiter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const response = await apiFetch(`${API}/api/admin/recruiters`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          display_name: displayName.trim(),
          email: email.trim(),
        }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(messageFrom(data, "Could not create recruiter account."));
      }
      const recruiter = data as Recruiter;
      setSelected(recruiter);
      setDisplayName("");
      setEmail("");
      setListVersion((current) => current + 1);
      await sendSetupEmail(recruiter);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not create recruiter account.");
    } finally {
      setSaving(false);
    }
  }

  async function selectRecruiter(recruiter: Recruiter) {
    setSelected(recruiter);
    setError("");
    try {
      const response = await apiFetch(
        `${API}/api/admin/recruiters/${encodeURIComponent(recruiter.uid)}`,
      );
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(messageFrom(data, "Could not load recruiter workspace details."));
      }
      setSelected(data as Recruiter);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load recruiter workspace details.");
    }
  }

  return (
    <section className="recruiter-admin">
      <header className="recruiter-admin__header">
        <div>
          <span className="recruiter-admin__eyebrow">SUPER ADMIN</span>
          <h1>Recruiter workspace</h1>
          <p>Create recruiter accounts and view private workspace summaries.</p>
        </div>
        <UsersRound size={25} aria-hidden="true" />
      </header>

      {error && <p className="recruiter-admin__feedback recruiter-admin__feedback--error" role="alert">{error}</p>}
      {notice && <p className="recruiter-admin__feedback" role="status">{notice}</p>}

      <div className="recruiter-admin__grid">
        <section className="panel recruiter-admin__create">
          <h2><UserPlus size={18} /> Add recruiter</h2>
          <p>Recruiters get a private workspace and set their own password from an email link.</p>
          <form onSubmit={(event) => void createRecruiter(event)}>
            <label>
              Recruiter name
              <input
                required
                maxLength={120}
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                autoComplete="name"
              />
            </label>
            <label>
              Recruiter email
              <input
                required
                type="email"
                maxLength={254}
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                autoComplete="email"
              />
            </label>
            <button type="submit" className="primary-button" disabled={saving}>
              <UserPlus size={15} /> {saving ? "Creating…" : "Create account"}
            </button>
          </form>
        </section>

        <section className="panel recruiter-admin__list">
          <div className="recruiter-admin__section-heading">
            <h2><UsersRound size={18} /> Recruiters</h2>
            <button
              type="button"
              className="secondary-button recruiter-admin__refresh"
              aria-label="Refresh recruiter list"
              title="Refresh"
              onClick={() => setListVersion((current) => current + 1)}
              disabled={loading}
            >
              <RefreshCw size={15} />
            </button>
          </div>
          {loading ? (
            <p className="recruiter-admin__muted">Loading recruiter accounts…</p>
          ) : recruiters.length === 0 ? (
            <p className="recruiter-admin__muted">No recruiter accounts have been created yet.</p>
          ) : (
            <div className="recruiter-admin__items">
              {recruiters.map((recruiter) => (
                <button
                  type="button"
                  className={`recruiter-admin__item${selected?.uid === recruiter.uid ? " is-selected" : ""}`}
                  key={recruiter.uid}
                  onClick={() => void selectRecruiter(recruiter)}
                >
                  <span className="recruiter-admin__avatar">
                    {recruiter.display_name.slice(0, 1).toUpperCase()}
                  </span>
                  <span className="recruiter-admin__identity">
                    <strong>{recruiter.display_name}</strong>
                    <small>{recruiter.email}</small>
                  </span>
                  <span className="recruiter-admin__count">
                    {recruiter.workspace.candidates} candidates
                  </span>
                </button>
              ))}
            </div>
          )}
        </section>
      </div>

      {selected && (
        <section className="panel recruiter-admin__details" aria-label="Recruiter details">
          <div className="recruiter-admin__section-heading">
            <div>
              <h2>{selected.display_name}</h2>
              <p>{selected.email}</p>
            </div>
            <button
              type="button"
              className="secondary-button"
              onClick={() => void sendSetupEmail(selected)}
              disabled={sendingUid === selected.uid}
            >
              <Mail size={15} />
              {sendingUid === selected.uid ? "Sending…" : "Send password setup email"}
            </button>
          </div>
          <dl className="recruiter-admin__stats">
            <div><dt>Candidates</dt><dd>{selected.workspace.candidates}</dd></div>
            <div><dt>Jobs</dt><dd>{selected.workspace.jobs}</dd></div>
            <div><dt>Applications</dt><dd>{selected.workspace.applications}</dd></div>
            <div><dt>Resumes</dt><dd>{selected.workspace.resumes}</dd></div>
          </dl>
          <p className="recruiter-admin__muted">
            Created {new Date(selected.created_at).toLocaleString()} by {selected.created_by_email}.
            Workspace records remain private to this recruiter.
          </p>
        </section>
      )}
    </section>
  );
}
