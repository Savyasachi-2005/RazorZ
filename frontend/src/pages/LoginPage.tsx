import { useState, type FormEvent } from "react";
import { Button } from "../components/ui/Button";
import { useAuth } from "../state/AuthContext";

const inputClass =
  "w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus-ring";

export function LoginPage() {
  const { signIn, signingIn, error, clearError } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!email.trim() || !password) return;
    try {
      await signIn(email, password);
    } catch {
      // The message is surfaced from auth state.
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-10 text-ink">
      <div className="w-full max-w-[400px]">
        <div className="mb-6 text-center">
          <p className="font-mono text-[11px] font-semibold tracking-[0.28em] text-accent-text">RAZORZ</p>
          <h1 className="mt-2 text-xl font-semibold tracking-tight text-ink">Finance Controller</h1>
          <p className="mt-2 text-xs leading-relaxed text-ink-muted">
            Sign in to review reconciliation breaks.
          </p>
        </div>

        <form
          onSubmit={submit}
          className="rounded-lg border border-line bg-surface/70 px-5 py-6 shadow-lg"
          noValidate
        >
          <label className="block text-xs font-medium text-ink-muted" htmlFor="login-email">
            Email
          </label>
          <input
            id="login-email"
            type="email"
            autoComplete="username"
            autoFocus
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
              if (error) clearError();
            }}
            placeholder="you@company.com"
            className={`mt-1.5 ${inputClass}`}
          />

          <label className="mt-4 block text-xs font-medium text-ink-muted" htmlFor="login-password">
            Password
          </label>
          <input
            id="login-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
              if (error) clearError();
            }}
            placeholder="••••••••••"
            className={`mt-1.5 ${inputClass}`}
          />

          {error ? (
            <p
              role="alert"
              className="mt-4 rounded-md border border-danger/40 bg-danger-soft px-3 py-2 text-xs text-danger-text"
            >
              {error}
            </p>
          ) : null}

          <Button
            type="submit"
            className="mt-5 w-full"
            loading={signingIn}
            disabled={!email.trim() || !password}
          >
            {signingIn ? "Signing in…" : "Sign in"}
          </Button>

          <p className="mt-4 text-[11px] leading-relaxed text-ink-faint">
            Sessions last 12 hours and end when this tab closes. Reconciliation decisions stay
            deterministic; signing in only grants review access.
          </p>
        </form>
      </div>
    </div>
  );
}
