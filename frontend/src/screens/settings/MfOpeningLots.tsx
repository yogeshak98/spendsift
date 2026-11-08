import { useEffect, useRef, useState } from "react";
import { api } from "@/shared/api/http";
import { parseCodedError } from "@/shared/api/codedError";
import { Callout } from "@/components/Callout";
import { triggerReload } from "@/shared/api/useReport";
import type { SchemeHit } from "@/screens/ledger/import/SchemeResolver";

type MfWritten = {
  symbol: string;
  name: string;
  account: string;
  account_display: string;
  units: string;
  unit_cost: string;
  cost_booked: string;
  date: string;
  txn_id: string;
};
type MfOpeningResponse = { written: MfWritten[]; snapshot: string };

type Lot = { id: number; hit: SchemeHit | null; units: string; cost: string; date: string };

const SEARCH_DEBOUNCE_MS = 250;
const emptyLot = (id: number): Lot => ({ id, hit: null, units: "", cost: "", date: "" });

function SchemePicker(props: { hit: SchemeHit | null; onPick: (hit: SchemeHit | null) => void }) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SchemeHit[]>([]);
  const [error, setError] = useState<string | null>(null);
  const latest = useRef(0);

  useEffect(() => {
    const q = query.trim();
    const seq = ++latest.current;
    if (q.length < 3) return;
    const timer = setTimeout(() => {
      api
        .getJSON<{ schemes: SchemeHit[] }>(`/api/schemes/search?q=${encodeURIComponent(q)}`)
        .then((res) => {
          if (seq !== latest.current) return;
          setHits(res.schemes);
          setError(null);
        })
        .catch((e: unknown) => {
          if (seq !== latest.current) return;
          setHits([]);
          setError(`Could not search the scheme list. ${String(e)}`);
        });
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query]);

  // Results for a query the user has since shortened are hidden, not cleared.
  const searchable = query.trim().length >= 3;
  if (props.hit) {
    return (
      <div>
        <strong>{props.hit.name}</strong> ({props.hit.symbol}){" "}
        <button type="button" className="btn sm" onClick={() => props.onPick(null)}>
          Change scheme
        </button>
      </div>
    );
  }
  return (
    <div>
      <input
        type="text"
        aria-label="Search schemes"
        placeholder="scheme name or AMFI code"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      {searchable && error && <div className="sub v-red">{error}</div>}
      {searchable && hits.map((h) => (
        <button
          type="button"
          key={h.symbol}
          className="resolver-hit"
          onClick={() => {
            props.onPick(h);
            setQuery("");
            setHits([]);
          }}
        >
          <span className="resolver-hit-name">{h.name}</span>
          <span className="resolver-hit-meta">
            {h.symbol} · {h.category}
          </span>
        </button>
      ))}
    </div>
  );
}

/** Opening lots for mutual-fund holdings an order history cannot cover. Nothing
 *  is written until the user ticks the confirmation; any edit clears the tick. */
export function MfOpeningLots() {
  const [lots, setLots] = useState<Lot[]>([emptyLot(1)]);
  const [nextId, setNextId] = useState(2);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ description: string; resolution: string | null } | null>(null);
  const [result, setResult] = useState<MfOpeningResponse | null>(null);

  const edit = (fn: () => void) => {
    fn();
    setConfirmed(false);
  };
  const patch = (id: number, p: Partial<Lot>) =>
    edit(() => setLots((ls) => ls.map((l) => (l.id === id ? { ...l, ...p } : l))));
  const complete = lots.every((l) => l.hit && l.units.trim() !== "" && l.cost.trim() !== "" && l.date !== "");

  async function record() {
    setError(null);
    setBusy(true);
    try {
      const res = await api.postJSON<MfOpeningResponse>("/api/openings/mf-lots", {
        lots: lots.map((l) => ({
          symbol: l.hit!.symbol,
          units: l.units.trim(),
          cost: l.cost.trim(),
          date: l.date,
        })),
      });
      setResult(res);
      triggerReload();
    } catch (e: unknown) {
      setError(parseCodedError(e) ?? { description: String(e), resolution: null });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mf-opening-lots">
      <h3>Mutual funds</h3>
      <p>
        Prefer importing your Groww order history from the start; add opening lots only for holdings it cannot
        cover.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void record();
        }}
      >
        {lots.map((lot, i) => (
          <div key={lot.id} className="opening-lot" role="group" aria-label={`Lot ${i + 1}`}>
            <SchemePicker hit={lot.hit} onPick={(hit) => patch(lot.id, { hit })} />
            <label>
              Units
              <input
                inputMode="decimal"
                value={lot.units}
                onChange={(e) => patch(lot.id, { units: e.target.value })}
              />
            </label>
            <label>
              Total cost
              <input
                inputMode="decimal"
                value={lot.cost}
                onChange={(e) => patch(lot.id, { cost: e.target.value })}
              />
            </label>
            <label>
              Acquisition date
              <input type="date" value={lot.date} onChange={(e) => patch(lot.id, { date: e.target.value })} />
            </label>
            {lots.length > 1 && (
              <button
                type="button"
                className="btn sm"
                onClick={() => edit(() => setLots((ls) => ls.filter((l) => l.id !== lot.id)))}
              >
                Remove lot
              </button>
            )}
          </div>
        ))}
        <button
          type="button"
          className="btn sm"
          onClick={() => {
            edit(() => setLots((ls) => [...ls, emptyLot(nextId)]));
            setNextId(nextId + 1);
          }}
        >
          Add lot
        </button>
        <label>
          <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
          I&apos;ve checked these lots
        </label>
        {error && (
          <Callout kind="bad">
            {error.description}
            {error.resolution && <> {error.resolution}</>}
          </Callout>
        )}
        <button type="submit" className="btn sm" disabled={!confirmed || !complete || busy || result !== null}>
          Record lots
        </button>
      </form>
      {result && (
        <div>
          <div>Recorded. Restore point: {result.snapshot}</div>
          <table>
            <thead>
              <tr>
                <th>Scheme</th>
                <th>Units</th>
                <th>Date</th>
                <th>Unit cost</th>
                <th>Cost booked</th>
              </tr>
            </thead>
            <tbody>
              {result.written.map((w) => (
                <tr key={w.txn_id}>
                  <td>{w.name}</td>
                  <td>{w.units}</td>
                  <td>{w.date}</td>
                  <td>{w.unit_cost}</td>
                  <td>{w.cost_booked}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
