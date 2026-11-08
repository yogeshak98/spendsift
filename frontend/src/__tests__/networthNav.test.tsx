import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect } from "vitest";
import { NAV } from "@/nav";
import { Rail } from "@/shell/Rail";
import { Breadcrumb } from "@/components/Breadcrumb";

describe("net worth nav", () => {
  // "Data freshness" was retired as a screen: a status that needs its own
  // screen is a status nobody checks. Its content is now the dropdown on the
  // refresh control, beside the "Prices as of ..." date it explains.
  // Net Worth is one screen with its own tabs, so the rail item has no
  // sub-items; Liabilities stays as a shortcut to its Liabilities tab.
  it("has no sub-items for Net Worth or Liabilities", () => {
    const networth = NAV.find((d) => d.id === "networth");
    expect(networth).toBeTruthy();
    expect(networth!.label).toBe("Net Worth");
    expect(networth!.subs).toEqual([]);
    expect(NAV.find((d) => d.id === "liabilities")!.subs).toEqual([]);
  });

  it("the rail links point at the Net Worth and Liabilities shortcuts", () => {
    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <Rail />
      </MemoryRouter>,
    );
    expect(screen.getByText("Net Worth").closest("a")).toHaveAttribute("href", "/networth");
    expect(screen.getByText("Liabilities").closest("a")).toHaveAttribute("href", "/liabilities");
  });

  it("no longer exposes a Gains & Returns or Stocks item anywhere in NAV", () => {
    const allSubLabels = NAV.flatMap((d) => d.subs.map((s) => s.label));
    const allSubIds = NAV.flatMap((d) => d.subs.map((s) => s.id));
    expect(allSubLabels).not.toContain("Gains & Returns");
    expect(allSubLabels).not.toContain("Stocks");
    expect(allSubIds).not.toContain("gains");
    expect(allSubIds).not.toContain("stocks");
    expect(NAV.find((d) => d.id === "assets")).toBeUndefined();
  });

  it("keeps Net Worth highlighted on its other tabs and drill-downs", () => {
    for (const path of [
      "/networth/overview",
      "/networth/allocation",
      "/networth/investments/stock/SBIN",
      "/networth/investments/mutual_fund/parag-parikh-flexi-cap",
    ]) {
      const r = render(
        <MemoryRouter initialEntries={[path]}>
          <Rail />
        </MemoryRouter>,
      );
      expect(r.getByText("Net Worth").closest("a")).toHaveClass("active");
      expect(r.getByText("Liabilities").closest("a")).not.toHaveClass("active");
      r.unmount();
    }
  });

  it("lights Liabilities, not Net Worth, on the Liabilities tab", () => {
    for (const path of ["/networth/liabilities", "/networth/liabilities/repayment"]) {
      const r = render(
        <MemoryRouter initialEntries={[path]}>
          <Rail />
        </MemoryRouter>,
      );
      expect(r.getByText("Liabilities").closest("a")).toHaveClass("active");
      expect(r.getByText("Net Worth").closest("a")).not.toHaveClass("active");
      r.unmount();
    }
  });
});

describe("Breadcrumb", () => {
  it("renders ancestors as links and the leaf as plain text", () => {
    render(
      <MemoryRouter>
        <Breadcrumb
          items={[
            { label: "Investments", to: "/networth/investments" },
            { label: "Stock", to: "/networth/investments/stock" },
            { label: "SBIN" },
          ]}
        />
      </MemoryRouter>,
    );
    const investmentsLink = screen.getByText("Investments");
    expect(investmentsLink.tagName).toBe("A");
    expect(investmentsLink).toHaveAttribute("href", "/networth/investments");

    const stockLink = screen.getByText("Stock");
    expect(stockLink.tagName).toBe("A");

    const leaf = screen.getByText("SBIN");
    expect(leaf.tagName).not.toBe("A");
  });
});
