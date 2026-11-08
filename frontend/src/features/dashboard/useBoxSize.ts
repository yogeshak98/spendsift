import { useLayoutEffect, useRef, useState } from "react";

/**
 * The pixel size of a box whose width comes from its container and whose
 * height comes from CSS (in rem), so a chart drawn in it scales with both the
 * container and the fluid root font. Falls back to `fallback` where layout is
 * unavailable (jsdom).
 */
export function useBoxSize<T extends HTMLElement>(fallback: { w: number; h: number }) {
  const ref = useRef<T>(null);
  const [size, setSize] = useState(fallback);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const read = () => {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) setSize({ w: Math.round(r.width), h: Math.round(r.height) });
    };
    read();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, size] as const;
}
