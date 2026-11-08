import { createContext, type ReactNode } from "react";

export type TipState = { content: ReactNode; x: number; y: number } | null;
/** Tooltip-setter context. Consumed by `Tip` for HTML targets and read
 *  directly by SVG charts (e.g. Sankey) that attach hover handlers to
 *  individual `<path>` elements, where a wrapping `<span>` is not valid. */
export const TooltipContext = createContext<(s: TipState) => void>(() => {});
