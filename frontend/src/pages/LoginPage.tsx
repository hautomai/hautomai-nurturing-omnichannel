import { useState } from "react";

import { useAuth } from "../app/AuthContext";
import { Button } from "../components/ui/Button";
import { Field, Input } from "../components/ui/Fields";
import { BulletList } from "../components/ui/Data";
import { LiveDot } from "../components/ui/Indicators";
import { ApiError } from "../lib/api";

export function LoginPage() {
  const { login, error, loading } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [fieldError, setFieldError] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setFieldError(null);
    try {
      await login(email, password);
    } catch (err) {
      setFieldError(err instanceof ApiError ? err.message : "Sign-in failed. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <LiveDot label="Checking session" />
      </div>
    );
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center px-6 py-12">
      <div className="app-texture" aria-hidden />
      <div className="rise relative z-10 grid w-full max-w-4xl overflow-hidden rounded-panel border border-line bg-[var(--panel-fill)] shadow-panel md:grid-cols-2">
        <div className="hidden flex-col justify-between gap-8 border-r border-line p-8 md:flex">
          <div>
            <p className="font-display text-[26px] font-bold tracking-display text-ink">
              Hautom<em className="not-italic text-accent">ai</em>
            </p>
            <p className="mt-3 font-mono text-[13px] leading-relaxed text-body-2">
              Cross-channel nurture — email, SMS and team alerts in one sequence, where the next
              touchpoint is chosen by how the prospect answered the last one.
            </p>
          </div>
          <BulletList
            items={[
              "Branch on reply, silence and meetings — no pixels, no link rewriting",
              "Email over your own rotated inboxes, SMS through Twilio",
              "React when someone replies, books, or opts out",
            ]}
          />
          <p className="font-mono text-[11px] text-meta-2">Internal tool · Acme innovation</p>
        </div>

        <form onSubmit={onSubmit} className="flex flex-col gap-4 p-8">
          <div>
            <p className="font-mono text-[11px] uppercase tracking-eyebrow text-accent">Sign in</p>
            <h1 className="mt-1 font-display text-[24px] font-bold tracking-title text-ink">
              Operator access
            </h1>
          </div>

          <Field label="Email" htmlFor="email">
            <Input
              id="email"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="ops@hautomai.com"
            />
          </Field>

          <Field label="Password" htmlFor="password" error={fieldError ?? error}>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="••••••••"
            />
          </Field>

          <Button type="submit" variant="primary" loading={submitting} className="mt-1 w-full">
            Enter workspace
          </Button>

          <p className="font-mono text-[11px] leading-relaxed text-meta-2">
            Seeded locally with <code className="text-body-2">BOOTSTRAP_ADMIN_EMAIL</code> and{" "}
            <code className="text-body-2">BOOTSTRAP_ADMIN_PASSWORD</code>.
          </p>
        </form>
      </div>
    </div>
  );
}
