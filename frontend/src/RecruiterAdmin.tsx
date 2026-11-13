
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { sendPasswordResetEmail } from "firebase/auth";
import {
  Mail,
  RefreshCw,
  UserPlus,
  UsersRound,
  Search,
  ShieldCheck,
  UserRound,
} from "lucide-react";
import { apiFetch } from "./api";
import { firebaseAuth } from "./firebase";

const API =
  import.meta.env.VITE_API_URL ||
  (import.meta.env.DEV ? "http://127.0.0.1:8000" : "");

type RegisteredAccount = {
  uid: string;
  email: string;
  display_name: string;
  email_verified: boolean;
  disabled: boolean;
  last_sign_in_at: string | null;
  created_at: string | null;
  recruiter_registered_by_admin: boolean;
  workspace: {
    candidates: number;
    jobs: number;
    applications: number;
    resumes: number;
  };
};

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

type DirectoryFilter = "all" | "admin" | "other";

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

function formatDate(value: string | null): string {
  if (!value) return "No recorded sign-in";

  const date = new Date(value);

  return Number.isNaN(date.getTime())
    ? "Sign-in date unavailable"
    : `Last sign-in ${date.toLocaleString()}`;
}

export default function RecruiterAdmin() {
  const [recruiters, setRecruiters] = useState<Recruiter[]>([]);
  const [accounts, setAccounts] = useState<RegisteredAccount[]>([]);
  const [accountsLoading, setAccountsLoading] = useState(true);
  const [accountsError, setAccountsError] = useState("");

  const [selected, setSelected] = useState<Recruiter | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [sendingUid, setSendingUid] = useState("");

  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [listVersion, setListVersion] = useState(0);
  const [filter, setFilter] = useState<DirectoryFilter>("all");
  const [search, setSearch] = useState("");

  useEffect(() => {
    let active = true;

    setLoading(true);

    void apiFetch(`${API}/api/admin/recruiters`)
      .then(async (response) => {
        const data = await response.json().catch(() => null);

        if (!response.ok) {
          throw new Error(
            messageFrom(data, "Could not load recruiter accounts.")
          );
        }

        if (active) {
          const items = data.items as Recruiter[];

          setRecruiters(items);

          setSelected((current) =>
            current
              ? items.find((item) => item.uid === current.uid) ?? null
              : null
          );
        }
      })
      .catch((reason: unknown) => {
        if (active) {
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not load recruiter accounts."
          );
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [listVersion]);

  useEffect(() => {
    let active = true;

    setAccountsLoading(true);
    setAccountsError("");

    void apiFetch(`${API}/api/admin/accounts`)
      .then(async (response) => {
        const data = await response.json().catch(() => null);

        if (!response.ok) {
          throw new Error(
            messageFrom(
              data,
              "Could not load registered sign-in accounts."
            )
          );
        }

        if (!data || !Array.isArray(data.items)) {
          throw new Error(
            "The account directory returned an invalid response."
          );
        }

        if (active) {
          setAccounts(data.items as RegisteredAccount[]);
        }
      })
      .catch((reason: unknown) => {
        if (active) {
          setAccountsError(
            reason instanceof Error
              ? reason.message
              : "Could not load registered sign-in accounts."
          );
        }
      })
      .finally(() => {
        if (active) setAccountsLoading(false);
      });

    return () => {
      active = false;
    };
  }, [listVersion]);

  const filteredAccounts = useMemo(() => {
    return accounts.filter((account) => {
      const matchesFilter =
        filter === "all" ||
        (filter === "admin"
          ? account.recruiter_registered_by_admin
          : !account.recruiter_registered_by_admin);

      const query = search.trim().toLowerCase();

      const matchesSearch =
        !query ||
        account.email.toLowerCase().includes(query) ||
        account.display_name.toLowerCase().includes(query);

      return matchesFilter && matchesSearch;
    });
  }, [accounts, filter, search]);

  const adminCount = accounts.filter(
    (account) => account.recruiter_registered_by_admin
  ).length;

  const otherCount = accounts.length - adminCount;

  async function sendSetupEmail(recruiter: Recruiter) {
    if (!firebaseAuth) {
      setError("Firebase authentication is not configured.");
      return false;
    }

    setSendingUid(recruiter.uid);
    setError("");

    try {
      await sendPasswordResetEmail(firebaseAuth, recruiter.email);

      setNotice(
        `Password setup email sent to ${recruiter.email}.`
      );

      return true;
    } catch (reason) {
      const code =
        typeof reason === "object" &&
        reason !== null &&
        "code" in reason
          ? String(reason.code)
          : "";

      setError(
        code === "auth/too-many-requests"
          ? "Too many email requests. Wait a little before trying again."
          : "The recruiter account exists, but Firebase could not send its password setup email. Try again."
      );

      return false;
    } finally {
      setSendingUid("");
    }
  }

  async function createRecruiter(
    event: FormEvent<HTMLFormElement>
  ) {
    event.preventDefault();

    setSaving(true);
    setError("");
    setNotice("");

    try {
      const response = await apiFetch(
        `${API}/api/admin/recruiters`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            display_name: displayName.trim(),
            email: email.trim(),
          }),
        }
      );

      const data = await response.json().catch(() => null);

      if (!response.ok) {
        throw new Error(
          messageFrom(data, "Could not create recruiter account.")
        );
      }

      const recruiter = data as Recruiter;

      setSelected(recruiter);
      setDisplayName("");
      setEmail("");

      setListVersion((current) => current + 1);

      await sendSetupEmail(recruiter);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not create recruiter account."
      );
    } finally {
      setSaving(false);
    }
  }

  async function selectRecruiter(recruiter: Recruiter) {
    setSelected(recruiter);
    setError("");

    try {
      const response = await apiFetch(
        `${API}/api/admin/recruiters/${encodeURIComponent(
          recruiter.uid
        )}`
      );

      const data = await response.json().catch(() => null);

      if (!response.ok) {
        throw new Error(
          messageFrom(
            data,
            "Could not load recruiter workspace details."
          )
        );
      }

      setSelected(data as Recruiter);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load recruiter workspace details."
      );
    }
  }

  async function openAccount(account: RegisteredAccount) {
    const recruiter = recruiters.find(
      (item) => item.uid === account.uid
    );

    if (recruiter) {
      await selectRecruiter(recruiter);
    } else {
      setSelected(null);

      setNotice(
        `${account.email} is a registered sign-in account, but has not been added as an approved recruiter.`
      );
    }
  }

  return (
    <section className="recruiter-admin">
      <header className="recruiter-admin__header">
        <div>
          <span className="recruiter-admin__eyebrow">
            SUPER ADMIN
          </span>

          <h1>Recruiter management</h1>

          <p>
            Manage approved recruiter workspaces and review every
            account that has signed in.
          </p>
        </div>

        <UsersRound size={25} aria-hidden="true" />
      </header>

      {error && (
        <p
          className="recruiter-admin__feedback recruiter-admin__feedback--error"
          role="alert"
        >
          {error}
        </p>
      )}

      {notice && (
        <p className="recruiter-admin__feedback" role="status">
          {notice}
        </p>
      )}

      <div className="recruiter-admin__grid">
        <section className="panel recruiter-admin__create">
          <div className="recruiter-admin__create-title">
            <UserPlus size={18} />
            <h2>Add recruiter</h2>
          </div>

          <p>
            Approved recruiters get a private workspace and set
            their password through an email link.
          </p>

          <form onSubmit={(event) => void createRecruiter(event)}>
            <label>
              Recruiter name

              <input
                required
                maxLength={120}
                value={displayName}
                onChange={(event) =>
                  setDisplayName(event.target.value)
                }
                autoComplete="name"
                placeholder="Enter full name"
              />
            </label>

            <label>
              Recruiter email

              <input
                required
                type="email"
                maxLength={254}
                value={email}
                onChange={(event) =>
                  setEmail(event.target.value)
                }
                autoComplete="email"
                placeholder="name@company.com"
              />
            </label>

            <button
              type="submit"
              className="primary-button"
              disabled={saving}
            >
              <UserPlus size={15} />
              {saving ? "Creating…" : "Create recruiter"}
            </button>
          </form>
        </section>

        <section
          className="panel recruiter-admin__directory"
          aria-labelledby="directory-heading"
        >
          <div className="recruiter-admin__section-heading">
            <div>
              <div className="recruiter-admin__create-title">
                <UsersRound size={18} />
                <h2 id="directory-heading">
                  Recruiter &amp; account directory
                </h2>
              </div>

              <p>
                All accounts are visible here. Only admin-added
                accounts are approved recruiters.
              </p>
            </div>

            <button
              type="button"
              className="secondary-button recruiter-admin__refresh"
              aria-label="Refresh directory"
              title="Refresh"
              onClick={() =>
                setListVersion((current) => current + 1)
              }
              disabled={loading || accountsLoading}
            >
              <RefreshCw size={15} />
            </button>
          </div>

          <div className="recruiter-admin__summary">
            <div>
              <strong>{accounts.length}</strong>
              <span>Total accounts</span>
            </div>

            <div>
              <strong>{adminCount}</strong>
              <span>Approved recruiters</span>
            </div>

            <div>
              <strong>{otherCount}</strong>
              <span>Other sign-ins</span>
            </div>
          </div>

          <div className="recruiter-admin__directory-tools">
            <div
              className="recruiter-admin__tabs"
              role="tablist"
              aria-label="Filter accounts"
            >
              <button
                type="button"
                role="tab"
                aria-selected={filter === "all"}
                className={filter === "all" ? "is-active" : ""}
                onClick={() => setFilter("all")}
              >
                All <span>{accounts.length}</span>
              </button>

              <button
                type="button"
                role="tab"
                aria-selected={filter === "admin"}
                className={filter === "admin" ? "is-active" : ""}
                onClick={() => setFilter("admin")}
              >
                Approved <span>{adminCount}</span>
              </button>

              <button
                type="button"
                role="tab"
                aria-selected={filter === "other"}
                className={filter === "other" ? "is-active" : ""}
                onClick={() => setFilter("other")}
              >
                Other sign-ins <span>{otherCount}</span>
              </button>
            </div>

            <label className="recruiter-admin__search">
              <Search size={15} />

              <input
                value={search}
                onChange={(event) =>
                  setSearch(event.target.value)
                }
                placeholder="Search name or email"
                aria-label="Search accounts"
              />
            </label>
          </div>

          {accountsLoading ? (
            <p className="recruiter-admin__muted">
              Loading account directory…
            </p>
          ) : accountsError ? (
            <p
              className="recruiter-admin__feedback recruiter-admin__feedback--error"
              role="alert"
            >
              {accountsError}
            </p>
          ) : filteredAccounts.length === 0 ? (
            <div className="recruiter-admin__empty">
              <UsersRound size={22} />
              <strong>No accounts found</strong>
              <span>Try another filter or search term.</span>
            </div>
          ) : (
            <div className="recruiter-admin__directory-list">
              {filteredAccounts.map((account) => {
                const approved =
                  account.recruiter_registered_by_admin;

                return (
                  <button
                    type="button"
                    className={`recruiter-admin__account${
                      selected?.uid === account.uid
                        ? " is-selected"
                        : ""
                    }`}
                    key={account.uid}
                    onClick={() => void openAccount(account)}
                  >
                    <span className="recruiter-admin__avatar">
                      {(
                        account.display_name ||
                        account.email ||
                        "?"
                      )
                        .slice(0, 1)
                        .toUpperCase()}
                    </span>

                    <span className="recruiter-admin__identity">
                      <strong>
                        {account.display_name || "Unnamed account"}
                      </strong>

                      <small>{account.email}</small>

                      <small>
                        {formatDate(account.last_sign_in_at)}
                      </small>
                    </span>

                    <span className="recruiter-admin__account-meta">
                      <span
                        className={`recruiter-admin__status ${
                          approved ? "is-approved" : "is-account"
                        }`}
                      >
                        {approved ? (
                          <ShieldCheck size={12} />
                        ) : (
                          <UserRound size={12} />
                        )}

                        {account.disabled
                          ? "Disabled"
                          : approved
                            ? "Approved recruiter"
                            : "Sign-in only"}
                      </span>

                      <small>
                        {account.workspace.candidates} candidates
                        {" · "}
                        {account.workspace.jobs} jobs
                      </small>
                    </span>
                  </button>
                );
              })}
            </div>
          )}

          <div className="recruiter-admin__directory-foot">
            Showing {filteredAccounts.length} of {accounts.length}
            {" "}registered accounts
          </div>
        </section>
      </div>

      {selected && (
        <section
          className="panel recruiter-admin__details"
          aria-label="Recruiter details"
        >
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

              {sendingUid === selected.uid
                ? "Sending…"
                : "Send password setup email"}
            </button>
          </div>

          <dl className="recruiter-admin__stats">
            <div>
              <dt>Candidates</dt>
              <dd>{selected.workspace.candidates}</dd>
            </div>

            <div>
              <dt>Jobs</dt>
              <dd>{selected.workspace.jobs}</dd>
            </div>

            <div>
              <dt>Applications</dt>
              <dd>{selected.workspace.applications}</dd>
            </div>

            <div>
              <dt>Resumes</dt>
              <dd>{selected.workspace.resumes}</dd>
            </div>
          </dl>

          <p className="recruiter-admin__muted">
            Created {new Date(selected.created_at).toLocaleString()}
            {" "}by {selected.created_by_email}. Workspace records
            remain private to this recruiter.
          </p>
        </section>
      )}
    </section>
  );
}
