import { useEffect, useRef, useState } from "react";
import type { Period } from "./period";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

type Props = Period & {
  // Inclusive navigable year span. Arrows stop at Jan of minYear / Dec of maxYear.
  minYear: number;
  maxYear: number;
  onChange: (p: Period) => void;
};

function isEditable(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName?.toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable;
}

// Compact month stepper: ‹ Nov 2025 ⌄ ›. The arrows step one month and roll
// across year boundaries; the label opens a year stepper + month grid for long
// jumps. ← / → anywhere on the page (outside text fields) step a month too, so
// the common "previous/next month" move never needs the mouse.
export function MonthPicker({ year, month, minYear, maxYear, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [viewYear, setViewYear] = useState(year);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const idx = year * 12 + (month - 1);
  const lo = minYear * 12;
  const hi = maxYear * 12 + 11;
  const canPrev = idx > lo;
  const canNext = idx < hi;
  const step = (d: number) => {
    const n = Math.min(Math.max(idx + d, lo), hi);
    if (n !== idx) onChange({ year: Math.floor(n / 12), month: (n % 12) + 1 });
  };

  useEffect(() => {
    if (open) return; // the popover handles its own keys
    function onKey(e: KeyboardEvent) {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      if (e.metaKey || e.ctrlKey || e.altKey || e.shiftKey || e.defaultPrevented) return;
      if (isEditable(e.target)) return;
      const n = Math.min(Math.max(idx + (e.key === "ArrowLeft" ? -1 : 1), lo), hi);
      e.preventDefault();
      if (n !== idx) onChange({ year: Math.floor(n / 12), month: (n % 12) + 1 });
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, idx, lo, hi, onChange]);

  // Close on outside click / Escape; focus the selected month on open.
  useEffect(() => {
    if (!open) return;
    rootRef.current?.querySelector<HTMLButtonElement>(".mpick-month[aria-pressed='true']")?.focus();
    function onDown(e: MouseEvent) {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const toggle = () => {
    setViewYear(year);
    setOpen((o) => !o);
  };
  const pick = (m: number) => {
    onChange({ year: viewYear, month: m });
    setOpen(false);
    triggerRef.current?.focus();
  };

  return (
    <div className="mpick" ref={rootRef}>
      <button type="button" className="mpick-arrow" aria-label="Previous month" disabled={!canPrev} onClick={() => step(-1)}>
        <Chevron dir="left" />
      </button>
      <button
        type="button"
        ref={triggerRef}
        className="mpick-label"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`Choose month, ${MONTHS[month - 1]} ${year}`}
        onClick={toggle}
      >
        <span>
          {MONTHS[month - 1]} {year}
        </span>
        <Chevron dir="down" />
      </button>
      <button type="button" className="mpick-arrow" aria-label="Next month" disabled={!canNext} onClick={() => step(1)}>
        <Chevron dir="right" />
      </button>

      {open && (
        <div className="mpick-pop" role="dialog" aria-label="Choose month">
          <div className="mpick-year">
            <button
              type="button"
              aria-label="Previous year"
              disabled={viewYear <= minYear}
              onClick={() => setViewYear((y) => y - 1)}
            >
              <Chevron dir="left" />
            </button>
            <span aria-live="polite">{viewYear}</span>
            <button
              type="button"
              aria-label="Next year"
              disabled={viewYear >= maxYear}
              onClick={() => setViewYear((y) => y + 1)}
            >
              <Chevron dir="right" />
            </button>
          </div>
          <div className="mpick-grid">
            {MONTHS.map((m, i) => (
              <button
                type="button"
                key={m}
                className="mpick-month"
                aria-pressed={viewYear === year && i + 1 === month}
                aria-label={`${m} ${viewYear}`}
                onClick={() => pick(i + 1)}
              >
                {m}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Chevron({ dir }: { dir: "left" | "right" | "down" }) {
  const d = dir === "left" ? "M10 3 5 8l5 5" : dir === "right" ? "M6 3l5 5-5 5" : "M4 6l4 4 4-4";
  return (
    <svg style={{ width: "1.0769rem", height: "1.0769rem" }} viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
    </svg>
  );
}
