import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Eye, EyeOff, LockKeyhole, ShieldCheck } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import brandArtwork from "../../../docs/assets/datepipette-hero-minimal.svg";
import { Brand } from "../components/Brand";
import { login, problemMessage } from "../lib/api";
import { queryKeys } from "../lib/query";

export function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const currentUser = queryClient.getQueryData(queryKeys.session);
  const signIn = useMutation({
    mutationFn: () => login(email, password),
    onSuccess: async (user) => {
      queryClient.setQueryData(queryKeys.session, user);
      const destination =
        typeof location.state === "object" &&
        location.state !== null &&
        "from" in location.state &&
        typeof location.state.from === "string"
          ? location.state.from
          : "/overview";
      await navigate(destination, { replace: true });
    },
  });

  if (currentUser) {
    return <Navigate to="/overview" replace />;
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    signIn.mutate();
  }

  return (
    <main className="login-page">
      <section className="login-story" aria-label="DatePipette product introduction">
        <div className="login-brand"><Brand /></div>
        <div className="story-copy">
          <span className="eyebrow light">Governed analytics workspace</span>
          <h1>Move from operational data to a clear decision.</h1>
          <p>
            Build once from PostgreSQL, read safely from snapshots, and share only the insight each person needs.
          </p>
          <div className="story-proof">
            <span><ShieldCheck size={18} /> Private by default</span>
            <span><LockKeyhole size={18} /> Source-safe reads</span>
          </div>
        </div>
        <img src={brandArtwork} alt="" className="login-art" />
        <p className="story-caption">Precise data. Deliberate access. Calm decisions.</p>
      </section>
      <section className="login-panel">
        <form className="login-form" onSubmit={submit}>
          <div>
            <span className="eyebrow">Welcome back</span>
            <h2>Sign in to DatePipette</h2>
            <p>Use your local workspace account.</p>
          </div>
          <label>
            Work email
            <input
              type="email"
              name="email"
              autoComplete="username"
              placeholder="you@company.com"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
              autoFocus
            />
          </label>
          <label>
            Password
            <span className="password-field">
              <input
                type={showPassword ? "text" : "password"}
                name="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
              <button
                type="button"
                onClick={() => setShowPassword((visible) => !visible)}
                aria-label={showPassword ? "Hide password" : "Show password"}
              >
                {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </span>
          </label>
          {signIn.isError && <div className="form-error" role="alert">{problemMessage(signIn.error)}</div>}
          <button className="button primary wide" type="submit" disabled={signIn.isPending}>
            {signIn.isPending ? "Signing in…" : "Open workspace"}
            {!signIn.isPending && <ArrowRight size={17} />}
          </button>
          <p className="login-security">Your password is sent only to this DatePipette installation and is never stored in the browser.</p>
        </form>
      </section>
    </main>
  );
}
