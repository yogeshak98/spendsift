/**
 * ChartSwitches: the panel-header controls of a period chart. Linear |
 * Compressed shows only in Bars view (a heatmap has no axis).
 */
import type { ReactNode } from "react";
import type { ChartView } from "./chartPrefs";
import type { YScale } from "./chartScale";
import { Segmented } from "./Segmented";

const VIEW_OPTIONS = [
  { value: "heat", label: "Heatmap" },
  { value: "bars", label: "Bars" },
] as const;
const SCALE_OPTIONS = [
  { value: "linear", label: "Linear" },
  { value: "sqrt", label: "Compressed" },
] as const;

export function ChartSwitches({
  view,
  setView,
  scale,
  setScale,
  extra,
}: {
  view: ChartView;
  setView: (v: ChartView) => void;
  scale: YScale;
  setScale: (s: YScale) => void;
  /** Controls drawn before the scale/view switches (e.g. Both | Income | Expenses). */
  extra?: ReactNode;
}) {
  return (
    <span className="chart-switches">
      {extra}
      {view === "bars" && <Segmented label="Scale" value={scale} options={SCALE_OPTIONS} onChange={setScale} />}
      <Segmented label="Chart" value={view} options={VIEW_OPTIONS} onChange={setView} />
    </span>
  );
}
