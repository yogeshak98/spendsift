import { createContext, useContext } from "react";
import type { Resp } from "./types";

export type TaxCtx = {
  fy: number;
  setFy: (n: number) => void;
  data: Resp | null;
  /** The workspace read error, so a screen can tell "still loading" from "it
   *  failed". A form-fixable error now renders ABOVE the children (see
   *  `FORM_FIXABLE_CODES`), and without this every screen's null-data guard
   *  would print "Loading…" under the error for ever. */
  error: string | null;
};

export const TaxContext = createContext<TaxCtx | null>(null);

export function useTaxContext(): TaxCtx {
  const ctx = useContext(TaxContext);
  if (!ctx) throw new Error("useTaxContext must be used inside TaxLayout");
  return ctx;
}
