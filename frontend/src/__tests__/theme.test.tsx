import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeSwitcher } from "@/shared/theme/theme";
import { vi } from "vitest";

test("switcher sets data-theme and persists", () => {
  render(<ThemeSwitcher />);
  const select = screen.getByLabelText("Theme") as HTMLSelectElement;
  fireEvent.change(select, { target: { value: "carbon" } });
  expect(document.body.getAttribute("data-theme")).toBe("carbon");
  expect(localStorage.getItem("ss-theme")).toBe("carbon");
});

test("storage that throws falls back to Midnight and still switches", () => {
  const boom = () => {
    throw new Error("storage blocked");
  };
  const get = vi.spyOn(localStorage, "getItem").mockImplementation(boom);
  const set = vi.spyOn(localStorage, "setItem").mockImplementation(boom);
  try {
    render(<ThemeSwitcher />);
    const select = screen.getByLabelText("Theme") as HTMLSelectElement;
    expect(select.value).toBe("midnight");
    fireEvent.change(select, { target: { value: "daylight" } });
    expect(document.body.getAttribute("data-theme")).toBe("daylight");
  } finally {
    get.mockRestore();
    set.mockRestore();
  }
});
