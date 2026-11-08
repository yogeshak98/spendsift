import { useEffect, useRef, useState } from "react";
import { api } from "@/shared/api/http";

// The resolution ACTION for a review row the importer could not identify.
//
// `POST /api/review/{batch}/{candidate}/resolve-scheme` shipped with no
// frontend caller at all: a blocked row could be seen but not fixed, because
// identifying a scheme meant knowing its AMFI code by heart. This is the
// missing half.
//
// Two different holds arrive on the same `unresolved_scheme` field, and they
// are NOT the same decision:
//
//   * A mutual fund whose name did not match the AMFI master. The row has no
//     commodity, and the user must say WHICH scheme it is. Guessing writes a
//     wrong cost lot under a wrong account, so this needs a real lookup.
//
//   * A stock symbol never held before. The symbol IS the commodity, so
//     nothing is unknown -- a first-ever BUY is ordinary activity. The user is
//     only being asked to confirm opening a new holding account, which the
//     taxonomy says should never happen silently.
//
// Presenting the second as a search box would be a lie about what is being
// asked, so they render differently and only share the route.

export type SchemeHit = { symbol: string; code: string; name: string; category: string };

export type SchemeResolverProps = {
  batchId: string;
  candidateId: string;
  // The flag's value: an unmatched scheme NAME for a fund, or the SYMBOL for a
  // stock. `commodity` is what tells the two apart -- a fund that failed to
  // resolve has none.
  unresolvedScheme: string;
  commodity: string | null;
  narration: string;
  onResolved: (candidateId: string, commodity: string, schemeName: string) => void;
};

export function SchemeResolver(props: SchemeResolverProps) {
  const isNewSymbol = props.commodity !== null;
  return isNewSymbol ? <NewSymbolHold {...props} /> : <SchemeLookup {...props} />;
}

// --- stock: a new symbol, nothing to identify -------------------------------

function NewSymbolHold(props: SchemeResolverProps) {
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    setPosting(true);
    setError(null);
    try {
      await api.postJSON(`/api/review/${props.batchId}/${props.candidateId}/resolve-scheme`, {
        // The symbol is already correct -- this is an acknowledgement, not a
        // correction, so nothing about the row changes except the hold.
        commodity: props.commodity,
        scheme_name: props.narration,
      });
      props.onResolved(props.candidateId, props.commodity as string, props.narration);
    } catch (e) {
      setError(String(e));
    } finally {
      setPosting(false);
    }
  }

  return (
    <div className="resolver">
      <div className="sub">
        First time holding <b>{props.unresolvedScheme}</b>. Committing opens a new holding
        account for it.
      </div>
      <button type="button" onClick={confirm} disabled={posting}>
        {posting ? "Opening…" : `Open a holding for ${props.unresolvedScheme}`}
      </button>
      {error !== null && (
        <div className="sub v-red">
          Could not clear this hold. Nothing was changed — the row is still blocked. {error}
        </div>
      )}
    </div>
  );
}

// --- mutual fund: identify the scheme ---------------------------------------

function SchemeLookup(props: SchemeResolverProps) {
  const [query, setQuery] = useState(props.unresolvedScheme);
  const [hits, setHits] = useState<SchemeHit[]>([]);
  // The query whose lookup last finished; "searching" is any other query.
  const [settled, setSettled] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [posting, setPosting] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Guards against a slow early response landing after a later one and
  // repainting the list with results for a query the user has moved past.
  const latest = useRef(0);

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    const trimmed = query.trim();
    if (trimmed.length < 3) return;
    const seq = ++latest.current;
    timer.current = setTimeout(() => {
      api
        .getJSON<{ schemes: SchemeHit[] }>(
          `/api/schemes/search?q=${encodeURIComponent(trimmed)}`,
        )
        .then((res) => {
          if (seq !== latest.current) return;
          setHits(res.schemes);
          setError(null);
        })
        .catch((e) => {
          if (seq !== latest.current) return;
          // A failed lookup must not read as "no such scheme" -- that would
          // push the user toward the wrong conclusion about their own data.
          setHits([]);
          setError(String(e));
        })
        .finally(() => {
          if (seq === latest.current) setSettled(trimmed);
        });
    }, 250);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [query]);

  async function choose(hit: SchemeHit) {
    setPosting(true);
    try {
      await api.postJSON(`/api/review/${props.batchId}/${props.candidateId}/resolve-scheme`, {
        commodity: hit.symbol,
        scheme_name: hit.name,
      });
      props.onResolved(props.candidateId, hit.symbol, hit.name);
    } catch (e) {
      setError(String(e));
    } finally {
      setPosting(false);
    }
  }

  const trimmed = query.trim();
  // Under three characters there is no lookup: show no results, error or spinner.
  const active = trimmed.length >= 3;
  const searching = active && settled !== trimmed;
  const shownHits = active ? hits : [];
  const shownError = active ? error : null;

  return (
    <div className="resolver">
      <div className="sub">
        The statement calls this <b>{props.unresolvedScheme}</b>, which is not a name in the
        AMFI scheme list. Find the scheme to identify it.
      </div>
      <input
        type="text"
        aria-label="search schemes"
        value={query}
        disabled={posting}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="scheme name or AMFI code"
      />
      {trimmed.length > 0 && trimmed.length < 3 && (
        <div className="sub v-dim">Keep typing — at least three characters.</div>
      )}
      {searching && <div className="sub v-dim">Searching…</div>}
      {shownError !== null && (
        <div className="sub v-red">
          Could not search the scheme list, so this is not a statement about whether the
          scheme exists. Nothing was changed. {shownError}
        </div>
      )}
      {!searching && shownError === null && active && shownHits.length === 0 && (
        <div className="sub v-dim">
          No scheme matches that. Try fewer words, or the AMFI code from your statement.
        </div>
      )}
      <div className="resolver-hits">
        {shownHits.map((hit) => (
          <button
            type="button"
            key={hit.symbol}
            className="resolver-hit"
            disabled={posting}
            onClick={() => choose(hit)}
          >
            <span className="resolver-hit-name">{hit.name}</span>
            <span className="resolver-hit-meta">
              {hit.symbol} · {hit.category}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
