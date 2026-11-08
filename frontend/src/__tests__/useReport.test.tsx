import { renderHook, waitFor } from "@testing-library/react";
import { useReport } from "@/shared/api/useReport";

test("useReport fetches and returns data", async () => {
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ spent_this_month: "412" }), { status: 200 }),
  );
  const { result } = renderHook(() =>
    useReport<{ spent_this_month: string }>("/api/reports/dashboard?year=2026&month=4"),
  );
  await waitFor(() => expect(result.current.data).not.toBeNull());
  expect(result.current.data!.spent_this_month).toBe("412");
});
