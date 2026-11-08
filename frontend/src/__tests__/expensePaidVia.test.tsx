import { fyOf, kindOf, labelOf } from "@/screens/expense/paidVia";

test("kindOf treats Assets:Bank: as bank, matching the backend funding rule", () => {
  expect(kindOf("Liabilities:CreditCard:Acme1234")).toBe("card");
  expect(kindOf("Assets:Checking:Synth")).toBe("bank");
  expect(kindOf("Assets:Bank:Synth")).toBe("bank");
  expect(kindOf("Liabilities:People:Someone")).toBe("other");
  expect(kindOf("Assets:Broker:Synth")).toBe("other");
});

test("labelOf and fyOf", () => {
  expect(labelOf("Assets:Bank:Synth")).toBe("Synth");
  expect(labelOf("Liabilities:CreditCard:Acme1234")).toBe("Acme1234");
  expect(fyOf(2026, 7)).toBe(2026);
  expect(fyOf(2026, 3)).toBe(2025);
});
