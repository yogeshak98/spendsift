import type { JSX } from "react";
import { useReport } from "@/shared/api/useReport";
import { inrWhole } from "@/shared/format/money";
import { journalStartLabel } from "./journalStartLabel";
import "./tax.css";

/** `GET /api/reports/tax/gratuity-ceiling` -- all-time, not FY-scoped, because
 *  the s.10(10) ceiling is cumulative across every employer for a working life.
 *  See `API_CONTRACT.md` ss`get_tax_gratuity_ceiling`. */
type Ceiling = {
  /** From the user's rules file ONLY. `null` means none is declared, and there
   *  is no fallback anywhere in the stack: a ceiling is a statutory figure this
   *  codebase does not write down. */
  ceiling: string | null;
  recorded: string;
  /** `ceiling - recorded`, floored at 0, and `null` whenever `ceiling` is. */
  unused: string | null;
  /** Earliest transaction date in the ledger, and the honest horizon of
   *  `recorded`. `""` for an empty journal. */
  journal_start: string;
  has_gratuity: boolean;
};

/** The gratuity exemption ceiling against the gratuity this journal records.
 *
 * Renders NOTHING in three cases, each because the panel would otherwise state
 * a figure the data does not support:
 *
 *  - no gratuity recorded (`has_gratuity === false`) -- there is nothing to
 *    measure, and a 0-of-ceiling bar would invite a reading ("you have X left")
 *    that only becomes true once a gratuity exists;
 *  - no declared ceiling (`ceiling === null`, and therefore `unused === null`)
 *    -- an invented ceiling would be unsourced and the headroom derived from it
 *    would be fiction. `null` is NOT a zero and has no fallback;
 *  - no readable `journal_start` -- `unused` is an upper bound, true only as far
 *    back as the journal goes, so without the horizon the caveat cannot be
 *    stated and the number must not be either.
 */
export function GratuityCeilingPanel(): JSX.Element {
  const { data } = useReport<Ceiling>("/api/reports/tax/gratuity-ceiling");
  if (data === null) return <></>;
  if (!data.has_gratuity) return <></>;
  if (data.ceiling === null || data.unused === null) return <></>;
  const start = journalStartLabel(data.journal_start);
  if (start === null) return <></>;

  const ceiling = Number(data.ceiling);
  const pct = ceiling > 0 ? Math.min(100, (Number(data.recorded) / ceiling) * 100) : 0;

  return (
    <div className="panel" style={{ marginTop: "0.9231rem" }}>
      <div className="panel-head">Gratuity exemption ceiling</div>
      <div className="ceiling-body">
        {/* The existing track/fill pair, in `--text3`: a correct exemption is
            not a warning, and the one amber on this screen is spent below. */}
        <div className="track">
          <div className="fill" style={{ width: `${pct}%`, background: "var(--text3)" }} />
        </div>
        <div className="data-row">
          <span>Recorded in this journal</span>
          <span className="mono">{inrWhole(data.recorded)}</span>
        </div>
        <div className="data-row">
          <span>
            Ceiling <span className="qual">— s.10(10), lifetime, across all employers</span>
          </span>
          <span className="mono">{inrWhole(data.ceiling)}</span>
        </div>
        <div className="data-row">
          <span>
            Unused <span className="qual">— as far as this journal knows</span>
          </span>
          <span className="mono">{inrWhole(data.unused)}</span>
        </div>
        {/* The journal-start date comes from the response, never from a
            constant: the real journal does not begin in the April a reader
            would assume, and a hardcoded month would make the one caveat on
            this screen the one false statement on it. */}
        <div className="callout warn" style={{ margin: "1.0769rem 0 0" }}>
          <span>
            This journal starts {start}. Gratuity from an earlier job is not in it. If you
            received any, the unused figure above is too high.
          </span>
        </div>
      </div>
    </div>
  );
}
