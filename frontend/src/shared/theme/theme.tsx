import { type Theme, useTheme } from "./useTheme";

const THEMES: { value: Theme; label: string }[] = [
  { value: "midnight", label: "◐ Midnight" },
  { value: "carbon", label: "◑ Carbon" },
  { value: "daylight", label: "○ Daylight" },
];

export function ThemeSwitcher() {
  const [theme, setTheme] = useTheme();
  return (
    <select aria-label="Theme" value={theme} onChange={(e) => setTheme(e.target.value as Theme)}>
      {THEMES.map((t) => (
        <option key={t.value} value={t.value}>{t.label}</option>
      ))}
    </select>
  );
}
