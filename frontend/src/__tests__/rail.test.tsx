import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { Rail } from "@/shell/Rail";

test("rail lists domains and reveals sub-views for the active one", () => {
  render(
    <MemoryRouter initialEntries={["/cashflow/overview"]}>
      <Rail />
    </MemoryRouter>,
  );
  expect(screen.getByText("Dashboard")).toBeInTheDocument();
  expect(screen.getByText("Cash Flow")).toBeInTheDocument();
  // active domain shows its sub-views
  expect(screen.getByText("Recurring")).toBeInTheDocument();
  expect(screen.queryByText("Yearly")).not.toBeInTheDocument();
  // inactive domain's sub-views are not shown
  expect(screen.queryByText("Credit Cards")).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("Liabilities"));
});

test("the Expense rail order is Overview, By Tag, Budget, Uncategorized", () => {
  const { container } = render(
    <MemoryRouter initialEntries={["/expense/overview"]}>
      <Rail />
    </MemoryRouter>,
  );
  const subs = [...container.querySelectorAll(".nav-sub .nav-subitem")];
  expect(subs.map((a) => a.textContent)).toEqual(["Overview", "By Tag", "Budget", "Uncategorized"]);
  expect(subs.map((a) => a.getAttribute("href"))).toEqual([
    "/expense/overview",
    "/expense/tags",
    "/expense/budget",
    "/expense/uncategorized",
  ]);
  // The domain link lands on the first sub, Overview.
  expect(screen.getByText("Expense").closest("a")).toHaveAttribute("href", "/expense/overview");
  expect(subs[0]).toHaveClass("active");
});

test("the Income rail shows Overview and By Tag", () => {
  const { container } = render(
    <MemoryRouter initialEntries={["/income/overview"]}>
      <Rail />
    </MemoryRouter>,
  );
  const subs = [...container.querySelectorAll(".nav-sub .nav-subitem")];
  expect(subs.map((a) => a.textContent)).toEqual(["Overview", "By Tag"]);
  expect(subs.map((a) => a.getAttribute("href"))).toEqual(["/income/overview", "/income/tags"]);
  expect(screen.getByText("Income").closest("a")).toHaveAttribute("href", "/income/overview");
});
