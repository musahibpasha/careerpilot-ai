import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import {
  createUserWithEmailAndPassword,
  onAuthStateChanged,
  sendEmailVerification,
  sendPasswordResetEmail,
  signInWithEmailAndPassword,
  signInWithPopup,
  signOut,
  type User,
} from "firebase/auth";
import { GoogleAuthProvider, reload } from "firebase/auth";
import App from "./App";
import { firebaseAuth, firebaseConfigured, persistenceReady } from "./firebase";

type AuthMode = "sign-in" | "sign-up" | "reset";

function messageForAuthError(error: unknown): string {
  const code =
    typeof error === "object" && error !== null && "code" in error
      ? String(error.code)
      : "";
  const messages: Record<string, string> = {
    "auth/email-already-in-use": "An account already uses this email. Sign in instead.",
    "auth/invalid-email": "Enter a valid email address.",
    "auth/invalid-credential": "Email or password is incorrect.",
    "auth/weak-password": "Choose a stronger password (at least 6 characters).",
    "auth/popup-closed-by-user": "The Google sign-in window was closed before completion.",
    "auth/popup-blocked": "Allow pop-ups for this site and try Google sign-in again.",
    "auth/unauthorized-domain": "Add this site to Firebase Authentication's authorized domains.",
    "auth/too-many-requests": "Too many attempts. Wait a little and try again.",
    "auth/network-request-failed": "Network error. Check your connection and retry.",
    "auth/user-not-found": "No account was found for this email.",
  };
  return messages[code] ?? "Authentication failed. Please try again.";
}

