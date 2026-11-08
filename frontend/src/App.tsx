import { createElement, type JSX } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { Rail } from "./shell/Rail";
import { ContentHeader } from "./shell/ContentHeader";
import { TooltipProvider } from "@/shared/theme/tooltip";
import { PeriodProvider } from "@/shared/period/period";
import { SCREENS, INSTRUMENT_SCREENS, REDIRECTS } from "./routes";
import "./theme.css";
import "./shared/ui/ui.css";
import "./shared/charts/charts.css";
import "./features/dashboard/dashboard.css";
import "./features/networth/networth.css";
import "./screens/cashflow/cashflow.css";
import "./screens/expense/expense.css";
import "./screens/ledger/ledger.css";
import "./screens/ledger/import/import.css";
import "./screens/ledger/quickadd.css";
import "./screens/planning/planning.css";
import "./features/networth/investments.css";
import "./screens/liabilities/liabilities.css";
import "./shell/shell.css";

function ComingSoon() {
  const key = useLocation().pathname.replace(/^\//, "") || "dashboard";
  return <div style={{ padding: "1.8462rem" }}>Coming soon — {key}</div>;
}

// The exact SCREENS lookup is tried first and is unchanged from before this
// resolver existed. Only on a miss do we try exactly one trailing dynamic
// segment -- the parent path (everything but the last "/"-segment) looked up
// in INSTRUMENT_SCREENS, with the last segment treated as an instrument id
// that the resolved screen parses back out of the URL itself (see
// InstrumentDetail.tsx). This is deliberately NOT a migration to declarative
// <Routes> -- that would touch every existing screen and test for one URL
// shape.
function resolveScreen(key: string): (() => JSX.Element) | undefined {
  if (SCREENS[key]) return SCREENS[key];
  const idx = key.lastIndexOf("/");
  return idx > 0 ? INSTRUMENT_SCREENS[key.slice(0, idx)] : undefined;
}

function ScreenOutlet() {
  const { pathname, search } = useLocation();
  const key = pathname.replace(/^\//, "") || "dashboard";
  // A retired screen's URL is replaced (not pushed) with its successor.
  const target = REDIRECTS[key];
  if (target) return <Navigate to={{ pathname: `/${target}`, search }} replace />;
  // SCREENS and INSTRUMENT_SCREENS hold module-level components, so this is a
  // lookup, not a component created during render.
  return createElement(resolveScreen(key) ?? ComingSoon);
}

export default function App() {
  return (
    <TooltipProvider>
      <PeriodProvider>
        <div style={{ display: "flex", minHeight: "100vh" }}>
          <Rail />
          <div className="content">
            <ContentHeader />
            <ScreenOutlet />
          </div>
        </div>
      </PeriodProvider>
    </TooltipProvider>
  );
}
