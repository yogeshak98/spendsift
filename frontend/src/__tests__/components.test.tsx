import { render, screen } from "@testing-library/react";
import { StatBlock } from "@/components/StatBlock";
import { ProgressTrack } from "@/components/ProgressTrack";

test("StatBlock renders label, value, delta", () => {
  render(<StatBlock label="Spent" value="₹1,42,350" delta="▲ 8.2%" tone="red" />);
  expect(screen.getByText("Spent")).toBeInTheDocument();
  expect(screen.getByText("₹1,42,350")).toBeInTheDocument();
  expect(screen.getByText("▲ 8.2%")).toBeInTheDocument();
});

test("ProgressTrack clamps fill width to 100%", () => {
  const { container } = render(<ProgressTrack value={150} max={100} tone="red" />);
  const fill = container.querySelector(".fill") as HTMLElement;
  expect(fill.style.width).toBe("100%");
});

test("istep renders with mk and tx children", () => {
  render(
    <button className="istep active">
      <span className="mk">1</span>
      <span className="tx">Upload</span>
    </button>
  );
  expect(screen.getByText("Upload").className).toContain("tx");
  expect(screen.getByText("1").className).toContain("mk");
});

test("bal-chip neg class applies", () => {
  render(<span className="bal-chip neg">₹-100</span>);
  expect(screen.getByText("₹-100").className).toContain("neg");
});
