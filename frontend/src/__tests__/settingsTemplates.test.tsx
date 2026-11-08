import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, it, expect, vi } from "vitest";
import { SettingsTemplates } from "@/screens/settings/Templates";

afterEach(() => vi.restoreAllMocks());

const TMPL = {
  fingerprint: "fp1",
  institution: "HDFC",
  header_row_index: 0,
  date_column: "Date",
  date_format: "%d/%m/%Y",
  narration_column: "Narration",
  delimiter: ",",
  debit_column: null,
  credit_column: null,
  amount_column: null,
  sign_column: null,
  credit_indicators: ["Cr"],
  account_marker_pattern: null,
  account_marker_map: {},
  default_account: "Assets:Checking:HDFC",
};

function mockFetch(bodies: unknown[]) {
  let i = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      const body = bodies[Math.min(i, bodies.length - 1)];
      i++;
      return {
        ok: true,
        status: 200,
        json: async () => body,
        text: async () => JSON.stringify(body),
      };
    }),
  );
}

function wrap(ui: React.ReactElement) {
  return render(<MemoryRouter initialEntries={["/settings/templates"]}>{ui}</MemoryRouter>);
}

describe("SettingsTemplates", () => {
  it("renders template cards", async () => {
    mockFetch([{ templates: [TMPL] }]);
    wrap(<SettingsTemplates />);
    expect(await screen.findByText("HDFC")).toBeInTheDocument();
    expect(screen.getByText("Assets:Checking:HDFC")).toBeInTheDocument();
    expect(screen.getByText(/fp1/)).toBeInTheDocument();
  });

  it("deletes template after confirm", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    mockFetch([
      { templates: [TMPL] },
      {},
      { templates: [] },
    ]);
    wrap(<SettingsTemplates />);
    await screen.findByText("HDFC");
    fireEvent.click(screen.getByRole("button", { name: /delete/i }));
    await waitFor(() => expect(screen.queryByText("HDFC")).not.toBeInTheDocument());
  });
});
