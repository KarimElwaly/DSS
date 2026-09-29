"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, type User } from "@/lib/api";

/**
 * Gate a page on the API accepting us.
 *
 * This asks the API who we are rather than checking for a token in
 * localStorage. It costs one request, but the front end then needs no copy of
 * the server's auth configuration: when `DSS_AUTH_DISABLED` is on the probe
 * simply succeeds, and when it is off an expired token is caught here rather
 * than on the first data request.
 */
export function useRequireAuth(): boolean {
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;

    apiFetch<User>("/auth/me")
      .then(() => {
        if (!cancelled) setReady(true);
      })
      .catch(() => {
        // apiFetch already clears a rejected token, so redirecting is all that
        // is left to do.
        if (!cancelled) router.replace("/login");
      });

    return () => {
      cancelled = true;
    };
  }, [router]);

  return ready;
}
