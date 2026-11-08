import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { TooltipProvider } from "@/shared/theme/tooltip";
import { compactK, niceMax, sqrtTicks, transform } from "@/shared/charts/chartScale";
import {
  clearOfLabels,
  columnsOf,
  labelBox,
  layoutSankey,
  sankeyHeightCap,
  type SkLink,
  type SkNode,
} from "@/shared/charts/sankeyLayout";
import { PeriodHeatmap } from "@/shared/charts/PeriodHeatmap";
import { PeriodBars } from "@/shared/charts/PeriodBars";
import { Sankey } from "@/shared/charts/Sankey";
import { ChartSwitches } from "@/shared/charts/ChartSwitches";
import { useChartPrefs } from "@/shared/charts/chartPrefs";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  localStorage.clear();
});

// Synthetic account graph: salary → bank, bank → card → food, bank → broker,
// plus two back links (broker → bank redemption, a refund-only category → bank).
const node = (id: string, role: SkNode["role"], kind: SkNode["kind"], amount: string, label = id.split(":").pop()!): SkNode => ({
  id,
  label,
  kind,
  role,
  amount,
  folded: [],
});
const NODES: SkNode[] = [
  node("Income:Salary", "income", "income", "100000.00"),
  node("Assets:Checking:Synth", "account", "asset", "100000.00"),
  node("Assets:Broker:Synth", "account", "asset", "40000.00"),
  node("Liabilities:CreditCard:Acme", "account", "liability", "9000.00"),
  node("Expenses:Food", "expense", "expense", "9000.00"),
  node("Expenses:Refundy", "expense", "expense", "-300.00"),
];
const link = (source: string, target: string, value: string, back = false): SkLink => ({ source, target, value, back });
const LINKS: SkLink[] = [
  link("Income:Salary", "Assets:Checking:Synth", "100000.00"),
  link("Assets:Checking:Synth", "Assets:Broker:Synth", "40000.00"),
  link("Assets:Checking:Synth", "Liabilities:CreditCard:Acme", "9000.00"),
  link("Liabilities:CreditCard:Acme", "Expenses:Food", "9000.00"),
  link("Assets:Broker:Synth", "Assets:Checking:Synth", "15000.00", true),
  link("Expenses:Refundy", "Assets:Checking:Synth", "300.00", true),
];

/** The y of a loop's bottom lane (its `Q xs,ly xs-r,ly H` bend). */
const lane = (p: string) => Number(/Q[\d.]+,([\d.]+) [\d.]+,\1 H/.exec(p)?.[1]);

// A big cycle (the demo's Oct 2026 shape): ₹3L salary into the bank, ₹1.5L
// to a fund, and a ₹1.5L redemption back: as wide as half the inflow.
const BIG_NODES: SkNode[] = [
  node("Income:Salary", "income", "income", "300000.00"),
  node("Assets:Checking:Synth", "account", "asset", "450000.00"),
  node("Assets:Investment:Fund", "account", "asset", "150000.00"),
  node("Expenses:Rent", "expense", "expense", "20000.00"),
];
const BIG_LINKS: SkLink[] = [
  link("Income:Salary", "Assets:Checking:Synth", "300000.00"),
  link("Assets:Checking:Synth", "Assets:Investment:Fund", "150000.00"),
  link("Assets:Checking:Synth", "Expenses:Rent", "20000.00"),
  link("Assets:Investment:Fund", "Assets:Checking:Synth", "150000.00", true),
];

