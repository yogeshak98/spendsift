import "@testing-library/jest-dom";
import { resetInvestmentsTabsCache } from "./features/networth/investmentsTabsCache";

// The Investments tab strip remembers its last vehicle list at module level;
// forget it after every test so no test or file sees another's tabs.
afterEach(() => resetInvestmentsTabsCache());

// jsdom in this toolchain does not ship a functional localStorage; provide a minimal one.
if (typeof localStorage === "undefined" || typeof localStorage.getItem !== "function") {
  const store = new Map<string, string>();
  const mock: Storage = {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    removeItem: (k: string) => void store.delete(k),
    setItem: (k: string, v: string) => void store.set(k, String(v)),
  };
  Object.defineProperty(globalThis, "localStorage", { value: mock, configurable: true });
}
