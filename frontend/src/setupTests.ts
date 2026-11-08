import "@testing-library/jest-dom";
import { resetInvestmentsTabsCache } from "./features/networth/investmentsTabsCache";

// The Investments tab strip remembers its last vehicle list at module level;
// forget it after every test so no test or file sees another's tabs.
afterEach(() => resetInvestmentsTabsCache());

// Always use this minimal in-memory localStorage. Depending on the Node
// version, the environment otherwise has none, a non-functional one, or jsdom's
// Storage, where vi.spyOn(localStorage, "getItem") stores an item named
// "getItem" instead of replacing the method; a plain object behaves the same
// everywhere.
{
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