describe("layoutSankey", () => {
  test("a large loop is drawn capped (R33) and the whole drawing stays within the height budget", () => {
    const g = layoutSankey(BIG_NODES, BIG_LINKS, { width: 1280, height: 400 });
    // uncapped it would be 150000 × ky ≈ a third of the forward height
    const loop = g.links.find((l) => l.back)!;
    const forwardBottom = Math.max(...g.nodes.map((n) => n.y1));
    expect(loop.value).toBe(150000);
    expect(loop.capped).toBe(true);
    // at most 8% of the forward height (and of the column gap rule)
    expect(loop.width).toBeLessThanOrEqual(0.08 * forwardBottom + 1);
    expect(g.height).toBeLessThanOrEqual(400);
    // nodes keep their true size: the loop lands inside the bank node
    const bank = g.nodes.find((n) => n.id === "Assets:Checking:Synth")!;
    expect(bank.y1 - bank.y0).toBeGreaterThan(loop.width);
  });

  test("Tax withheld has its own column, so a forward TDS → account link stays forward", () => {
    const nodes: SkNode[] = [
      node("Income:Salary", "income", "income", "100000.00"),
      node("Tax withheld", "tax", "expense", "9000.00", "Tax withheld"),
      node("Assets:Checking:Synth", "account", "asset", "92000.00"),
    ];
    const links: SkLink[] = [
      link("Income:Salary", "Tax withheld", "10000.00"),
      link("Income:Salary", "Assets:Checking:Synth", "90000.00"),
      // a TDS refund (negative TDS) runs tax → account, forward per the contract
      link("Tax withheld", "Assets:Checking:Synth", "1000.00"),
    ];
    const col = columnsOf(nodes, links.map((l) => ({ ...l, value: Number(l.value) })));
    expect(col.get("Tax withheld")!).toBeGreaterThan(col.get("Income:Salary")!);
    expect(col.get("Assets:Checking:Synth")!).toBeGreaterThan(col.get("Tax withheld")!);
    const g = layoutSankey(nodes, links, { width: 1000, height: 400 });
    expect(g.links.filter((l) => l.back)).toHaveLength(0);
  });

  test("the height cap follows the viewport: viewport − 20rem, between 29.2rem and 40rem", () => {
    expect(sankeyHeightCap(600, 13)).toBeCloseTo(29.2308 * 13, 3);
    expect(sankeyHeightCap(700, 13)).toBe(700 - 260);
    expect(sankeyHeightCap(2000, 13)).toBe(520);
  });

  test("arrows inside a label's box are left off", () => {
    const box = labelBox({ x0: 100, x1: 108, y0: 40, y1: 60, col: 1 }, "Salary ₹24L", 1);
    const kept = clearOfLabels(
      [
        { x: 130, y: 50, angle: 0 }, // over the label text
        { x: 400, y: 50, angle: 0 }, // past it
        { x: 130, y: 120, angle: 0 }, // below it
      ],
      [box],
    );
    expect(kept.map((a) => [a.x, a.y])).toEqual([
      [400, 50],
      [130, 120],
    ]);
  });

  test("back links never reach d3-sankey: a cycle lays out, with one loop per back link", () => {
    const g = layoutSankey(NODES, LINKS, { width: 1000, height: 400 });
    expect(g.links.filter((l) => l.back).map((l) => `${l.source}>${l.target}`).sort()).toEqual([
      "Assets:Broker:Synth>Assets:Checking:Synth",
      "Expenses:Refundy>Assets:Checking:Synth",
    ]);
    expect(g.links.filter((l) => !l.back)).toHaveLength(4);
    // Loops run under the diagram, inside the height budget.
    const bottom = Math.max(...g.nodes.map((n) => n.y1));
    for (const l of g.links.filter((x) => x.back)) expect(lane(l.path)).toBeGreaterThan(bottom);
    expect(g.height).toBeLessThanOrEqual(400);
  });

  test("the account band is ordered by flow: bank → broker runs rightward", () => {
    const fwd = LINKS.filter((l) => !l.back).map((l) => ({ ...l, value: Number(l.value) }));
    const col = columnsOf(NODES, fwd);
    expect(col.get("Assets:Broker:Synth")!).toBeGreaterThan(col.get("Assets:Checking:Synth")!);
    expect(col.get("Expenses:Food")!).toBeGreaterThan(col.get("Assets:Broker:Synth")!);
    const g = layoutSankey(NODES, LINKS, { width: 1000, height: 400 });
    const x = new Map(g.nodes.map((n) => [n.id, n.x0]));
    expect(x.get("Assets:Broker:Synth")!).toBeGreaterThan(x.get("Assets:Checking:Synth")!);
  });

  test("a negative node amount never becomes a width: heights come from link values", () => {
    const g = layoutSankey(NODES, LINKS, { width: 1000, height: 400 });
    for (const n of g.nodes) {
      expect(Number.isFinite(n.y1 - n.y0)).toBe(true);
      expect(n.y1 - n.y0).toBeGreaterThan(0);
    }
    const refundy = g.nodes.find((n) => n.id === "Expenses:Refundy")!;
    const food = g.nodes.find((n) => n.id === "Expenses:Food")!;
    // 300 vs 9000 of link value: the refund-only category is 1/30 of Food.
    expect((refundy.y1 - refundy.y0) / (food.y1 - food.y0)).toBeCloseTo(300 / 9000, 5);
  });

  test("loops are stacked by width, smallest innermost, in separate lanes", () => {
    const g = layoutSankey(NODES, LINKS, { width: 1000, height: 400 });
    const loops = g.links.filter((l) => l.back);
    expect(loops[0].value).toBeLessThan(loops[1].value);
    expect(loops[0].width).toBeLessThan(loops[1].width);
    // the second loop's lane sits below the first one's, and the strokes never overlap
    expect(lane(loops[1].path) - lane(loops[0].path)).toBeGreaterThanOrEqual((loops[0].width + loops[1].width) / 2);
    // each flow carries direction arrows
    expect(g.links.every((l) => l.arrows.length > 0)).toBe(true);
  });
});

