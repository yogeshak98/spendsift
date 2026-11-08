/**
 * useChartBox: the measured pixel size of a chart's container plus the root
 * font size, so an SVG can draw in real pixels (viewBox = its box) and scale
 * paddings with the fluid rem (`u` = rem / 13, the design's 13px base).
 *
 * Without ResizeObserver or layout (jsdom), it falls back to `fallback`.
 */
import { useEffect, useLayoutEffect, useRef, useState } from "react";

export type ChartBox = { width: number; height: number; u: number };

function rootUnit(): number {
  try {
    const px = parseFloat(getComputedStyle(document.documentElement).fontSize);
    return Number.isFinite(px) && px > 0 ? px / 13 : 1;
  } catch {
    return 1;
  }
}

export function useChartBox<E extends HTMLElement>(fallback: { width: number; height: number }) {
  const ref = useRef<E>(null);
  const [box, setBox] = useState<ChartBox>({ ...fallback, u: 1 });
  const { width: fw, height: fh } = fallback;

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const r = el.getBoundingClientRect();
      const width = r.width > 0 ? Math.round(r.width) : fw;
      const height = r.height > 0 ? Math.round(r.height) : fh;
      const u = rootUnit();
      setBox((b) => (b.width === width && b.height === height && b.u === u ? b : { width, height, u }));
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [fw, fh]);

  return [ref, box] as const;
}

/** `window.innerHeight`, kept current across resizes (800 when unknown). */
export function useViewportHeight(): number {
  const read = () => (typeof window !== "undefined" && window.innerHeight > 0 ? window.innerHeight : 800);
  const [h, setH] = useState(read);
  useEffect(() => {
    const on = () => setH(read());
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  return h;
}
