import { useEffect, useState, useSyncExternalStore } from "react";
import { api } from "./http";

// A process-wide "the data underneath changed, re-read it" signal.
//
// Refreshing prices is the case this exists for: the refresh endpoints write
// new NAVs/closes server-side, so every mounted report on screen is stale the
// moment they return -- not just the one the button happens to sit next to.
// Bumping one nonce that every useReport depends on re-reads all of them,
// which is why the refresh control does not need to know what else is on the
// page.
let reloadNonce = 0;
const listeners = new Set<() => void>();

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

function getNonce(): number {
  return reloadNonce;
}

/** Re-fetch every mounted `useReport`. Call after a write that invalidates reads. */
export function triggerReload(): void {
  reloadNonce += 1;
  for (const fn of [...listeners]) fn();
}

export function useReport<T>(url: string): { data: T | null; error: string | null } {
  const nonce = useSyncExternalStore(subscribe, getNonce, getNonce);
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    api.getJSON<T>(url).then(
      (d) => {
        if (!alive) return;
        setData(d);
        // A recovered fetch must clear the previous failure, or a screen that
        // errored once keeps rendering its error state over fresh data.
        setError(null);
      },
      (e) => alive && setError(String(e)),
    );
    return () => {
      alive = false;
    };
  }, [url, nonce]);
  return { data, error };
}
