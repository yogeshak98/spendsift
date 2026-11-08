import { fireEvent, render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { DataTable, type Cell, type Column } from "@/components/DataTable";
import { cell } from "@/components/cell";

// Sorting exists because these tables are read to answer comparative questions
// -- "which fund is dragging me down", "what did I pay most for". The trap the
// whole design avoids is sorting the DISPLAY string: every money and percent
// cell is a formatted Decimal ("₹80,000.00", "-3.00%", "—"), and ordering those
// as text is silently, plausibly wrong.

function bodyColumn(index: number): string[] {
  return Array.from(document.querySelectorAll("tbody tr")).map(
    (tr) => (tr.children[index].textContent ?? "").trim(),
  );
}

const COLUMNS: Column[] = [
  { key: "name", label: "Fund" },
  { key: "value", label: "Value", num: true },
];

// Deliberately built so that text order and numeric order disagree: as text,
// "₹9,000.00" sorts after "₹80,000.00" because "9" > "8".
const ROWS: Record<string, Cell>[] = [
  { name: "Beta", value: cell(80000, "₹80,000.00") },
  { name: "Alpha", value: cell(9000, "₹9,000.00") },
  { name: "Gamma", value: cell(-2500, "-₹2,500.00") },
];

describe("DataTable sorting", () => {
  it("leaves rows in the caller's order until the user asks for a sort", () => {
    render(<DataTable columns={COLUMNS} rows={ROWS} />);
    // The caller's order is meaningful everywhere this table is used (holdings
    // by value, repayments by date). Sorting is opt-in, not a default reorder.
    expect(bodyColumn(0)).toEqual(["Beta", "Alpha", "Gamma"]);
  });

  it("orders money by its value, not by the text of the formatted rupee string", () => {
    render(<DataTable columns={COLUMNS} rows={ROWS} />);
    fireEvent.click(screen.getByRole("button", { name: /Value/ }));
    expect(bodyColumn(1)).toEqual(["-₹2,500.00", "₹9,000.00", "₹80,000.00"]);
  });

  it("cycles a column ascending, descending, then back to the caller's order", () => {
    render(<DataTable columns={COLUMNS} rows={ROWS} />);
    const header = screen.getByRole("button", { name: /Value/ });

    fireEvent.click(header);
    expect(bodyColumn(0)).toEqual(["Gamma", "Alpha", "Beta"]);

    fireEvent.click(header);
    expect(bodyColumn(0)).toEqual(["Beta", "Alpha", "Gamma"]);

    // A third click is an escape hatch back to the unsorted view, so the user
    // is never stuck having destroyed the order the screen chose.
    fireEvent.click(header);
    expect(bodyColumn(0)).toEqual(["Beta", "Alpha", "Gamma"]);
    expect(screen.getByRole("columnheader", { name: /Value/ })).not.toHaveAttribute("aria-sort");
  });

  it("announces the active sort direction, not just the arrow glyph", () => {
    render(<DataTable columns={COLUMNS} rows={ROWS} />);
    const header = screen.getByRole("button", { name: /Value/ });

    fireEvent.click(header);
    expect(screen.getByRole("columnheader", { name: /Value/ })).toHaveAttribute(
      "aria-sort",
      "ascending",
    );
    fireEvent.click(header);
    expect(screen.getByRole("columnheader", { name: /Value/ })).toHaveAttribute(
      "aria-sort",
      "descending",
    );
  });

  it("sorts plain string columns without the caller supplying a sort value", () => {
    // The eight existing call sites pass bare strings. They gain sorting with
    // no edit; only columns whose cells are formatted numbers need `cell()`.
    render(<DataTable columns={COLUMNS} rows={ROWS} />);
    fireEvent.click(screen.getByRole("button", { name: /Fund/ }));
    expect(bodyColumn(0)).toEqual(["Alpha", "Beta", "Gamma"]);
  });

  it("keeps unmeasurable rows at the bottom in BOTH directions", () => {
    // A fund with no XIRR is not the worst performer -- it is unmeasured.
    // Sinking it in one direction and floating it to the top in the other
    // would make the flipped view read as a ranking of the unknowable.
    const rows: Record<string, Cell>[] = [
      { name: "Measured", value: cell(5, "5.00%") },
      { name: "Unknown", value: cell(null, "—") },
      { name: "Lowest", value: cell(-9, "-9.00%") },
    ];
    render(<DataTable columns={COLUMNS} rows={rows} />);
    const header = screen.getByRole("button", { name: /Value/ });

    fireEvent.click(header);
    expect(bodyColumn(0)).toEqual(["Lowest", "Measured", "Unknown"]);

    fireEvent.click(header);
    expect(bodyColumn(0)).toEqual(["Measured", "Lowest", "Unknown"]);
  });

  it("does not offer sorting on a column whose cells carry no orderable value", () => {
    // A column that sorts only some of its rows is worse than one that does
    // not sort at all -- the rows it cannot place would move arbitrarily.
    const rows: Record<string, Cell>[] = [
      { name: "Alpha", value: <em>rich content, no sort value</em> },
      { name: "Beta", value: cell(1, "1") },
    ];
    render(<DataTable columns={COLUMNS} rows={rows} />);
    expect(screen.getByRole("button", { name: /Fund/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Value/ })).not.toBeInTheDocument();
  });

  it("honours an explicit sortable: false", () => {
    render(
      <DataTable
        columns={[{ key: "name", label: "Fund", sortable: false }, COLUMNS[1]]}
        rows={ROWS}
      />,
    );
    expect(screen.queryByRole("button", { name: /Fund/ })).not.toBeInTheDocument();
  });

  it("renders no sort affordance on an empty table", () => {
    render(<DataTable columns={COLUMNS} rows={[]} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
