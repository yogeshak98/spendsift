import { render, screen, fireEvent } from "@testing-library/react";
import { test, expect, vi } from "vitest";
import { WillLearn, REASON_VETO, type ReviewLearning } from "@/screens/ledger/import/WillLearn";
import learningFixture from "@/__tests__/fixtures/importReview/review-learning.json";
import learningEmpty from "@/__tests__/fixtures/importReview/review-learning-empty.json";

test("lists each pattern with its category and a don't-learn control", async () => {
  const onSetLearn = vi.fn().mockResolvedValue(undefined);
  render(
    <WillLearn
      learning={{
        will_learn: [
          { pattern: "FICTIVEBIKES", category: "Expenses:Travel", candidate_ids: ["c1"], replaces: "Expenses:Travel:Bike" },
        ],
        not_learned: [{ candidate_id: "c2", pattern: "OFFUS EMI", reason: "only generic words" }],
        would_open: ["Expenses:Subscriptions"],
      }}
      rows={[]}
      onSetLearn={onSetLearn}
    />,
  );
  expect(screen.getByText(/FICTIVEBIKES/)).toBeInTheDocument();
  expect(screen.getByText(/was Expenses:Travel:Bike/)).toBeInTheDocument();
  expect(screen.getByText(/only generic words/)).toBeInTheDocument();
  expect(screen.getByText(/Expenses:Subscriptions/)).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("don't learn FICTIVEBIKES"));
  expect(onSetLearn).toHaveBeenCalledWith(["c1"], false);
});

test("a vetoed row offers 'learn it' and a generic row does not", () => {
  const onSetLearn = vi.fn().mockResolvedValue(undefined);
  render(<WillLearn learning={learningFixture as ReviewLearning} rows={[]} onSetLearn={onSetLearn} />);
  expect(REASON_VETO).toBe("you said don't learn this");
  const buttons = screen.getAllByRole("button", { name: "learn it" });
  expect(buttons).toHaveLength(1);
  fireEvent.click(buttons[0]);
  expect(onSetLearn).toHaveBeenCalledWith(["295609f0a6cb54a0a8c1453db2727748"], true);
  expect(screen.getByText(/New accounts: Expenses:Food:Cafe, Expenses:Household:Appliances, Expenses:Subscriptions/)).toBeInTheDocument();
});

test("renders nothing when there is nothing to say", () => {
  const { container } = render(<WillLearn learning={learningEmpty as ReviewLearning} rows={[]} onSetLearn={vi.fn()} />);
  expect(container).toBeEmptyDOMElement();
});

test("a pattern set to different categories shows the conflict warning", () => {
  render(
    <WillLearn
      learning={{
        will_learn: [
          {
            pattern: "FICTIVEBIKES",
            category: "Expenses:Travel",
            candidate_ids: ["c1", "c2"],
            replaces: null,
            conflicting: { "Expenses:Travel:Bike": 1, "Expenses:Travel": 1 },
            warning:
              "conflicting categories for pattern FICTIVEBIKES: Expenses:Travel:Bike (1 row), Expenses:Travel (1 row); the rule will use Expenses:Travel",
          },
        ],
        not_learned: [],
        would_open: [],
      }}
      rows={[]}
      onSetLearn={vi.fn()}
    />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent(/conflicting categories for pattern FICTIVEBIKES/);
});
