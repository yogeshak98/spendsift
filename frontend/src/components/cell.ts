import type { ReactNode } from "react";
import type { SortableCell } from "./DataTable";

export function cell(sort: string | number | null, node: ReactNode): SortableCell {
  return { sort, node };
}
