import type { JSX, ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { LIABILITY_VIEWS, TAB_PATHS, TABS, tabOf } from "./netWorthPaths";

// The Net Worth screen's own header: one tab per view, and a slot on the right
// for what belongs to the open tab (the as-of date and badges on Overview, the
// benchmark on Investments, the period on Credit cards). It replaces the
// app-wide crumb bar for the whole /networth subtree.

export function NetWorthTabs({ right }: { right?: ReactNode }): JSX.Element {
  const { pathname } = useLocation();
  const active = tabOf(pathname);
  return (
    <div className="nw-top">
      <nav className="nw-tabs" aria-label="Net Worth">
        {TABS.map((t) => (
          <Link
            key={t.id}
            to={TAB_PATHS[t.id]}
            className={`nw-tab${t.id === active ? " on" : ""}`}
            aria-current={t.id === active ? "page" : undefined}
          >
            {t.label}
          </Link>
        ))}
      </nav>
      {right !== undefined && <div className="nw-top-right">{right}</div>}
    </div>
  );
}

/** The view chips under the Liabilities tab. */
export function LiabilityViews(): JSX.Element {
  const { pathname } = useLocation();
  return (
    <div className="itabs nw-subtabs">
      {LIABILITY_VIEWS.map((v) => (
        <Link key={v.path} to={v.path} className={`itab${pathname === v.path ? " active" : ""}`}>
          {v.label}
        </Link>
      ))}
    </div>
  );
}
