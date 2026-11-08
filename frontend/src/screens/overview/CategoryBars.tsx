/**
 * CategoryBars: the By category / By source list of a flow overview. Each
 * row is a button that opens the detail modal for that group; the hover lists
 * the top names (spendTip "items"). A negative group (a refund-only category,
 * or one whose one-off was left out) draws a zero-width bar and its amount as
 * a magnitude in the opposite tone: colour carries direction, as on the
 * calendar, so there is no minus sign.
 */
import { useContext } from "react";
import { TooltipContext } from "@/shared/theme/tooltipContext";
import { inrWhole } from "@/shared/format/money";
import { CATEGORY_PALETTE } from "@/shared/charts/palette";
import { spendTip, type DayDetail } from "../../charts/spendTip";
import type { FlowGroup } from "./flowOverviewApi";

const leaf = (account: string) => account.split(":").pop() ?? account;

export function CategoryBars({
  groups,
  tone,
  onPick,
}: {
  groups: FlowGroup[];
  tone: "red" | "green";
  onPick: (g: FlowGroup) => void;
}) {
  const setTip = useContext(TooltipContext);
  const flip = tone === "red" ? "green" : "red";
  const max = Math.max(...groups.map((g) => Number(g.pct)), 1);
  return (
    <div>
      {groups.map((g, i) => {
        const label = leaf(g.group);
        const amount = Number(g.amount);
        const items: DayDetail[] = g.items.map((it) => ({
          merchant: it.name,
          category: g.group,
          amount: it.amount,
          count: it.count,
        }));
        const tip = spendTip(label, amount, items, "items");
        const show = (e: { clientX: number; clientY: number }) => setTip({ content: tip, x: e.clientX, y: e.clientY });
        return (
          <button
            type="button"
            key={g.group}
            className="cat-row"
            onClick={() => {
              setTip(null);
              onPick(g);
            }}
            onMouseEnter={show}
            onMouseMove={show}
            onMouseLeave={() => setTip(null)}
          >
            <span className="nm">
              <span className="tw">▸</span>
              {label}
            </span>
            <span className="barwrap">
              <span
                className="barfill"
                style={{
                  display: "block",
                  width: `${(Math.max(0, Number(g.pct)) / max) * 100}%`,
                  background: CATEGORY_PALETTE[i % CATEGORY_PALETTE.length],
                }}
              />
            </span>
            <span className={`amt v-${amount < 0 ? flip : tone}`}>{inrWhole(Math.abs(amount))}</span>
            <span className="pct">{g.pct}%</span>
          </button>
        );
      })}
    </div>
  );
}
