import { Link, useLocation, useNavigate } from "react-router-dom";
import { NAV } from "../nav";
import { ThemeSwitcher } from "@/shared/theme/theme";
import { useEffect } from "react";

export function Rail() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  // Liabilities is a top-level shortcut to Net Worth's Liabilities tab, so on
  // that tab it is Liabilities, not Net Worth, that is lit.
  const activeDomain =
    pathname === "/networth/liabilities" || pathname.startsWith("/networth/liabilities/")
      ? "liabilities"
      : pathname.split("/")[1] || "dashboard";

  // Global 'n' shortcut: navigate to Quick Add from anywhere,
  // as long as no input/textarea/contenteditable has focus.
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key !== "n" || e.metaKey || e.ctrlKey || e.altKey) return;
      const tag = (e.target as HTMLElement).tagName.toLowerCase();
      const inInput =
        tag === "input" ||
        tag === "textarea" ||
        tag === "select" ||
        (e.target as HTMLElement).isContentEditable;
      if (!inInput) {
        e.preventDefault();
        navigate("/ledger/quick-add");
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [navigate]);

  const pinned = NAV[NAV.length - 1].id === "settings" ? NAV[NAV.length - 1] : null;
  const domains = pinned ? NAV.slice(0, -1) : NAV;

  const isQA = pathname === "/ledger/quick-add";

  const renderDomain = (d: (typeof NAV)[number]) => {
    const isActive = d.id === activeDomain && !isQA;
    const target = d.subs.length ? `/${d.id}/${d.subs[0].id}` : `/${d.id}`;
    return (
      <div key={d.id}>
        <Link to={target} className={`nav-item${isActive ? " active" : ""}`}>
          <span className="ic">{d.icon}</span>
          {d.label}
        </Link>
        {isActive && d.subs.length > 0 && (
          <div className="nav-sub show">
            {d.subs.map((s) => {
              const subPath = `/${d.id}/${s.id}`;
              // startsWith (not exact equality) so a drill-down screen nested under
              // this sub — e.g. /networth/investments/stock/SBIN — still highlights
              // its parent sub-item, mirroring the mockup where `data-nav` stays on
              // the hub (e.g. "Investments") through every level of breadcrumb.
              const isSubActive = pathname === subPath || pathname.startsWith(`${subPath}/`);
              return (
                <Link key={s.id} to={subPath} className={`nav-subitem${isSubActive ? " active" : ""}`}>
                  {s.label}
                </Link>
              );
            })}
          </div>
        )}
      </div>
    );
  };

  return (
    <nav className="rail">
      <div className="brand">
        <div className="logo">
          Spend<span>Sift</span>
        </div>
        <div className="tag">Local · INR</div>
      </div>
      <div className="nav">{domains.map(renderDomain)}</div>
      <div className="rail-foot">
        <div className="rail-qa">
          <button
            className={isQA ? "on" : ""}
            onClick={() => navigate("/ledger/quick-add")}
          >
            <span className="plus">＋</span> Quick add
            <span className="key">n</span>
          </button>
        </div>
        {pinned && <div style={{ marginBottom: "0.6154rem" }}>{renderDomain(pinned)}</div>}
        <ThemeSwitcher />
      </div>
    </nav>
  );
}
