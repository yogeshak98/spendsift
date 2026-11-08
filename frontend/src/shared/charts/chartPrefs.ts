/**
 * The remembered chart choices of a period panel:
 * - `view` (Heatmap | Bars), per panel: `ss-chart-view:<panel>`;
 * - `scale` (Linear | Compressed √), one choice for every bar chart: `ss-chart-scale`.
 * Defaults: Heatmap, Compressed (the mock's defaults).
 */
import type { YScale } from "./chartScale";
import { useRemembered } from "./useRemembered";

export type ChartView = "heat" | "bars";

export const VIEWS = ["heat", "bars"] as const;
export const SCALES = ["linear", "sqrt"] as const;
export const SCALE_KEY = "ss-chart-scale";
export const viewKey = (panel: string) => `ss-chart-view:${panel}`;

export function useChartPrefs(panel: string) {
  const [view, setView] = useRemembered<ChartView>(viewKey(panel), VIEWS, "heat");
  const [scale, setScale] = useRemembered<YScale>(SCALE_KEY, SCALES, "sqrt");
  return { view, setView, scale, setScale };
}
