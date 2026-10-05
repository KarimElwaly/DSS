"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, type User } from "@/lib/api";

let _sessionVerified = false;
let _verifyPromise: Promise<boolean> | null = null;

export function resetAuthSession(): void {
  _sessionVerified = false;
  _verifyPromise = null;
}

/**
 * Gate a page on the API accepting us.
 *
 * Caches verification across client-side navigations so clicking tabs
 * transitions instantly instead of unmounting and blocking on /api/auth/me.
 */
export function useRequireAuth(): boolean {
  const router = useRouter();
  const [ready, setReady] = useState<boolean>(_sessionVerified);

  useEffect(() => {
    if (_sessionVerified) {
      if (!ready) setReady(true);
      return;
    }

    let cancelled = false;

    if (!_verifyPromise) {
      _verifyPromise = apiFetch<User>("/api/auth/me")
        .then(() => {
          _sessionVerified = true;
          return true;
        })
        .catch(() => {
          _sessionVerified = false;
          _verifyPromise = null;
          return false;
        });
    }

    _verifyPromise.then((ok) => {
      if (cancelled) return;
      if (ok) {
        setReady(true);
      } else {
        router.replace("/login");
      }
    });

    return () => {
      cancelled = true;
    };
  }, [ready, router]);

  return ready;
}

