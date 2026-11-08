/**
 * Sankey: the account-graph cash-flow diagram (Paisa-style), drawn from
 * `GET /api/reports/cashflow/sankey` (`nodes` + `links`).
 *
 * `<Sankey nodes links onPick? />`
 * - Layout in `sankeyLayout.ts`: d3-sankey (nodeWidth 8, nodePadding 9) over
 *   the forward links only; back links are loops under the diagram, stacked by
 *   width; arrowheads along every flow.
 * - The diagram fills the container's width; its whole height, loops
 *   included, is capped to the viewport (between 29.2rem and 40rem) and
 *   follows window resizes. A loop drawn narrower than its value (R33 cap)
 *   carries `data-capped`, and its hover says so under the true Total (a
 *   dashed thick stroke read as a ladder, so the mark is in the hover).
 * - Colours by `kind`: income green, asset blue, liability amber, expense red.
 * - Hover: a node shows its id, its amount (a label, may be negative) and,
 *   for "Other", the folded categories; a flow shows From (credit) / To
 *   (debit) / Total. Click, or Enter/Space on a focused node or flow, calls
 *   `onPick`. Arrows inside a label's box are left off.
 * - Labels that would collide with the one above in the same column are
 *   left off (the hover still names the node).
 */
import { useContext, useId, useMemo, type KeyboardEvent, type ReactNode } from "react";
import { TooltipContext } from "@/shared/theme/tooltipContext";
import { inrWhole } from "@/shared/format/money";
import { rupeeK } from "./chartScale";
import { clearOfLabels, labelBox, layoutSankey, sankeyHeightCap, type LaidNode, type SkLink, type SkNode } from "./sankeyLayout";
import { useChartBox, useViewportHeight } from "./useChartBox";

export type SankeyPick = { kind: "node"; id: string; label: string } | { kind: "link"; source: string; target: string };

const COLOUR: Record<SkNode["kind"], string> = {
  income: "var(--green)",
  asset: "var(--blue)",
  liability: "var(--amber)",
  expense: "var(--red)",
};

function Rows({ head, rows }: { head: string; rows: [string, string][] }) {
  return (
    <div className="sk-tip">
      <div className="sk-tip-h">{head}</div>
      {rows.map(([k, v], i) => (
        <div className="sk-tip-row" key={i}>
          <span>{k}</span>
          <span>{v}</span>
        </div>
      ))}
    </div>
  );
}

const labelText = (n: LaidNode) => `${n.label} ${rupeeK(Number(n.amount))}${n.folded.length ? " ⓘ" : ""}`;

/** Ids of the nodes whose label fits under the previous shown label of its column. */
function labelled(nodes: LaidNode[], lineH: number): Set<string> {
  const shown = new Set<string>();
  const byCol = new Map<number, LaidNode[]>();
  for (const n of nodes) byCol.set(n.col, [...(byCol.get(n.col) ?? []), n]);
  for (const list of byCol.values()) {
    let last = -Infinity;
    for (const n of [...list].sort((a, b) => a.y0 + a.y1 - (b.y0 + b.y1))) {
      const mid = (n.y0 + n.y1) / 2;
      if (mid - last >= lineH) {
        shown.add(n.id);
        last = mid;
      }
    }
  }
  return shown;
}

