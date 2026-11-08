/**
 * layoutSankey: the pure geometry behind the account-graph Sankey
 * (`GET /api/reports/cashflow/sankey`).
 *
 * - Only FORWARD links (`back: false`) go to d3-sankey: the backend guarantees
 *   they form a DAG, while a back link closes a cycle and d3-sankey throws
 *   "circular link". Back links are drawn here as loops under the diagram,
 *   one lane each, stacked by width (smallest innermost).
 * - Columns are pinned by `role`, as the contract numbers them: payer →
 *   income type → `Tax withheld` → accounts → expense categories →
 *   sub-accounts (an absent role takes no column). The account band is ordered by flow: an account sits one
 *   sub-column right of the furthest account that sends it money, so a
 *   forward account → account link (bank → broker) always runs rightward.
 * - Widths come from link `value` only. A node's `amount` is a label (it can
 *   be negative or zero); its height is max(Σ in, Σ out) over all its links,
 *   back links included, so a loop always lands inside its node.
 * - Loop width cap (ruling R33): a loop is DRAWN at most
 *   min(0.4 × column gap − 8u, 8% of the forward height) wide (never under
 *   1.5px); its true `value` stays on the link and `capped` marks it. The
 *   drawn width sets its lane, bends and attach points.
 * - `height` is the whole budget: the forward diagram shrinks to leave room
 *   for the loop lanes, which may take at most 40% of it, so the drawing
 *   never grows past the cap.
 */
import { sankey, type SankeyGraph, type SankeyLink as D3Link, type SankeyNode as D3Node } from "d3-sankey";
import type { components } from "@/shared/api/schema";

export type SkNode = components["schemas"]["SankeyNode"];
export type SkLink = components["schemas"]["SankeyLink"];

export type Arrow = { x: number; y: number; angle: number };
export type LaidNode = SkNode & { x0: number; x1: number; y0: number; y1: number; col: number };
export type LaidLink = {
  source: string;
  target: string;
  /** The true flow value (the hover Total). */
  value: number;
  back: boolean;
  /** The drawn stroke width. */
  width: number;
  /** A loop drawn narrower than its value (R33 cap). */
  capped: boolean;
  path: string;
  arrows: Arrow[];
};
export type SankeyLayout = { nodes: LaidNode[]; links: LaidLink[]; width: number; height: number };

export type LayoutOptions = {
  width: number;
  /** The whole height budget: forward diagram plus the loop lanes under it. */
  height: number;
  /** rem / 13: scales margins and gaps with the fluid root font. */
  u?: number;
  nodeWidth?: number;
  nodePadding?: number;
  marginLeft?: number;
  marginRight?: number;
};

type N = { id: string; fixedValue: number };
type L = { source: string; target: string; value: number };

/** Column of every node: role order, the account band ordered by flow. */
export function columnsOf(nodes: SkNode[], forward: L[]): Map<string, number> {
  const role = new Map(nodes.map((n) => [n.id, n.role]));
  const has = (r: SkNode["role"]) => nodes.some((n) => n.role === r);
  let c = 0;
  const payer = has("payer") ? c++ : 0;
  const income = has("income") ? c++ : c;
  const tax = has("tax") ? c++ : c;
  const acct0 = c;

  // Longest forward account → account path into each account.
  const into = new Map<string, string[]>();
  for (const l of forward) {
    if (role.get(l.source) === "account" && role.get(l.target) === "account") {
      into.set(l.target, [...(into.get(l.target) ?? []), l.source]);
    }
  }
  const level = new Map<string, number>();
  const levelOf = (id: string, seen: Set<string>): number => {
    const known = level.get(id);
    if (known !== undefined) return known;
    if (seen.has(id)) return 0; // never for a DAG; guards a malformed payload
    seen.add(id);
    const v = Math.max(0, ...(into.get(id) ?? []).map((p) => levelOf(p, seen) + 1));
    level.set(id, v);
    return v;
  };
  let maxLevel = -1;
  for (const n of nodes) if (n.role === "account") maxLevel = Math.max(maxLevel, levelOf(n.id, new Set()));
  const expense = acct0 + maxLevel + 1;
  const sub = expense + 1;

  const col = new Map<string, number>();
  for (const n of nodes) {
    col.set(
      n.id,
      n.role === "payer"
        ? payer
        : n.role === "income"
          ? income
          : n.role === "tax"
            ? tax
            : n.role === "account"
              ? acct0 + (level.get(n.id) ?? 0)
              : n.role === "expense"
                ? expense
                : sub,
    );
  }
  return col;
}