describe("Sankey", () => {
  test("draws forward ribbons and back loops, and a click picks the node or link", () => {
    const onPick = vi.fn();
    const { container } = render(
      <TooltipProvider>
        <Sankey nodes={NODES} links={LINKS} onPick={onPick} />
      </TooltipProvider>,
    );
    expect(container.querySelectorAll("path.sk-link")).toHaveLength(4);
    expect(container.querySelectorAll("path.sk-back")).toHaveLength(2);
    fireEvent.click(container.querySelector('path.sk-back[data-source="Expenses:Refundy"]')!);
    expect(onPick).toHaveBeenLastCalledWith({ kind: "link", source: "Expenses:Refundy", target: "Assets:Checking:Synth" });
    fireEvent.click(container.querySelector('.sk-node[data-id="Expenses:Food"] rect')!);
    expect(onPick).toHaveBeenLastCalledWith({ kind: "node", id: "Expenses:Food", label: "Food" });
  });

  test("colours by kind: income green, asset blue, liability amber, expense red; a loop takes its source's", () => {
    const { container } = render(
      <TooltipProvider>
        <Sankey nodes={NODES} links={LINKS} />
      </TooltipProvider>,
    );
    const fill = (id: string) => container.querySelector(`.sk-node[data-id="${id}"] rect`)!.getAttribute("fill");
    expect(fill("Income:Salary")).toBe("var(--green)");
    expect(fill("Assets:Checking:Synth")).toBe("var(--blue)");
    expect(fill("Liabilities:CreditCard:Acme")).toBe("var(--amber)");
    expect(fill("Expenses:Food")).toBe("var(--red)");
    expect(container.querySelector('path.sk-back[data-source="Expenses:Refundy"]')!.getAttribute("stroke")).toBe("var(--red)");
  });

  test("the drawing stays within the viewport cap and follows a window resize", () => {
    const vh = window.innerHeight;
    try {
      window.innerHeight = 600;
      const { container } = render(
        <TooltipProvider>
          <Sankey nodes={BIG_NODES} links={BIG_LINKS} />
        </TooltipProvider>,
      );
      const height = () => Number(container.querySelector("svg")!.getAttribute("viewBox")!.split(" ")[3]);
      expect(height()).toBeLessThanOrEqual(sankeyHeightCap(600, 13) + 1e-6);
      act(() => {
        window.innerHeight = 1200;
        window.dispatchEvent(new Event("resize"));
      });
      expect(height()).toBeGreaterThan(sankeyHeightCap(600, 13));
      expect(height()).toBeLessThanOrEqual(sankeyHeightCap(1200, 13) + 1e-6);
    } finally {
      window.innerHeight = vh;
    }
  });

  test("a capped loop is marked and its hover keeps the true Total", () => {
    const { container } = render(
      <TooltipProvider>
        <Sankey nodes={BIG_NODES} links={BIG_LINKS} />
      </TooltipProvider>,
    );
    const loop = container.querySelector("path.sk-back")!;
    expect(loop).toHaveAttribute("data-capped", "true");
    fireEvent.mouseEnter(loop);
    const tip = document.querySelector(".tip") as HTMLElement;
    expect(within(tip).getByText("Total").nextSibling).toHaveTextContent("₹1,50,000");
    expect(within(tip).getByText("Drawn")).toBeInTheDocument();
  });

  test("nodes and flows are keyboard-reachable: Enter or Space picks them", () => {
    const onPick = vi.fn();
    const { container } = render(
      <TooltipProvider>
        <Sankey nodes={NODES} links={LINKS} onPick={onPick} />
      </TooltipProvider>,
    );
    const flow = container.querySelector('path.sk-link[data-target="Assets:Broker:Synth"]') as SVGElement;
    expect(flow).toHaveAttribute("tabindex", "0");
    fireEvent.keyDown(flow, { key: "Enter" });
    expect(onPick).toHaveBeenLastCalledWith({ kind: "link", source: "Assets:Checking:Synth", target: "Assets:Broker:Synth" });
    const rect = container.querySelector('.sk-node[data-id="Expenses:Food"] rect') as SVGElement;
    expect(rect).toHaveAttribute("tabindex", "0");
    fireEvent.keyDown(rect, { key: " " });
    expect(onPick).toHaveBeenLastCalledWith({ kind: "node", id: "Expenses:Food", label: "Food" });
  });

  test("no arrowhead is drawn over a node label", () => {
    const long = "Salary from Synthetic Employer Private Limited";
    const nodes: SkNode[] = [
      node("Income:Salary:Synth", "payer", "income", "100000.00", "Synth"),
      node("Income:Salary", "income", "income", "100000.00", long),
      node("Assets:Checking:Synth", "account", "asset", "100000.00"),
      node("Expenses:Rent", "expense", "expense", "30000.00"),
    ];
    const links: SkLink[] = [
      link("Income:Salary:Synth", "Income:Salary", "100000.00"),
      link("Income:Salary", "Assets:Checking:Synth", "100000.00"),
      link("Assets:Checking:Synth", "Expenses:Rent", "30000.00"),
    ];
    const { container } = render(
      <TooltipProvider>
        <Sankey nodes={nodes} links={links} />
      </TooltipProvider>,
    );
    const height = Number(container.querySelector("svg")!.getAttribute("viewBox")!.split(" ")[3]);
    const g = layoutSankey(nodes, links, { width: 1000, height, u: 1 });
    const salary = g.nodes.find((n) => n.id === "Income:Salary")!;
    const box = labelBox(salary, `${long} ₹1L`, 1);
    const all = g.links.flatMap((l) => l.arrows);
    expect(all.some((a) => a.x >= box.x0 && a.x <= box.x1 && a.y >= box.y0 && a.y <= box.y1)).toBe(true);
    const drawn = [...container.querySelectorAll(".sk-arrows path")].map((p) => {
      const m = /translate\(([-\d.e]+),([-\d.e]+)\)/.exec(p.getAttribute("transform")!)!;
      return { x: Number(m[1]), y: Number(m[2]) };
    });
    expect(drawn.length).toBeLessThan(all.length);
    expect(drawn.some((a) => a.x >= box.x0 && a.x <= box.x1 && a.y >= box.y0 && a.y <= box.y1)).toBe(false);
  });

  test("a flow's hover shows From (credit) / To (debit) / Total", () => {
    const { container } = render(
      <TooltipProvider>
        <Sankey nodes={NODES} links={LINKS} />
      </TooltipProvider>,
    );
    fireEvent.mouseEnter(container.querySelector('path.sk-link[data-target="Assets:Broker:Synth"]')!);
    const tip = document.querySelector(".tip") as HTMLElement;
    expect(within(tip).getByText("From (credit)").nextSibling).toHaveTextContent("Assets:Checking:Synth");
    expect(within(tip).getByText("To (debit)").nextSibling).toHaveTextContent("Assets:Broker:Synth");
    expect(within(tip).getByText("Total").nextSibling).toHaveTextContent("₹40,000");
  });
});

