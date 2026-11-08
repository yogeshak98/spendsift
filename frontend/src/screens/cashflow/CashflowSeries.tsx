/**
 * The Cash Flow · Overview series panel ("Daily cash flow" / "Monthly cash
 * flow") over `CashflowOverviewResponse.series`.
 *
 * - Both | Income | Expenses picks what is drawn. Heatmap: Both is the net
 *   (green in, red out), Income its income (green), Expenses its spending
 *   (red); no minus signs, the colour carries direction. Bars: income and
 *   expenses side by side above the axis; the net is in the hover.
 * - Heatmap | Bars and Linear | Compressed are remembered per viewer.
 * - In Linear, Both, when one income bucket is over 8× the largest spending
 *   one, a hint suggests switching to Expenses.
 * - A cell or bar opens the detail modal for that day / month.
 */
import { useState } from "react";
import type { components } from "@/shared/api/schema";
import { Panel } from "../../components/Panel";
import { PeriodHeatmap } from "@/shared/charts/PeriodHeatmap";
import { PeriodBars, type BarSeries } from "@/shared/charts/PeriodBars";
import { ChartSwitches } from "@/shared/charts/ChartSwitches";
import { Segmented } from "@/shared/charts/Segmented";
import { useChartPrefs } from "@/shared/charts/chartPrefs";
import { bucketRange, bucketTitle } from "@/shared/charts/periodBuckets";
import { inrWhole } from "@/shared/format/money";
import type { DetailSpec } from "@/shared/ui/DetailModal";

type Resp = components["schemas"]["CashflowOverviewResponse"];
type Show = "both" | "income" | "expenses";

const SHOW_OPTIONS = [
  { value: "both", label: "Both" },
  { value: "income", label: "Income" },
  { value: "expenses", label: "Expenses" },
] as const;

function Tip({ title, income, expenses }: { title: string; income: number; expenses: number }) {
  const net = income - expenses;
  return (
    <div className="sk-tip">
      <div className="sk-tip-h">{title}</div>
      <div className="sk-tip-row">
        <span>Income</span>
        <span className="v-green">{inrWhole(income)}</span>
      </div>
      <div className="sk-tip-row">
        <span>Expenses</span>
        <span className="v-red">{inrWhole(expenses)}</span>
      </div>
      <div className="sk-tip-row">
        <span>{net >= 0 ? "Net in" : "Net out"}</span>
        <span className={net >= 0 ? "v-green" : "v-red"}>{inrWhole(Math.abs(net))}</span>
      </div>
    </div>
  );
}

export function CashflowSeries({ data, onOpen }: { data: Resp; onOpen: (d: DetailSpec) => void }) {
  const chart = useChartPrefs("cashflow");
  const [show, setShow] = useState<Show>("both");
  const inc = data.series.map((p) => Number(p.income));
  const exp = data.series.map((p) => Number(p.expenses));
  const day = data.granularity === "day";

  const open = (key: string) => {
    const r = bucketRange(key, data.start, data.end);
    onOpen({
      title: bucketTitle(key),
      sub: "Cash flow · click a category to filter",
      query: { ...r, side: show === "both" ? "all" : show === "income" ? "income" : "expense" },
    });
  };
  const tip = (i: number) => <Tip title={bucketTitle(data.series[i].key)} income={inc[i]} expenses={exp[i]} />;

  const series: BarSeries[] = [];
  if (show !== "expenses") series.push({ id: "income", label: "Income", color: "var(--green)", values: inc });
  if (show !== "income") series.push({ id: "expenses", label: "Expenses", color: "var(--red)", values: exp });
  const dwarfed = show === "both" && Math.max(0, ...inc) > 8 * Math.max(0, ...exp);

  const body =
    chart.view === "heat" ? (
      <PeriodHeatmap
        granularity={data.granularity}
        from={data.start}
        to={data.end}
        cells={data.series.map((p, i) => ({
          key: p.key,
          label: p.label,
          value: show === "both" ? inc[i] - exp[i] : show === "income" ? inc[i] : exp[i],
        }))}
        positive={show === "expenses" ? "out" : "in"}
        legend={show === "both" ? "direction" : "magnitude"}
        tip={(c) => tip(data.series.findIndex((p) => p.key === c.key))}
        onPick={(c) => open(c.key)}
      />
    ) : (
      <>
        <PeriodBars
          points={data.series}
          series={series}
          scale={chart.scale}
          ariaLabel={`cash flow per ${day ? "day" : "month"}`}
          tip={tip}
          onPick={(i) => open(data.series[i].key)}
          legend
        />
        {chart.scale === "linear" && dwarfed && (
          <div className="pbars-hint">
            One large credit sets the scale. Switch to <b>Expenses</b> to read the smaller days.
          </div>
        )}
      </>
    );

  return (
    <Panel
      title={`${day ? "Daily" : "Monthly"} cash flow`}
      right={
        <ChartSwitches
          {...chart}
          extra={<Segmented label="Show" value={show} options={SHOW_OPTIONS} onChange={setShow} />}
        />
      }
    >
      {body}
    </Panel>
  );
}