function cubicAt(sx: number, sy: number, tx: number, ty: number, t: number): Arrow {
  const xm = (sx + tx) / 2;
  const mt = 1 - t;
  const x = mt ** 3 * sx + 3 * mt * mt * t * xm + 3 * mt * t * t * xm + t ** 3 * tx;
  const y = mt ** 3 * sy + 3 * mt * mt * t * sy + 3 * mt * t * t * ty + t ** 3 * ty;
  const dx = 3 * mt * mt * (xm - sx) + 3 * t * t * (tx - xm);
  const dy = 6 * mt * t * (ty - sy);
  return { x, y, angle: (Math.atan2(dy, dx) * 180) / Math.PI };
}

/** Arrows every ~140px along a path of length `len`, none under 40px. */
function arrowCount(len: number, u: number): number {
  return len < 40 * u ? 0 : Math.max(1, Math.floor(len / (140 * u)));
}

/** Loop geometry, in units of u (rem / 13) unless a share. */
const LOOP = {
  /** Gap between the forward diagram and the first lane. */
  top: 6,
  /** Space between two lanes, on top of their half-widths. */
  between: 4,
  /** Room under the last lane. */
  bottom: 14,
  /** Bend radius beyond half the stroke. */
  bend: 4,
  /** Share of the column gap a loop may take (less 8u). */
  gapShare: 0.4,
  /** Share of the forward height a loop may take. */
  heightShare: 0.08,
  /** Share of the whole budget the loop lanes may take. */
  bandShare: 0.4,
  minWidth: 1.5,
} as const;

/**
 * The Sankey's whole height (loops included) for a viewport height: the
 * viewport less 20rem, kept between 29.2308rem and 40rem.
 */
export function sankeyHeightCap(viewport: number, rem: number): number {
  return Math.max(29.2308 * rem, Math.min(40 * rem, viewport - 20 * rem));
}

/** The widest a loop is drawn (R33). */
export function maxLoopWidth(gap: number, forwardHeight: number, u: number): number {
  return Math.max(LOOP.minWidth, Math.min(gap * LOOP.gapShare - 8 * u, forwardHeight * LOOP.heightShare));
}

/** A label's box; arrows inside it are left off (they would cross the text). */
export type Box = { x0: number; x1: number; y0: number; y1: number };

/** Mono at 0.7692rem is about 6.2u per glyph; the label sits 6u off the node. */
const GLYPH = 6.2;

export function labelBox(n: { x0: number; x1: number; y0: number; y1: number; col: number }, text: string, u: number): Box {
  const w = (text.length * GLYPH + 8) * u;
  const mid = (n.y0 + n.y1) / 2;
  return n.col === 0
    ? { x0: n.x0 - w, x1: n.x0, y0: mid - 8 * u, y1: mid + 8 * u }
    : { x0: n.x1, x1: n.x1 + w, y0: mid - 8 * u, y1: mid + 8 * u };
}

export function clearOfLabels(arrows: Arrow[], boxes: Box[]): Arrow[] {
  return arrows.filter((a) => !boxes.some((b) => a.x >= b.x0 && a.x <= b.x1 && a.y >= b.y0 && a.y <= b.y1));
}

