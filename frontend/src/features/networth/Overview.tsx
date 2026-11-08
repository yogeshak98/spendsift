import { useCallback, useEffect, useState, type JSX } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { NoOpeningBadge } from "@/components/NoOpeningBadge";
import { OpeningsCallout } from "@/components/OpeningsCallout";
import { OPENINGS_URL, missingAccounts, type OpeningWarning, type OpeningsStatusResponse } from "@/openings";
import { useReport } from "@/shared/api/useReport";
import { inrWhole } from "@/shared/format/money";
import { NetWorthTabs } from "./NetWorthTabs";
import { useNetworthOverview } from "./useOverview";
import type { NetworthOverviewResponse } from "./api";
import {
  FLASH_MS,
  sectionCovers,
  xirrBasis,
  SECTION_PARAM,
  SECTIONS,
  cardFacts,
  cardTag,
  monthLabel,
  pct,
  shortDate,
  signedCompact,
  type OverviewGroup,
} from "./overviewSections";

// Net Worth · Overview (mock networth-balance v2): four tiles, what the assets
// are made of, then one card per asset group and per liability group. Every
// figure comes from GET /api/reports/networth/overview; nothing is summed here
// except the mix bar's shares and the accounts count.

type Doctor = { journal_valid: boolean; errors: string[] };

function AsOfBadges({ data }: { data: NetworthOverviewResponse | null }): JSX.Element | null {
  const { data: doctor } = useReport<Doctor>("/api/reports/doctor");
  if (data === null) return null;
  // A bank account with no activity for more than 14 days: its import is
  // probably overdue. Rows are only the top 3, so the count comes from facts.
  const bank = data.groups.find((g) => g.id === "bank");
  const staleCount = bank?.facts.stale_count ?? 0;
  const staleRows = (bank?.accounts ?? []).filter((a) => a.stale);
  return (
    <span className="nw-asof">
      as of {shortDate(data.as_of)}
      {doctor !== null &&
        (doctor.journal_valid ? (
          <span className="nw-badge ok">● journal valid</span>
        ) : (
          <Link to="/doctor" className="nw-badge bad">
            ● journal has {doctor.errors.length} error{doctor.errors.length === 1 ? "" : "s"}
          </Link>
        ))}
      {staleCount === 1 && staleRows.length === 1 ? (
        <span className="nw-badge warn" title="No activity for more than 14 days">
          {staleRows[0].name} · {staleRows[0].meta}
        </span>
      ) : (
        staleCount > 0 && (
          <span className="nw-badge warn" title="Bank accounts with no activity for more than 14 days">
            {staleCount} stale import{staleCount === 1 ? "" : "s"}
          </span>
        )
      )}
    </span>
  );
}

function Tiles({ data }: { data: NetworthOverviewResponse }): JSX.Element {
  const t = data.totals;
  const inv = data.groups.find((g) => g.id === "investments");
  const loans = data.liability_groups.find((g) => g.id === "loans");
  const accounts = data.groups.reduce((n, g) => n + g.account_count, 0);
  const owedAccounts = data.liability_groups.reduce((n, g) => n + g.account_count, 0);
  const xirr = inv?.facts.xirr_pct ?? null;
  const basis = xirrBasis(data.investments.xirr_basis);
  const b = data.benchmarks;
  const benchLabel = b?.options.find((o) => o.key === b.selected)?.label ?? b?.selected;
  const change = (v: string, label: string) => (
    <span className={Number(v) < 0 ? "t-red" : Number(v) > 0 ? "t-green" : undefined}>
      {Number(v) < 0 ? "▼" : Number(v) > 0 ? "▲" : ""} {inrWhole(Math.abs(Number(v)))} {label}
    </span>
  );
  return (
    <div className="nw-sumrow">
      <div className="nw-stat hero">
        <div className="k">Net worth</div>
        <div className="v t-violet">{inrWhole(t.net_worth)}</div>
        <div className="s">
          {change(t.change_this_fy, "this FY")} · {change(t.change_this_month, monthLabel(data.as_of))}
        </div>
      </div>
      <div className="nw-stat">
        <div className="k">Assets</div>
        <div className="v">{inrWhole(t.assets)}</div>
        <div className="s">
          {accounts} account{accounts === 1 ? "" : "s"}
        </div>
      </div>
      <div className="nw-stat">
        <div className="k">Liabilities</div>
        <div className={`v${Number(t.liabilities) > 0 ? " t-amber" : ""}`}>{inrWhole(t.liabilities)}</div>
        <div className="s">
          {loans?.facts.emi_per_month
            ? `EMIs ${inrWhole(loans.facts.emi_per_month)}/mo`
            : owedAccounts === 0
              ? "nothing owed"
              : `${owedAccounts} account${owedAccounts === 1 ? "" : "s"}`}
        </div>
      </div>
      <div className="nw-stat" data-testid="nw-return-tile">
        <div className="k">Investments return</div>
        {xirr === null ? (
          <>
            <div className="v t-dim">—</div>
            <div className="s">{inv ? "at cost, no return yet" : "no investments"}</div>
          </>
        ) : (
          <>
            <div className={`v${Number(xirr) < 0 ? " t-red" : " t-green"}`}>{pct(xirr)}</div>
            <div className="s" title={`XIRR ${basis.long}`}>
              XIRR, {basis.short}
              {inv?.facts.gain != null && <> · gain {signedCompact(inv.facts.gain)}</>}
              {b !== null && b.selected_return_pct !== null && (
                <>
                  {" "}
                  · {benchLabel} {pct(b.selected_return_pct)}
                </>
              )}
            </div>
          </>
        )}
        {data.benchmarks === null && (
          <div className="nw-quiet" title={data.benchmarks_error?.message}>
            Benchmark unavailable: check config/benchmarks.yaml
          </div>
        )}
      </div>
    </div>
  );
}