describe("compressed (√) scale", () => {
  test("ticks drop any step within 16px of the previous kept tick", () => {
    // 200px of axis for 0..₹1L on √: 500 → 14px (dropped), 2.5K → 31.6px, 10K → 63px,
    // 25K → 100px, 50K → 141px, 1L → 200px.
    const y = (v: number) => 200 - (transform("sqrt")(v) / Math.sqrt(100_000)) * 200;
    const ticks = sqrtTicks(100_000, y, 16);
    expect(ticks).toEqual([0, 2_500, 10_000, 25_000, 50_000, 100_000]);
    for (let i = 1; i < ticks.length; i++) expect(y(ticks[i - 1]) - y(ticks[i])).toBeGreaterThanOrEqual(16);
  });

  test("niceMax and compact labels", () => {
    expect(niceMax(83_000)).toBe(100_000);
    expect(niceMax(36_000)).toBe(50_000);
    expect(compactK(4_200)).toBe("4.2K");
    expect(compactK(36_000)).toBe("36K");
    expect(compactK(268_000)).toBe("2.7L");
  });

  test("bars in Compressed mode carry the √ scale tag; Linear does not", () => {
    const props = {
      points: [
        { key: "2026-08-01", label: "1" },
        { key: "2026-08-02", label: "2" },
      ],
      series: [{ id: "x", label: "Spent", color: "var(--red)", values: [82_700, 1_200] }],
      ariaLabel: "spend per day",
      today: "2026-12-31",
    };
    const { rerender, container } = render(
      <TooltipProvider>
        <PeriodBars {...props} scale="sqrt" />
      </TooltipProvider>,
    );
    expect(screen.getByText("√ scale")).toBeInTheDocument();
    expect(container.querySelector("svg")).toHaveAttribute("aria-label", "spend per day, square-root scale");
    rerender(
      <TooltipProvider>
        <PeriodBars {...props} scale="linear" />
      </TooltipProvider>,
    );
    expect(screen.queryByText("√ scale")).toBeNull();
  });
});

