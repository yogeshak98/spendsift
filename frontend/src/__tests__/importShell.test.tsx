import { render, screen, fireEvent } from "@testing-library/react";
import { describe, test, expect, vi } from "vitest";
import { ImportShell } from "@/screens/ledger/import/ImportShell";

describe("ImportShell", () => {
  test("renders Statement source with 4 steps by default (Upload/Map/Review/Commit)", () => {
    render(
      <ImportShell renderStep={(src, stp) => <div data-testid="body">{src}/{stp}</div>} />
    );
    expect(screen.getByText("Upload")).toBeInTheDocument();
    expect(screen.getByText("Map")).toBeInTheDocument();
    expect(screen.getByText("Review")).toBeInTheDocument();
    expect(screen.getByText("Commit")).toBeInTheDocument();
    // First step is active
    expect(screen.getByTestId("body").textContent).toBe("statement/upload");
  });

  test("switching to Investment shows 3 steps, no Map, resets to inv-upload", () => {
    render(
      <ImportShell renderStep={(src, stp) => <div data-testid="body">{src}/{stp}</div>} />
    );
    fireEvent.click(screen.getByRole("button", { name: /investment/i }));
    expect(screen.queryByText("Map")).not.toBeInTheDocument();
    // Investment has Upload + Review + Commit but no Map
    const steps = screen.getAllByRole("button").filter(
      (b) => ["Upload", "Review", "Commit"].includes(b.textContent?.trim() ?? "")
    );
    expect(steps).toHaveLength(3);
    expect(screen.getByTestId("body").textContent).toBe("investment/inv-upload");
  });

  test("clicking a step navigates to it", () => {
    render(
      <ImportShell renderStep={(src, stp) => <div data-testid="body">{src}/{stp}</div>} />
    );
    // Click the Review step button in the rail
    const reviewBtn = screen.getAllByRole("button").find(
      (b) => b.textContent?.trim() === "Review"
    )!;
    fireEvent.click(reviewBtn);
    expect(screen.getByTestId("body").textContent).toBe("statement/review");
  });

  test("switching source resets step to that source's first step", () => {
    render(
      <ImportShell renderStep={(src, stp) => <div data-testid="body">{src}/{stp}</div>} />
    );
    // Navigate to Review first
    const reviewBtn = screen.getAllByRole("button").find(
      (b) => b.textContent?.trim() === "Review"
    )!;
    fireEvent.click(reviewBtn);
    expect(screen.getByTestId("body").textContent).toBe("statement/review");
    // Now switch to Investment — must reset to inv-upload
    fireEvent.click(screen.getByRole("button", { name: /investment/i }));
    expect(screen.getByTestId("body").textContent).toBe("investment/inv-upload");
  });

  test("Statement source button has 'on' class when active", () => {
    render(
      <ImportShell renderStep={() => null} />
    );
    const stmtBtn = screen.getByRole("button", { name: /statement/i });
    expect(stmtBtn.className).toContain("on");
  });

  test("onSourceChange fires when the source switches", () => {
    const onSourceChange = vi.fn();
    render(<ImportShell renderStep={() => null} onSourceChange={onSourceChange} />);
    fireEvent.click(screen.getByRole("button", { name: /investment/i }));
    expect(onSourceChange).toHaveBeenCalledWith("investment");
    fireEvent.click(screen.getByRole("button", { name: /statement/i }));
    expect(onSourceChange).toHaveBeenCalledWith("statement");
  });
});

describe("ImportShell opening step", () => {
  test("opening step only listed when showOpening", () => {
    const body = (src: string, stp: string) => <div data-testid="body">{src}/{stp}</div>;
    const { rerender } = render(<ImportShell renderStep={body} />);
    expect(screen.queryByText("Opening")).not.toBeInTheDocument();
    rerender(<ImportShell renderStep={body} showOpening />);
    const labels = screen.getAllByRole("button").map((b) => b.textContent?.trim());
    const i = labels.indexOf("Review");
    expect(labels.slice(i, i + 3)).toEqual(["Review", "Opening", "Commit"]);
  });
});
