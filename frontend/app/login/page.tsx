"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, apiFetch, login, type User } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("admin@dss-demo.com");
  const [password, setPassword] = useState("admin12345");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    let cancelled = false;
    // Ask the API whether we are already accepted. This covers a live token
    // and, when the server runs with auth disabled, skips the form entirely --
    // so the front end needs no flag of its own to stay in step.
    apiFetch<User>("/auth/me")
      .then(() => router.replace("/"))
      .catch(() => {
        if (!cancelled) setChecking(false);
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      router.replace("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to sign in.");
    } finally {
      setBusy(false);
    }
  }

  if (checking) return null;

  return (
    <div className="mx-auto mt-20 max-w-sm">
      <h1 className="text-xl font-semibold">Revenue &amp; Pricing DSS</h1>
      <p className="mt-1 text-sm text-slate-400">Sign in to continue.</p>

      <form onSubmit={onSubmit} className="card mt-6 space-y-4 p-5">
        <label className="block">
          <span className="text-xs font-medium text-slate-400">Email</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            className="mt-1 w-full rounded-md border border-[#243044] bg-[#0d1424] px-3 py-2 text-sm outline-none focus:border-emerald-500"
          />
        </label>

        <label className="block">
          <span className="text-xs font-medium text-slate-400">Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
            className="mt-1 w-full rounded-md border border-[#243044] bg-[#0d1424] px-3 py-2 text-sm outline-none focus:border-emerald-500"
          />
        </label>

        {error && (
          <p className="rounded-md bg-red-500/10 px-3 py-2 text-xs text-red-300">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-md bg-emerald-500 px-3 py-2 text-sm font-semibold text-slate-950 transition hover:bg-emerald-400 disabled:opacity-50"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