test("a bar group is keyboard-reachable: Enter picks it", () => {
  const onPick = vi.fn();
  const { container } = render(
    <TooltipProvider>
      <PeriodBars
        points={[
          { key: "2026-08-01", label: "1" },
          { key: "2026-08-02", label: "2" },
        ]}
        series={[{ id: "x", label: "Spent", color: "var(--red)", values: [500, 1_200] }]}
        scale="linear"
        ariaLabel="spend per day"
        today="2026-12-31"
        onPick={onPick}
      />
    </TooltipProvider>,
  );
  const hit = container.querySelector('.pbars-hit[data-key="2026-08-02"]') as SVGElement;
  expect(hit).toHaveAttribute("tabindex", "0");
  expect(hit).toHaveAttribute("aria-label", "2: Spent ₹1,200");
  fireEvent.keyDown(hit, { key: "Enter" });
  expect(onPick).toHaveBeenCalledWith(1);
});

describe("PeriodHeatmap", () => {
  const cells = [
    { key: "2026-08-03", label: "3 Aug", value: 412 },
    { key: "2026-08-09", label: "9 Aug", value: -5000 },
  ];

  test("no minus sign on a refund cell: the colour carries direction", () => {
    render(
      <TooltipProvider>
        <PeriodHeatmap granularity="day" from="2026-08-01" to="2026-08-31" cells={cells} positive="out" today="2026-12-31" />
      </TooltipProvider>,
    );
    const refund = screen.getByRole("button", { name: /^9 Aug 2026/ });
    expect(refund).toHaveTextContent("5K");
    expect(refund.textContent).not.toMatch(/[-−]/);
    expect(refund).toHaveAttribute("data-dir", "in");
    expect(screen.getByRole("button", { name: /^3 Aug 2026/ })).toHaveAttribute("data-dir", "out");
  });

  test("a month fills a 7-column grid with leading blanks; future days are dashed and inert", () => {
    const { container } = render(
      <TooltipProvider>
        <PeriodHeatmap granularity="day" from="2026-08-01" to="2026-08-31" cells={cells} positive="out" today="2026-08-20" />
      </TooltipProvider>,
    );
    // 1 Aug 2026 is a Saturday: six leading blanks.
    expect(container.querySelectorAll(".hm-day.blank")).toHaveLength(6);
    expect(container.querySelectorAll(".hm-day.future")).toHaveLength(11);
    expect(container.querySelectorAll("button.hm-day")).toHaveLength(20);
  });

  test("a click picks the cell", () => {
    const onPick = vi.fn();
    render(
      <TooltipProvider>
        <PeriodHeatmap granularity="day" from="2026-08-01" to="2026-08-31" cells={cells} positive="out" today="2026-12-31" onPick={onPick} />
      </TooltipProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: /^3 Aug 2026/ }));
    expect(onPick).toHaveBeenCalledWith(cells[0]);
  });
});

function Prefs() {
  const p = useChartPrefs("test");
  return <ChartSwitches {...p} />;
}

describe("remembered switches", () => {
  test("Heatmap | Bars and Linear | Compressed survive a reload", () => {
    const first = render(<Prefs />);
    fireEvent.click(screen.getByRole("button", { name: "Bars" }));
    fireEvent.click(screen.getByRole("button", { name: "Linear" }));
    first.unmount();
    render(<Prefs />);
    expect(screen.getByRole("button", { name: "Bars" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Linear" })).toHaveAttribute("aria-pressed", "true");
  });

  test("the defaults are Heatmap and Compressed; the scale switch shows only in Bars", () => {
    render(<Prefs />);
    expect(screen.getByRole("button", { name: "Heatmap" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("button", { name: "Compressed" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Bars" }));
    expect(screen.getByRole("button", { name: "Compressed" })).toHaveAttribute("aria-pressed", "true");
  });

  test("a throwing localStorage falls back to the defaults and still switches", () => {
    // Replace the whole object: the runtime's localStorage may not be a jsdom Storage.
    const deny = () => {
      throw new Error("SecurityError");
    };
    vi.stubGlobal("localStorage", { getItem: deny, setItem: deny, removeItem: deny, clear: deny, key: deny, length: 0 });
    render(<Prefs />);
    expect(screen.getByRole("button", { name: "Heatmap" })).toHaveAttribute("aria-pressed", "true");
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "Bars" }));
    });
    expect(screen.getByRole("button", { name: "Bars" })).toHaveAttribute("aria-pressed", "true");
  });
});
