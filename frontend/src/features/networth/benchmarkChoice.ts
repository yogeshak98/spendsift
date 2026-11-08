import { useSyncExternalStore } from "react";

// The benchmark the viewer compares their investments with, remembered per
// browser. Only a preference lives here, never money. Storage can be missing or
// throw (private window, blocked site data), so every access is guarded and the
// choice still works in memory for the session.
//
// `null` means "no choice made": the backend then applies its own default
// (Nifty 50, or the first configured benchmark).

const STORAGE_KEY = "spendsift.networth.benchmark";

let loaded = false;
let current: string | null = null;
const listeners = new Set<() => void>();

function readStored(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function snapshot(): string | null {
  if (!loaded) {
    current = readStored();
    loaded = true;
  }
  return current;
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** Choose a benchmark key, or `null` to fall back to the backend default. */
export function setBenchmarkChoice(key: string | null): void {
  loaded = true;
  current = key;
  try {
    if (key === null) window.localStorage.removeItem(STORAGE_KEY);
    else window.localStorage.setItem(STORAGE_KEY, key);
  } catch {
    // Not persisted; the in-memory choice still holds for this session.
  }
  for (const fn of [...listeners]) fn();
}

export function useBenchmarkChoice(): string | null {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

/** Test hook: forget the in-memory choice so the next read goes to storage. */
export function resetBenchmarkChoice(): void {
  loaded = false;
  current = null;
}