export function layoutSankey(nodesIn: SkNode[], linksIn: SkLink[], opt: LayoutOptions): SankeyLayout {
  const u = opt.u ?? 1;
  const nodeWidth = opt.nodeWidth ?? 8;
  const nodePadding = opt.nodePadding ?? 9;
  const W = opt.width;
  const left = opt.marginLeft ?? 130 * u;
  const right = opt.marginRight ?? 170 * u;

  const ids = new Set(nodesIn.map((n) => n.id));
  const links: (L & { back: boolean })[] = linksIn
    .map((l) => ({ source: l.source, target: l.target, value: Number(l.value), back: l.back }))
    .filter((l) => l.value > 0 && ids.has(l.source) && ids.has(l.target) && l.source !== l.target);
  const linked = new Set(links.flatMap((l) => [l.source, l.target]));
  const nodes = nodesIn.filter((n) => linked.has(n.id));

  let forward = links.filter((l) => !l.back);
  const col = columnsOf(nodes, forward);
  // Defensive: a forward link that does not run rightward is drawn as a loop.
  const back = links.filter((l) => l.back || (col.get(l.target) ?? 0) <= (col.get(l.source) ?? 0));
  forward = forward.filter((l) => (col.get(l.target) ?? 0) > (col.get(l.source) ?? 0));
  if (forward.length === 0) return { nodes: [], links: [], width: W, height: 0 };

  // Node size from link values only: max(Σ in, Σ out) over every link.
  const inSum = new Map<string, number>();
  const outSum = new Map<string, number>();
  for (const l of links) {
    outSum.set(l.source, (outSum.get(l.source) ?? 0) + l.value);
    inSum.set(l.target, (inSum.get(l.target) ?? 0) + l.value);
  }
  const d3nodes: N[] = nodes.map((n) => ({
    id: n.id,
    fixedValue: Math.max(inSum.get(n.id) ?? 0, outSum.get(n.id) ?? 0),
  }));

  const place = (h: number) => {
    const layout = sankey<N, L>()
      .nodeId((d) => d.id)
      .nodeWidth(nodeWidth)
      .nodePadding(nodePadding)
      .nodeAlign((n) => col.get((n as N).id) ?? 0)
      .extent([
        [left, 10 * u],
        [W - right, h - 10 * u],
      ]);
    const graph: SankeyGraph<N, L> = layout({
      nodes: d3nodes.map((d) => ({ ...d })),
      links: forward.map((l) => ({ source: l.source, target: l.target, value: l.value })),
    });
    // d3-sankey caps the columns at its own longest path; re-pin x to ours.
    for (const n of graph.nodes) {
      n.x0 = left + (col.get(n.id) ?? 0) * kx;
      n.x1 = n.x0 + nodeWidth;
    }
    layout.update(graph);
    const ky = Math.max(
      0,
      ...graph.nodes.map((n) => ((n.value ?? 0) > 0 ? ((n.y1 ?? 0) - (n.y0 ?? 0)) / (n.value ?? 1) : 0)),
    );
    return { graph, ky };
  };

  const ncols = Math.max(...col.values()) + 1;
  const kx = ncols > 1 ? (W - right - left - nodeWidth) / (ncols - 1) : 0;
  const gap = Math.max(nodeWidth, kx - nodeWidth);
  const loops = [...back].sort((a, b) => a.value - b.value || a.source.localeCompare(b.source));
  const budget = opt.height;

  /** Drawn widths, lane spacing and band height for a forward height `h` at scale `ky`. */
  const lanesFor = (h: number, ky: number) => {
    const cap = maxLoopWidth(gap, h, u);
    let widths = loops.map((e) => Math.min(Math.max(LOOP.minWidth, e.value * ky), cap));
    let between = LOOP.between * u;
    const fixed = (LOOP.top + LOOP.bottom) * u;
    const room = Math.max(0, budget * LOOP.bandShare - fixed);
    const need = widths.reduce((a, w) => a + w + between, 0);
    if (need > room && need > 0) {
      // Many loops: shrink widths and spacing together so the band fits.
      const f = room / need;
      widths = widths.map((w) => Math.max(1, w * f));
      between *= f;
    }
    const band = loops.length ? fixed + widths.reduce((a, w) => a + w + between, 0) : 0;
    return { widths, between, band };
  };

  let { graph, ky } = place(budget);
  let H0 = budget;
  if (loops.length) {
    // Second pass: shrink the forward diagram to leave the loop band its room.
    H0 = Math.max(budget * (1 - LOOP.bandShare), budget - lanesFor(budget, ky).band);
    ({ graph, ky } = place(H0));
  }
  const lanes = lanesFor(H0, ky);

  const byId = new Map(graph.nodes.map((n) => [n.id, n as D3Node<N, L>]));
  const out: LaidLink[] = [];
  // Forward links: d3-sankey's horizontal cubic, arrows along it.
  // Forward links fill a node from the top; loops stack from its bottom.
  for (const l of graph.links as D3Link<N, L>[]) {
    const s = l.source as D3Node<N, L>;
    const t = l.target as D3Node<N, L>;
    const sx = s.x1 ?? 0;
    const tx = t.x0 ?? 0;
    const sy = l.y0 ?? 0;
    const ty = l.y1 ?? 0;
    const xm = (sx + tx) / 2;
    const k = arrowCount(Math.hypot(tx - sx, ty - sy), u);
    out.push({
      source: s.id,
      target: t.id,
      value: l.value,
      back: false,
      width: Math.max(1, l.width ?? 0),
      capped: false,
      path: `M${sx},${sy}C${xm},${sy} ${xm},${ty} ${tx},${ty}`,
      arrows: Array.from({ length: k }, (_, i) => cubicAt(sx, sy, tx, ty, (i + 1) / (k + 1))),
    });
  }

  // Back links: leave the source on the right, drop to a lane under the
  // diagram, run left, rise into the target from the left. Smallest first, so
  // the smallest sits innermost (nearest lane and nearest the nodes).
  const colOut = new Map<number, number>();
  const colIn = new Map<number, number>();
  const nodeOut = new Map<string, number>();
  const nodeIn = new Map<string, number>();
  let lane = H0 + LOOP.top * u;
  loops.forEach((e, i) => {
    const S = byId.get(e.source);
    const T = byId.get(e.target);
    if (!S || !T) return;
    const w = lanes.widths[i];
    // Lanes never overlap: centres are (w0 + w1)/2 + spacing apart.
    const ly = lane + w / 2;
    lane += w + lanes.between;
    const cs = col.get(e.source) ?? 0;
    const ct = col.get(e.target) ?? 0;
    const os = colOut.get(cs) ?? 0;
    const ot = colIn.get(ct) ?? 0;
    colOut.set(cs, os + w + 3 * u);
    colIn.set(ct, ot + w + 3 * u);
    // The turn radius clears half the stroke, so a loop never folds over
    // itself; the vertical run starts just past the node edge.
    const r = w / 2 + LOOP.bend * u;
    const xs = (S.x1 ?? 0) + Math.min(2 * u + os, gap * 0.45) + r;
    const xt = Math.max((T.x0 ?? 0) - Math.min(2 * u + ot, gap * 0.45) - r, r);
    const bs = nodeOut.get(e.source) ?? 0;
    const bt = nodeIn.get(e.target) ?? 0;
    nodeOut.set(e.source, bs + w);
    nodeIn.set(e.target, bt + w);
    const y1 = (S.y1 ?? 0) - bs - w / 2;
    const y2 = (T.y1 ?? 0) - bt - w / 2;
    const path =
      `M${S.x1},${y1} H${xs - r} Q${xs},${y1} ${xs},${y1 + r} V${ly - r} Q${xs},${ly} ${xs - r},${ly} ` +
      `H${xt + r} Q${xt},${ly} ${xt},${ly - r} V${y2 + r} Q${xt},${y2} ${xt + r},${y2} H${T.x0}`;
    const run = xs - xt - 2 * r;
    const k = arrowCount(run, u);
    out.push({
      source: e.source,
      target: e.target,
      value: e.value,
      back: true,
      width: w,
      capped: e.value * ky > w + 1e-6,
      path,
      arrows: Array.from({ length: k }, (_, j) => ({ x: xs - r - (run * (j + 1)) / (k + 1), y: ly, angle: 180 })),
    });
  });

  const laid: LaidNode[] = nodes.map((n) => {
    const g = byId.get(n.id)!;
    return { ...n, x0: g.x0 ?? 0, x1: g.x1 ?? 0, y0: g.y0 ?? 0, y1: g.y1 ?? 0, col: col.get(n.id) ?? 0 };
  });
  return { nodes: laid, links: out, width: W, height: loops.length ? lane + LOOP.bottom * u : H0 };
}
