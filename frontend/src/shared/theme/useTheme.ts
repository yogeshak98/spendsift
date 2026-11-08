import { useEffect, useState } from "react";

export type Theme = "midnight" | "carbon" | "daylight";

const KEY = "ss-theme";
const THEMES: readonly Theme[] = ["midnight", "carbon", "daylight"];

// Storage can be missing or throw (private window, blocked site data). The
// theme is a convenience: on any failure it falls back to Midnight and simply
// is not remembered, rather than taking the whole app shell down.
function storedTheme(): Theme {
  try {
    const raw = localStorage.getItem(KEY);
    return THEMES.includes(raw as Theme) ? (raw as Theme) : "midnight";
  } catch {
    return "midnight";
  }
}

export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(storedTheme);
  useEffect(() => {
    document.body.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(KEY, theme);
    } catch {
      // Not remembered; the choice still holds for this session.
    }
  }, [theme]);
  return [theme, setTheme];
}