function MixBar({ data, onPick }: { data: NetworthOverviewResponse; onPick: (id: string) => void }) {
  const positive = data.groups.filter((g) => Number(g.total) > 0);
  const total = positive.reduce((s, g) => s + Number(g.total), 0);
  const excluded = data.totals.excluded;
  return (
    <div className="nw-mix" aria-label="What your assets are made of">
      {total > 0 && (
        <div className="bar">
          {positive.map((g) => (
            <button
              type="button"
              key={g.id}
              title={`${g.label} ${inrWhole(g.total)}`}
              aria-label={`${g.label} ${inrWhole(g.total)}`}
              style={{ flex: Number(g.total), background: SECTIONS[g.id].color }}
              onClick={() => onPick(g.id)}
            />
          ))}
        </div>
      )}
      <div className="lg">
        {positive.map((g) => (
          <span key={g.id}>
            <i style={{ background: SECTIONS[g.id].color }} />
            {g.label}
            <b>{((Number(g.total) / total) * 100).toFixed(0)}%</b>
          </span>
        ))}
      </div>
      {excluded.length > 0 && (
        <span className="excl" title={excluded.map((e) => e.reason).join("\n")}>
          Not counted:{" "}
          <b>{excluded.map((e) => `${e.account_display} ${inrWhole(e.amount)}`).join(", ")}</b> (settles
          against tax due)
        </span>
      )}
    </div>
  );
}

function Card({
  g,
  asOf,
  flash,
  missing,
}: {
  g: OverviewGroup;
  asOf: string;
  flash: boolean;
  missing: ReadonlyMap<string, OpeningWarning>;
}): JSX.Element {
  const navigate = useNavigate();
  const spec = SECTIONS[g.id];
  const tag = cardTag(g);
  const facts = cardFacts(g, asOf);
  // A liability group below zero is money owed to you (People, or another
  // liability account in credit): shown as a magnitude, in green.
  const owedToYou = (g.id === "people" || g.id === "other_liabilities") && Number(g.total) < 0;
  return (
    <button
      type="button"
      id={`nw-sec-${g.id}`}
      data-section={g.id}
      className={`nw-card${flash ? " flash" : ""}`}
      onClick={() => navigate(spec.to)}
    >
      <div className="hd">
        <span className="ico" style={{ color: spec.color }}>
          {spec.icon}
        </span>
        <span>
          <span className="name">{g.label}</span>
          <span className="cnt">{spec.noun(g.account_count)}</span>
        </span>
        <span className="chev">›</span>
      </div>
      <div className="val">
        <span className={owedToYou ? "t-green" : undefined}>{inrWhole(Math.abs(Number(g.total)))}</span>
        {tag && <span className={`nw-tag t-${tag.tone}`}>{tag.text}</span>}
      </div>
      {facts.length > 0 && (
        <div className="facts">
          {facts.map((f) => (
            <span key={f.k}>
              {f.k} <b className={f.tone ? `t-${f.tone}` : undefined}>{f.v}</b>
            </span>
          ))}
        </div>
      )}
      <ul>
        {g.accounts.map((a) => (
          <li key={a.account} title={a.meta ? `${a.name} · ${a.meta}` : a.name}>
            <span>
              {a.name}
              {a.meta && <span className={a.stale ? "t-amber" : "meta"}> · {a.meta}</span>}{" "}
              <NoOpeningBadge account={a.account} missing={missing} />
            </span>
            <span className={a.direction === "owed_to_me" ? "t-green" : undefined}>{inrWhole(a.balance)}</span>
          </li>
        ))}
      </ul>
    </button>
  );
}

