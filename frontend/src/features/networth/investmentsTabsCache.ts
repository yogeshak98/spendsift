// Last-known vehicle list for the Investments tab strip.
//
// Every investments screen is a different component type, so each tab click
// remounts the strip and its useReport starts again at `data: null`. Without a
// remembered list the strip would shrink to "Overview" plus the active tab
// until the summary lands, on every navigation.
//
// Only tab labels live here -- navigation, never money. useReport is left
// uncached on purpose: a global stale-while-revalidate would flash stale
// financial figures after a write. The strip still refetches on every mount and
// on triggerReload(), and the last successful response replaces this list.

export type TabVehicle = { key: string; label: string };

let remembered: TabVehicle[] | null = null;

export function rememberedVehicles(): TabVehicle[] | null {
  return remembered;
}

export function rememberVehicles(vehicles: TabVehicle[]): void {
  remembered = vehicles.map((v) => ({ key: v.key, label: v.label }));
}

/** Test hook: forget the remembered list so no test sees another's tabs. */
export function resetInvestmentsTabsCache(): void {
  remembered = null;
}
