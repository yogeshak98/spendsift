import { render, screen, fireEvent } from "@testing-library/react";
import { TooltipProvider, Tip } from "@/shared/theme/tooltip";

test("shows tip content on hover", () => {
  render(
    <TooltipProvider>
      <Tip content={<div>Income 2,80,000</div>}>
        <span>hover me</span>
      </Tip>
    </TooltipProvider>,
  );
  fireEvent.mouseEnter(screen.getByText("hover me"));
  expect(screen.getByText("Income 2,80,000")).toBeVisible();
  fireEvent.mouseLeave(screen.getByText("hover me"));
});