// The section being highlighted, for FLASH_MS: the one a `?section=<id>` link
// names (once the cards exist), or the card picked on the mix bar. A section
// may cover several cards (see `sectionCovers`); the first is scrolled to.
function useSectionFlash(ready: boolean): [string | null, (id: string) => void] {
  const [params] = useSearchParams();
  const fromUrl = params.get(SECTION_PARAM);
  const [urlDone, setUrlDone] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const urlFlash = ready && fromUrl !== null && urlDone !== fromUrl ? fromUrl : null;

  useEffect(() => {
    if (urlFlash === null) return;
    const t = window.setTimeout(() => setUrlDone(urlFlash), FLASH_MS);
    return () => window.clearTimeout(t);
  }, [urlFlash]);

  useEffect(() => {
    if (picked === null) return;
    const t = window.setTimeout(() => setPicked(null), FLASH_MS);
    return () => window.clearTimeout(t);
  }, [picked]);

  const flash = useCallback((id: string) => setPicked(id), []);
  const flashing = picked ?? urlFlash;

  // After the highlight is drawn, bring the first highlighted card into view.
  useEffect(() => {
    if (flashing === null) return;
    document.querySelector(".nw-card.flash")?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }, [flashing]);

  return [flashing, flash];
}

export function NetWorthOverview(): JSX.Element {
  const { data, error } = useNetworthOverview();
  const [flashing, flash] = useSectionFlash(data !== null);
  // A failed openings fetch is ignored: only `data` is read, so the warning
  // simply does not appear and the screen's own state is untouched.
  const openings = useReport<OpeningsStatusResponse>(OPENINGS_URL).data;
  const missing = missingAccounts(openings);

  let body: JSX.Element;
  if (error !== null && data === null) {
    body = (
      <div className="callout warn">
        Could not load net worth. The journal is unchanged — this is a read failure, not a data loss.
      </div>
    );
  } else if (data === null) {
    body = <div className="nw-loading">Loading…</div>;
  } else if (data.groups.length === 0 && data.liability_groups.length === 0) {
    body = <div className="nw-empty">No accounts with a balance yet. Import a statement to see your net worth.</div>;
  } else {
    body = (
      <>
        <OpeningsCallout data={openings} />
        <Tiles data={data} />
        <MixBar data={data} onPick={flash} />
        {data.groups.length > 0 && (
          <>
            <div className="nw-sec">
              <h2>Assets</h2>
              <span className="tot">{inrWhole(data.totals.assets)}</span>
            </div>
            <div className="nw-cards">
              {data.groups.map((g) => (
                <Card
                  key={g.id}
                  g={g}
                  asOf={data.as_of}
                  flash={sectionCovers(flashing, g.id)}
                  missing={missing}
                />
              ))}
            </div>
          </>
        )}
        {data.liability_groups.length > 0 && (
          <>
            <div className="nw-sec">
              <h2>Liabilities</h2>
              <span className="tot">{inrWhole(data.totals.liabilities)}</span>
            </div>
            <div className="nw-cards">
              {data.liability_groups.map((g) => (
                <Card
                  key={g.id}
                  g={g}
                  asOf={data.as_of}
                  flash={sectionCovers(flashing, g.id)}
                  missing={missing}
                />
              ))}
            </div>
          </>
        )}
      </>
    );
  }

  return (
    <>
      <NetWorthTabs right={<AsOfBadges data={data} />} />
      <div className="nw-wrap">{body}</div>
    </>
  );
}
