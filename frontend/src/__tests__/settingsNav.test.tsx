import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect } from "vitest";
import { NAV } from "@/nav";
import { Rail } from "@/shell/Rail";

describe("settings nav", () => {
  it("settings lists Opening balances", () => {
    const settings = NAV.find((d) => d.id === "settings");
    expect(settings).toBeTruthy();
    expect(settings!.subs.map((s) => s.id)).toEqual(["accounts", "templates", "openings"]);
    expect(settings!.subs.find((s) => s.id === "openings")?.label).toBe("Opening balances");
    expect(NAV[NAV.length - 1].id).toBe("settings");
  });

  it("renders Settings link in the rail", () => {
    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <Rail />
      </MemoryRouter>,
    );
    expect(screen.getByText("Settings")).toBeInTheDocument();
  });
});
