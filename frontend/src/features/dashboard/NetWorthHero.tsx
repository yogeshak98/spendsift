import { useContext, type MouseEvent, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { inrAbbr, inrWhole } from "@/shared/format/money";
import { TooltipContext } from "@/shared/theme/tooltipContext";
import type { DashboardNetWorth, NetWorthPoint } from "./api";
import { monthName, monthYear, pct0 } from "./format";
import { useBoxSize } from "./useBoxSize";

const signedAbbr = (v: string | number) => {
  const n = Number(v);
  return `${n > 0 ? "+" : n < 0 ? "−" : ""}${inrAbbr(Math.abs(n))}`;
};
const tone = (v: string | number) => (Number(v) > 0 ? "g" : Number(v) < 0 ? "r" : "dim");

/** Net worth over the 12 points, as an area line that fills its box. */
function Trend({ points }: { points: NetWorthPoint[] }) {
  const [ref, { w, h }] = useBoxSize<HTMLDivElement>({ w: 600, h: 96 });
  const setTip = useContext(TooltipContext);
  const vals = points.map((p) => Number(p.net_worth));
  if (vals.length < 2) return <div className="dash-trend" ref={ref} />;
  const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
  const pad = 6;
  const x = (i: number) => (i / (vals.length - 1)) * w;
  const y = (v: number) => h - pad - ((v - lo) / span) * (h - 2 * pad);
  const d = vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const onMove = (e: MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const i = Math.max(0, Math.min(vals.length - 1, Math.round(((e.clientX - r.left) / r.width) * (vals.length - 1))));
    setTip({
      content: (
        <>
          {points[i].label}
          <br />
          <b className="v">{inrWhole(points[i].net_worth)}</b>
        </>
      ),
      x: e.clientX,
      y: e.clientY,
    });
  };
  return (
    <div className="dash-trend" ref={ref}>
      <svg
        width={w}
        height={h}
        viewBox={`0 0 ${w} ${h}`}
        role="img"
        aria-label="Net worth, last 12 months"
        onMouseMove={onMove}
        onMouseLeave={() => setTip(null)}
      >
        <defs>
          <linearGradient id="dash-nw-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="var(--violet)" stopOpacity=".3" />
            <stop offset="1" stopColor="var(--violet)" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={`${d} L${w},${h} L0,${h} Z`} fill="url(#dash-nw-fill)" />
        <path d={d} fill="none" stroke="var(--violet)" strokeWidth={2} />
      </svg>
    </div>
  );
}

function Row(props: {
  op: string;
  name: string;
  facts: ReactNode;
  amount: string;
  amountClass: string;
  route: string;
}) {
  const navigate = useNavigate();
  return (
    <button
      type="button"
      className="row"
      title="Open in Net Worth"
      onClick={() => navigate(`/${props.route}`)}
    >
      <span className="op">{props.op}</span>
      <span className="n">
        {props.name}
        <small>{props.facts}</small>
      </span>
      <span className={`amt ${props.amountClass}`}>{inrWhole(props.amount)}</span>
      <span className="chev" aria-hidden="true">
        ›
      </span>
    </button>
  );
}

const join = (parts: ReactNode[]) =>
  parts.filter(Boolean).flatMap((p, i) => (i ? [" · ", p] : [p]));

/** The hero: the figure, its 12-month line, and the breakdown equation. */
export function NetWorthHero({ nw, asOf }: { nw: DashboardNetWorth; asOf: string }) {
  const { investments: inv, bank, other, liabilities: liab } = nw.breakdown;
  const xirr = inv.xirr_pct === null ? "XIRR —" : `XIRR ${Number(inv.xirr_pct).toFixed(1)}%`;
  const comp = [
    { k: "Investments", v: inv.share_pct, c: "var(--blue)" },
    { k: "Bank", v: bank.share_pct, c: "var(--text2)" },
    { k: "Other", v: other.share_pct, c: "var(--teal)" },
    { k: "Liabilities", v: liab.share_pct, c: "var(--amber)" },
  ];
  const showComp = comp.every((p) => p.v !== null);
  return (
    <section className="panel dash-hero" aria-labelledby="dash-nw-label">
      <div className="nwbox">
        <div className="label" id="dash-nw-label">
          Net worth
        </div>
        <div className="nw v">{inrWhole(nw.current)}</div>
        <div className="delta">
          <b className={tone(nw.change_12_months)}>{signedAbbr(nw.change_12_months)}</b> in 12 months
          {nw.change_12_months_pct !== null && ` (${Number(nw.change_12_months_pct) > 0 ? "+" : ""}${nw.change_12_months_pct}%)`}
          {" · "}
          <b className={tone(nw.change_this_month)}>{signedAbbr(nw.change_this_month)}</b> in {monthName(asOf)}
        </div>
        <Trend points={nw.series} />
      </div>
      <div className="eq">
        <Row
          op=" "
          name="Investments"
          route={inv.route}
          amount={inv.total}
          amountClass="b"
          facts={join([
            xirr,
            inv.gain !== null && `gain ${signedAbbr(inv.gain)}`,
            `${inv.vehicles} ${inv.vehicles === 1 ? "vehicle" : "vehicles"}`,
          ])}
        />
        <Row
          op="+"
          name="Bank and cash"
          route={bank.route}
          amount={bank.total}
          amountClass=""
          facts={join([
            bank.months_of_cover !== null &&
              `covers ${bank.months_of_cover} months of ${bank.cover_basis_months}-month avg spending`,
            !!bank.stale_count && (
              <span className="stale" key="stale">
                {bank.stale_count} {bank.stale_count === 1 ? "import" : "imports"} stale
              </span>
            ),
          ])}
        />
        <Row
          op="+"
          name="Other assets"
          route={other.route}
          amount={other.total}
          amountClass="t"
          facts={other.groups.length ? other.groups.join(", ") : "none"}
        />
        <Row
          op="−"
          name="Liabilities"
          route={liab.route}
          amount={liab.total}
          amountClass="a"
          facts={join([
            liab.emi_per_month !== null &&
              `EMIs ${inrWhole(liab.emi_per_month)}/mo${liab.emi_last_ends ? ` till ${monthYear(liab.emi_last_ends)}` : ""}`,
            liab.cards_net_of_in_transit !== null && `cards ${inrWhole(liab.cards_net_of_in_transit)} net of payments`,
          ])}
        />
        <div className="row total">
          <span className="op">=</span>
          <span className="n strong">Net worth</span>
          <span className="amt v">{inrWhole(nw.current)}</span>
          <span />
        </div>
        {showComp && (
          <>
            <div className="comp" aria-hidden="true">
              {comp.map((p) => (
                <span key={p.k} style={{ flex: Math.abs(Number(p.v)), background: p.c }} />
              ))}
            </div>
            <div className="complg">
              {comp.map((p) => (
                <span key={p.k}>
                  <i style={{ background: p.c }} />
                  {p.k} {pct0(p.v)}
                </span>
              ))}
            </div>
          </>
        )}
      </div>
    </section>
  );
}