export function Sankey({
  nodes,
  links,
  onPick,
}: {
  nodes: SkNode[];
  links: SkLink[];
  onPick?: (p: SankeyPick) => void;
}) {
  const setTip = useContext(TooltipContext);
  const uid = useId().replace(/:/g, "");
  const [ref, { width, u }] = useChartBox<HTMLDivElement>({ width: 1000, height: 400 });
  const rem = 13 * u;
  const viewport = useViewportHeight();
  const H0 = sankeyHeightCap(viewport, rem);

  const g = useMemo(() => layoutSankey(nodes, links, { width, height: H0, u }), [nodes, links, width, H0, u]);
  const byId = useMemo(() => new Map(g.nodes.map((n) => [n.id, n])), [g]);
  const show = useMemo(() => labelled(g.nodes, 12 * u), [g, u]);
  const order = useMemo(
    () => g.links.map((l, i) => [l.back, i] as const).sort((a, b) => Number(b[0]) - Number(a[0]) || a[1] - b[1]).map(([, i]) => i),
    [g],
  );

  const hover = (content: ReactNode) => ({
    onMouseEnter: (e: { clientX: number; clientY: number }) => setTip({ content, x: e.clientX, y: e.clientY }),
    onMouseMove: (e: { clientX: number; clientY: number }) => setTip({ content, x: e.clientX, y: e.clientY }),
    onMouseLeave: () => setTip(null),
  });
  const pick = (p: SankeyPick) => {
    setTip(null);
    onPick?.(p);
  };
  /** Click plus Enter/Space for a focusable SVG element. */
  const press = (p: SankeyPick) =>
    onPick
      ? {
          tabIndex: 0,
          role: "button",
          onClick: () => pick(p),
          onKeyDown: (e: KeyboardEvent<SVGElement>) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              pick(p);
            }
          },
        }
      : {};
  const arrows = useMemo(() => {
    const boxes = g.nodes.filter((n) => show.has(n.id)).map((n) => labelBox(n, labelText(n), u));
    return g.links.map((l) => clearOfLabels(l.arrows, boxes));
  }, [g, show, u]);

  return (
    <div className="sk-wrap" ref={ref}>
      {g.nodes.length === 0 ? (
        <div className="panel-body">
          <span className="note flow-note">No flows to draw in this period.</span>
        </div>
      ) : (
        <svg viewBox={`0 0 ${g.width} ${g.height}`} width="100%" role="img" aria-label="Money flow between ledger accounts">
          <defs>
            {g.links.map((l, i) =>
              l.back ? null : (
                <linearGradient
                  key={i}
                  id={`${uid}-lg${i}`}
                  gradientUnits="userSpaceOnUse"
                  x1={byId.get(l.source)!.x1}
                  x2={byId.get(l.target)!.x0}
                >
                  <stop offset="0%" stopColor={COLOUR[byId.get(l.source)!.kind]} />
                  <stop offset="100%" stopColor={COLOUR[byId.get(l.target)!.kind]} />
                </linearGradient>
              ),
            )}
          </defs>
          <g fill="none">
            {/* loops first, so the forward ribbons and the nodes sit on top */}
            {order.map((i) => {
              const l = g.links[i];
              const tip = (
                <Rows
                  head={`${byId.get(l.source)!.label} → ${byId.get(l.target)!.label}`}
                  rows={[
                    ["From (credit)", l.source],
                    ["To (debit)", l.target],
                    ["Total", inrWhole(l.value)],
                    ...(l.capped ? [["Drawn", "narrower than its Total"] as [string, string]] : []),
                  ]}
                />
              );
              return (
                <path
                  key={`${l.source}|${l.target}|${l.back}`}
                  className={`${l.back ? "sk-back" : "sk-link"}${onPick ? " is-clickable" : ""}`}
                  data-source={l.source}
                  data-target={l.target}
                  data-capped={l.capped || undefined}
                  d={l.path}
                  stroke={l.back ? COLOUR[byId.get(l.source)!.kind] : `url(#${uid}-lg${i})`}
                  strokeWidth={l.width}
                  strokeLinejoin="round"
                  aria-label={`${byId.get(l.source)!.label} → ${byId.get(l.target)!.label} ${inrWhole(l.value)}`}
                  {...press({ kind: "link", source: l.source, target: l.target })}
                  {...hover(tip)}
                />
              );
            })}
          </g>
          <g className="sk-arrows" aria-hidden="true">
            {g.links.flatMap((l, i) =>
              arrows[i].map((a, k) => (
                <path
                  key={`${l.source}|${l.target}|${l.back}|${k}`}
                  d={`M${-4 * u},${-3.5 * u} L${3 * u},0 L${-4 * u},${3.5 * u} Z`}
                  transform={`translate(${a.x},${a.y}) rotate(${a.angle})`}
                />
              )),
            )}
          </g>
          {g.nodes.map((n) => {
            const amount = Number(n.amount);
            const tip = (
              <Rows
                head={n.id}
                rows={[["Total", inrWhole(amount)], ...n.folded.map((f): [string, string] => [f.label, inrWhole(Number(f.amount))])]}
              />
            );
            return (
              <g key={n.id} className="sk-node" data-id={n.id}>
                <rect
                  x={n.x0}
                  y={n.y0}
                  width={n.x1 - n.x0}
                  height={Math.max(2, n.y1 - n.y0)}
                  rx={2}
                  fill={COLOUR[n.kind]}
                  aria-label={`${n.label} ${inrWhole(amount)}`}
                  className={onPick ? "is-clickable" : undefined}
                  {...press({ kind: "node", id: n.id, label: n.label })}
                  {...hover(tip)}
                />
              </g>
            );
          })}
          <g className="sk-labels">
            {g.nodes.map((n) =>
              show.has(n.id) ? (
                <text
                  key={n.id}
                  x={n.col === 0 ? n.x0 - 6 * u : n.x1 + 6 * u}
                  y={(n.y0 + n.y1) / 2}
                  dy="0.35em"
                  textAnchor={n.col === 0 ? "end" : "start"}
                >
                  {`${n.label} `}
                  <tspan className="v">{`${rupeeK(Number(n.amount))}${n.folded.length ? " ⓘ" : ""}`}</tspan>
                </text>
              ) : null,
            )}
          </g>
        </svg>
      )}
      <div className="legend sk-legend">
        <span>
          <span className="dot" style={{ background: "var(--green)" }} />
          Income
        </span>
        <span>
          <span className="dot" style={{ background: "var(--blue)" }} />
          Assets (bank, investments, cash, people)
        </span>
        <span>
          <span className="dot" style={{ background: "var(--amber)" }} />
          Liabilities (cards, loans)
        </span>
        <span>
          <span className="dot" style={{ background: "var(--red)" }} />
          Expenses
        </span>
      </div>
    </div>
  );
}
