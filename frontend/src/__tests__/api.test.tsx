import { api } from "@/shared/api/http";

test("getJSON builds the right URL and returns parsed body", async () => {
  const spy = vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ ok: true }), { status: 200 }),
  );
  const body = await api.getJSON<{ ok: boolean }>("/api/health");
  expect(spy).toHaveBeenCalledWith("/api/health", expect.objectContaining({ method: "GET" }));
  expect(body.ok).toBe(true);
});
