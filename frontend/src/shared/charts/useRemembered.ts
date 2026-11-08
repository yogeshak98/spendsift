/**
 * useRemembered: a string choice remembered per viewer in localStorage
 * (e.g. Heatmap | Bars, Linear | Compressed).
 *
 * Every read and write is wrapped in try/catch: storage can be missing or
 * throw (private window, blocked site data), and the chart must still work
 * with the default. A stored value outside `allowed` falls back to it too.
 */
import { useState } from "react";

export function readRemembered<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    return v !== null && (allowed as readonly string[]).includes(v) ? (v as T) : fallback;
  } catch {
    return fallback;
  }
}

export function useRemembered<T extends string>(
  key: string,
  allowed: readonly T[],
  fallback: T,
): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => readRemembered(key, allowed, fallback));
  const set = (v: T) => {
    setValue(v);
    try {
      localStorage.setItem(key, v);
    } catch {
      // not remembered this time; the choice still applies on screen
    }
  };
  return [value, set];
}