export default function AuthBoundary({ children }: { children?: ReactNode }) {
  const [mode, setMode] = useState<AuthMode>("sign-in");
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const auth = firebaseAuth;
    if (!auth) {
      setLoading(false);
      return;
    }
    let unsubscribe: (() => void) | undefined;
    let active = true;
    persistenceReady
      .then(() => {
        if (!active) return;
        unsubscribe = onAuthStateChanged(auth, (currentUser) => {
          setUser(currentUser);
          setLoading(false);
        });
      })
      .catch((reason: unknown) => {
        setError(messageForAuthError(reason));
        setLoading(false);
      });
    return () => {
      active = false;
      unsubscribe?.();
    };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!firebaseAuth) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (mode === "reset") {
        await sendPasswordResetEmail(firebaseAuth, email.trim());
        setNotice("Password reset email sent. Check your inbox.");
      } else if (mode === "sign-up") {
        const credential = await createUserWithEmailAndPassword(
          firebaseAuth,
          email.trim(),
          password,
        );
        await sendEmailVerification(credential.user);
        setNotice("Verify your email using the link we sent, then return here.");
      } else {
        const credential = await signInWithEmailAndPassword(
          firebaseAuth,
          email.trim(),
          password,
        );
        if (!credential.user.emailVerified) {
          await sendEmailVerification(credential.user);
          setNotice("Your email is not verified yet. We sent another verification link.");
        }
      }
    } catch (reason) {
      const code =
        typeof reason === "object" && reason !== null && "code" in reason
          ? String(reason.code)
          : "";
      if (mode === "reset" && code === "auth/user-not-found") {
        setNotice("If an account exists for that email, a reset link has been sent.");
      } else {
        setError(messageForAuthError(reason));
      }
    } finally {
      setBusy(false);
    }
  }

  async function signInWithGoogle() {
    if (!firebaseAuth) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await signInWithPopup(firebaseAuth, new GoogleAuthProvider());
    } catch (reason) {
      setError(messageForAuthError(reason));
    } finally {
      setBusy(false);
    }
  }

  async function checkEmailVerification() {
    if (!firebaseAuth?.currentUser) return;
    setBusy(true);
    setError("");
    try {
      await reload(firebaseAuth.currentUser);
      setUser(firebaseAuth.currentUser);
      if (!firebaseAuth.currentUser.emailVerified) {
        setError("The email is not verified yet. Open the link in your inbox first.");
      }
    } catch (reason) {
      setError(messageForAuthError(reason));
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    if (!firebaseAuth) return;
    setBusy(true);
    setError("");
    try {
      await signOut(firebaseAuth);
      setMode("sign-in");
    } catch (reason) {
      setError(messageForAuthError(reason));
    } finally {
      setBusy(false);
    }
  }

  if (!firebaseConfigured) {
    return (
      <main className="auth-page">
        <section className="auth-card auth-card--setup">
          <h1>Firebase setup required</h1>
          <p>
            Add the Firebase web app values to the frontend environment. See
            `frontend/.env.example` and the setup instructions in README.
          </p>
        </section>
      </main>
    );
  }

  if (loading) {
    return <main className="auth-page"><p>Loading your account…</p></main>;
  }

  if (user && user.emailVerified) {
    return (
      <div className="authenticated-shell">
        <header className="auth-session-bar">
          <span>{user.email}</span>
          <button type="button" className="secondary-button" disabled={busy} onClick={logout}>
            Log out
          </button>
        </header>
        {children ?? <App />}
      </div>
    );
  }

  if (user && !user.emailVerified) {
    return (
      <main className="auth-page">
        <section className="auth-card">
          <div className="auth-card__brand">CareerPilot AI</div>
          <h1>Verify your email</h1>
          <p className="auth-card__subtitle">
            Open the verification link sent to {user.email}, then check again.
          </p>
          {error && <p className="auth-feedback auth-feedback--error" role="alert">{error}</p>}
          {notice && <p className="auth-feedback" role="status">{notice}</p>}
          <button type="button" disabled={busy} onClick={checkEmailVerification}>
            I verified my email
          </button>
          <button type="button" className="auth-link" disabled={busy} onClick={logout}>
            Sign out
          </button>
        </section>
      </main>
    );
  }

  return (
    <main className="auth-page">
      <section className="auth-layout">
        <aside className="auth-intro">
          <div className="auth-card__brand">CareerPilot AI</div>
          <h1>Your career, in your control.</h1>
          <p>Sign in to your private workspace for saved jobs, applications, and resumes.</p>
          <div className="auth-steps">
            <span>01 <b>Secure sign in</b></span>
            <span>02 <b>Private workspace</b></span>
            <span>03 <b>Prepare your next move</b></span>
          </div>
        </aside>
        <section className="auth-card">
          <h2>{mode === "sign-up" ? "Create your account" : mode === "reset" ? "Reset password" : "Welcome back"}</h2>
          <p className="auth-card__subtitle">
            {mode === "reset"
              ? "We’ll email you a secure password reset link."
              : mode === "sign-up"
                ? "Create an account to start your private workspace."
                : "Sign in to continue to CareerPilot."}
          </p>
          {mode !== "reset" && (
            <button type="button" className="auth-google" disabled={busy} onClick={signInWithGoogle}>
              Continue with Google
            </button>
          )}
          {mode !== "reset" && <div className="auth-divider"><span>or use email</span></div>}
          <form onSubmit={submit} className="auth-form">
            <label>
              Email address
              <input
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </label>
            {mode !== "reset" && (
              <label>
                Password
                <input
                  type="password"
                  autoComplete={mode === "sign-up" ? "new-password" : "current-password"}
                  minLength={6}
                  required
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
              </label>
            )}
            {error && <p className="auth-feedback auth-feedback--error" role="alert">{error}</p>}
            {notice && <p className="auth-feedback" role="status">{notice}</p>}
            <button type="submit" disabled={busy}>
              {busy ? "Please wait…" : mode === "sign-up" ? "Create account" : mode === "reset" ? "Send reset link" : "Sign in"}
            </button>
          </form>
          <nav className="auth-links" aria-label="Account options">
            {mode === "sign-in" && (
              <>
                <button type="button" className="auth-link" onClick={() => { setError(""); setNotice(""); setMode("reset"); }}>
                  Forgot password?
                </button>
                <button type="button" className="auth-link" onClick={() => { setError(""); setNotice(""); setMode("sign-up"); }}>
                  Create account
                </button>
              </>
            )}
            {mode !== "sign-in" && (
              <button type="button" className="auth-link" onClick={() => { setError(""); setNotice(""); setMode("sign-in"); }}>
                Back to sign in
              </button>
            )}
          </nav>
        </section>
      </section>
    </main>
  );
}
