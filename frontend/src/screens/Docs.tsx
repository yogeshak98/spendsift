import type { JSX } from "react";

// Section helper so each block gets consistent padding + scroll anchor
function S({ id, children }: { id: string; children: React.ReactNode }): JSX.Element {
  return (
    <section id={id} style={{ marginBottom: "3.6923rem", scrollMarginTop: 48 }}>
      {children}
    </section>
  );
}

function H2({ children }: { children: React.ReactNode }): JSX.Element {
  return (
    <h2 style={{ fontSize: "1.1538rem", fontWeight: 600, color: "var(--text)", marginBottom: "0.7692rem", paddingBottom: "0.6154rem", borderBottom: "1px solid var(--border)", marginTop: 0 }}>
      {children}
    </h2>
  );
}

function H3({ children }: { children: React.ReactNode }): JSX.Element {
  return (
    <h3 style={{ fontSize: "1rem", fontWeight: 600, color: "var(--text)", margin: "1.6923rem 0 0.5385rem" }}>
      {children}
    </h3>
  );
}

function P({ children }: { children: React.ReactNode }): JSX.Element {
  return <p style={{ fontSize: "0.9615rem", color: "var(--text2)", lineHeight: 1.65, marginBottom: "0.7692rem" }}>{children}</p>;
}

function Code({ children }: { children: React.ReactNode }): JSX.Element {
  return (
    <code style={{ fontFamily: "var(--mono)", fontSize: "0.8462rem", background: "var(--surface2)", border: "1px solid var(--border)", borderRadius: "0.3077rem", padding: "1px 0.3846rem", color: "var(--text)" }}>
      {children}
    </code>
  );
}

function Pre({ children }: { children: React.ReactNode }): JSX.Element {
  return (
    <pre style={{ fontFamily: "var(--mono)", fontSize: "0.8462rem", background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "0.6154rem", padding: "0.9231rem 1.0769rem", overflowX: "auto", margin: "0.7692rem 0", lineHeight: 1.65, color: "var(--text2)" }}>
      {children}
    </pre>
  );
}

type CalloutKind = "info" | "warn" | "tip";
const CALLOUT_COLORS: Record<CalloutKind, { bg: string; border: string; text: string }> = {
  info: { bg: "rgba(96,165,250,0.07)", border: "rgba(96,165,250,0.30)", text: "var(--text2)" },
  warn: { bg: "rgba(251,191,36,0.10)", border: "rgba(251,191,36,0.40)", text: "var(--amber)" },
  tip:  { bg: "rgba(74,222,128,0.07)",  border: "rgba(74,222,128,0.30)",  text: "var(--green)" },
};

function Callout({ kind, title, children }: { kind: CalloutKind; title: string; children: React.ReactNode }): JSX.Element {
  const c = CALLOUT_COLORS[kind];
  return (
    <div style={{ background: c.bg, border: `1px solid ${c.border}`, borderRadius: "0.6923rem", padding: "0.7692rem 1rem", display: "flex", gap: "0.6923rem", alignItems: "flex-start", margin: "0.9231rem 0", color: c.text, fontSize: "0.9231rem" }}>
      <div>
        <strong style={{ display: "block", color: "var(--text)", marginBottom: 2, fontSize: "0.9231rem" }}>{title}</strong>
        <div style={{ color: "var(--text2)", fontSize: "0.9231rem" }}>{children}</div>
      </div>
    </div>
  );
}

function Pill({ color, children }: { color: "blue" | "green" | "amber" | "dim"; children: React.ReactNode }): JSX.Element {
  const styles: Record<string, { bg: string; color: string; border?: string }> = {
    blue:  { bg: "rgba(96,165,250,0.10)",  color: "var(--blue)" },
    green: { bg: "rgba(74,222,128,0.10)",  color: "var(--green)" },
    amber: { bg: "rgba(251,191,36,0.10)",  color: "var(--amber)" },
    dim:   { bg: "var(--surface2)",         color: "var(--text3)", border: "1px solid var(--border)" },
  };
  const s = styles[color];
  return (
    <span style={{ display: "inline-block", fontFamily: "var(--mono)", fontSize: "0.7692rem", fontWeight: 500, padding: "2px 0.5385rem", borderRadius: "0.3077rem", background: s.bg, color: s.color, border: s.border ?? "none" }}>
      {children}
    </span>
  );
}

function Kbd({ children }: { children: React.ReactNode }): JSX.Element {
  return (
    <kbd style={{ fontFamily: "var(--mono)", fontSize: "0.6923rem", color: "var(--text3)", border: "1px solid var(--border)", borderRadius: "0.3077rem", padding: "1px 0.3846rem", background: "var(--surface2)" }}>
      {children}
    </kbd>
  );
}

function Flow({ steps }: { steps: string[] }): JSX.Element {
  return (
    <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: "0.4615rem", margin: "0.9231rem 0", padding: "0.9231rem 1.0769rem", background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "0.7692rem" }}>
      {steps.map((s, i) => (
        <span key={i} style={{ display: "flex", alignItems: "center", gap: "0.4615rem" }}>
          <span style={{ background: "var(--surface2)", border: "1px solid var(--border)", borderRadius: "0.4615rem", padding: "0.3846rem 0.7692rem", fontFamily: "var(--mono)", fontSize: "0.8462rem", color: "var(--text2)" }}>{s}</span>
          {i < steps.length - 1 && <span style={{ color: "var(--text3)", fontFamily: "var(--mono)", fontSize: "1rem" }}>→</span>}
        </span>
      ))}
    </div>
  );
}

// Simple anchor link list for in-page nav
const SECTIONS = [
  { id: "install",       label: "Installation" },
  { id: "first-run",    label: "First Run" },
  { id: "data-folder",  label: "Data Folder" },
  { id: "institutions", label: "Institutions" },
  { id: "import-flow",  label: "Import Flow" },
  { id: "review",       label: "Review Queue" },
  { id: "dashboard",    label: "Dashboard" },
  { id: "networth",     label: "Net Worth" },
  { id: "nav-prices",   label: "NAV Prices" },
  { id: "cashflow",     label: "Cash Flow" },
  { id: "expense",      label: "Expense" },
  { id: "liabilities",  label: "Liabilities" },
  { id: "income",       label: "Income" },
  { id: "ledger",       label: "Ledger" },
  { id: "planning",     label: "Planning" },
  { id: "tax",          label: "Tax" },
  { id: "quick-add",    label: "Quick Add" },
  { id: "settings",     label: "Settings" },
  { id: "journal",      label: "Journal file" },
  { id: "troubleshoot", label: "Troubleshooting" },
];

export function Docs(): JSX.Element {
  return (
    <div style={{ display: "flex", alignItems: "flex-start" }}>

      {/* ── in-page nav ── */}
      <nav style={{ width: "12.6154rem", flexShrink: 0, position: "sticky", top: "3.2308rem", height: "calc(100vh - 3.2308rem)", overflowY: "auto", padding: "0.9231rem 0", borderRight: "1px solid var(--border)", background: "var(--surface)" }}>
        <div style={{ fontFamily: "var(--mono)", fontSize: "0.6923rem", color: "var(--text3)", letterSpacing: "0.10em", textTransform: "uppercase", padding: "0.3077rem 1.0769rem 0.6154rem" }}>On this page</div>
        {SECTIONS.map(s => (
          <a key={s.id} href={"#" + s.id} style={{ display: "block", padding: "0.3077rem 1.0769rem", fontSize: "0.8846rem", color: "var(--text3)", textDecoration: "none" }}
            onMouseEnter={e => (e.currentTarget.style.color = "var(--text)")}
            onMouseLeave={e => (e.currentTarget.style.color = "var(--text3)")}
          >
            {s.label}
          </a>
        ))}
      </nav>

      {/* ── content ── */}
      <div style={{ flex: 1, minWidth: 0, padding: "2.1538rem 3.0769rem 6.1538rem" }}>

        <div style={{ marginBottom: "2.1538rem" }}>
          <div style={{ fontFamily: "var(--mono)", fontSize: "0.6923rem", color: "var(--blue)", letterSpacing: "0.12em", textTransform: "uppercase", marginBottom: "0.4615rem" }}>User Guide</div>
          <h1 style={{ fontSize: "1.5385rem", fontWeight: 600, color: "var(--text)", letterSpacing: "-0.3px", marginBottom: "0.6154rem" }}>SpendSift</h1>
          <P>Ingests exported statements from your bank, card, and broker — turns them into a trustworthy journal and unified dashboards. No database to touch. No data sent anywhere.</P>
          <Flow steps={["Export from institution", "Drop into inbox", "Review exceptions", "Open dashboards"]} />
        </div>

        {/* ── Installation ── */}
        <S id="install">
          <H2>Installation</H2>
          <P>Requires Python 3.11+ and Node.js 18+.</P>
          <Pre>{`git clone https://github.com/yourname/spendsift.git
cd spendsift
uv sync
cd frontend && npm install && npm run build && cd ..
mkdir -p ../storage/inbox`}</Pre>
          <Callout kind="tip" title="Verify">Run <Code>uv run pytest -q</Code> from the repo root. All tests should pass.</Callout>
        </S>

        {/* ── First Run ── */}
        <S id="first-run">
          <H2>First Run</H2>
          <Pre>{`./.venv/bin/python -m backend.app`}</Pre>
          <P>Open <Code>http://127.0.0.1:8765</Code>. The backend serves both the API and the frontend from one process.</P>
          <Callout kind="info" title="First-time checklist">
            <ol style={{ margin: "0.4615rem 0 0 1.2308rem", lineHeight: 1.9 }}>
              <li>Go to <strong>Settings → Accounts</strong> and confirm default accounts match your institutions.</li>
              <li>Export a CSV from HDFC or ICICI (simplest starting point).</li>
              <li>Drop the file into <Code>storage/inbox/</Code> or use <strong>Ledger → Import → Upload</strong>.</li>
              <li>Work through the Review Queue for any flagged rows.</li>
              <li>Open <strong>Ledger → Transactions</strong> to confirm the import.</li>
              <li>Go to <strong>Net Worth → Investments</strong> and click <strong>Refresh</strong> to fetch NAV prices and stock closes; the arrow beside it lists each feed (AMFI NAVAll, NSE bhavcopy) with its own Refresh.</li>
            </ol>
          </Callout>
        </S>

        {/* ── Data Folder ── */}
        <S id="data-folder">
          <H2>Data Folder</H2>
          <P>Default: <Code>storage/</Code> next to the repo.</P>
          <Pre>{`storage/
├── journal/             # beancount journal — source of truth
│   ├── main.beancount   # options + includes for every year file
│   ├── 2024.beancount   # one file per calendar year
│   └── 2025.beancount
├── snapshots/           # full journal/ copy taken before every write
├── inbox/               # drop exported files here
├── imports/             # uploaded statement files, retained for audit
├── templates/           # column-mapping templates (YAML)
├── scheme_master/       # cached AMFI scheme code/name/NAV master
├── prices/              # NAV price cache
└── rules/               # learned category rules`}</Pre>
          <Callout kind="warn" title="Back this up">
            <Code>journal/</Code> is your primary record. SpendSift snapshots it into <Code>snapshots/</Code> before every write, but that lives in the same folder — keep an off-site copy too.
          </Callout>
        </S>

        {/* ── Institutions ── */}
        <S id="institutions">
          <H2>Supported Institutions</H2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Institution</th><th>Type</th><th>Format</th><th>Status</th></tr></thead>
              <tbody>
                {[
                  ["HDFC Bank", "Savings + Credit Card", "CSV", <Pill color="green">Supported</Pill>],
                  ["ICICI Bank", "Savings + Credit Card", "CSV", <Pill color="green">Supported</Pill>],
                  ["IDFC FIRST Bank", "Savings", "CSV", <Pill color="green">Supported</Pill>],
                  ["Groww", "Mutual Fund orders + P&L", "CSV", <Pill color="green">Supported</Pill>],
                  ["Kite (Zerodha)", "Equity trades + P&L", "CSV", <Pill color="amber">In progress</Pill>],
                  ["ledger-cli / hledger", "Plain-text journal merge", ".ledger", <Pill color="blue">Merge import</Pill>],
                ].map(([name, type, fmt, status]) => (
                  <tr key={name as string}>
                    <td>{name}</td><td>{type}</td><td><Code>{fmt as string}</Code></td><td>{status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </S>

        {/* ── Import Flow ── */}
        <S id="import-flow">
          <H2>Import Flow</H2>
          <Flow steps={["Fingerprint file", "Parse & normalize", "Deduplicate", "Categorize", "Review (if low confidence)", "Commit to journal"]} />
          <H3>Uploading a statement</H3>
          <ol style={{ margin: "0.6154rem 0 1.0769rem 1.3846rem", lineHeight: 1.9 }}>
            <li style={{ fontSize: "0.9615rem", color: "var(--text2)" }}>Open <strong>Ledger → Import</strong> in the rail.</li>
            <li style={{ fontSize: "0.9615rem", color: "var(--text2)" }}>Drag a CSV onto the drop zone (or click Browse).</li>
            <li style={{ fontSize: "0.9615rem", color: "var(--text2)" }}>If the institution is new, the Map step walks you through column assignment once — the template is saved after that.</li>
            <li style={{ fontSize: "0.9615rem", color: "var(--text2)" }}>Confirm the source account and click <strong>Import</strong>. High-confidence rows commit immediately; low-confidence rows go to Review.</li>
          </ol>
          <H3>Investment imports (Groww)</H3>
          <P>Groww CSV imports include an <strong>Investment Review</strong> step — a wallet-flow view of each buy/refund leg with the running Groww Wallet balance. Review lot reconstruction before committing.</P>
          <H3>Ledger file merge</H3>
          <P>Expand <em>Import from ledger-cli / hledger file</em> on the Import screen. SpendSift maps ledger accounts to SpendSift accounts and deduplicates before writing.</P>
          <Callout kind="info" title="Traceability">Every imported transaction records its source file. Filter by source in <strong>Ledger → Transactions</strong> to audit any import.</Callout>
        </S>

        {/* ── Review Queue ── */}
        <S id="review">
          <H2>Review Queue</H2>
          <P>The review screen uses a CodeMirror-based beancount editor — you see the entries as they'll be written and can edit inline. Only rows that need a decision appear here.</P>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Why flagged</th><th>What to do</th></tr></thead>
              <tbody>
                <tr><td>Unknown category</td><td>Pick a category; SpendSift learns and won't ask again for the same merchant</td></tr>
                <tr><td>Ambiguous transfer</td><td>Confirm whether this is a same-owner transfer or a real expense</td></tr>
                <tr><td>New institution</td><td>Walk through one-time column mapping in the Map step</td></tr>
                <tr><td>Low-confidence parse</td><td>Verify the parsed date and amount in the editor</td></tr>
              </tbody>
            </table>
          </div>
          <Callout kind="warn" title="Snapshot on commit">Clicking Commit writes to <Code>storage/journal/</Code> and snapshots the previous state into <Code>storage/snapshots/</Code> first.</Callout>
        </S>

        {/* ── Dashboard ── */}
        <S id="dashboard">
          <H2>Dashboard</H2>
          <P>The home screen: where you stand <strong>today</strong>. It has no period picker; the date beside the title is today. Everything on it links to the screen that explains it.</P>
          <dl style={{ margin: "0.7692rem 0" }}>
            {[
              ["Needs action", "A red pill at the top right with a count, shown only when there is something to do. Its list: a card bill overdue or due within 7 days (noting a payment already made that the card statement does not show yet), this month's transactions that still need a category, card statements expected in the next 3 weeks, and open bank accounts with no new entries for more than 14 days. Each item has a button to the place that resolves it."],
              ["Net worth", "The figure, its change over 12 months and this month, and a 12-month line. Beside it the breakdown as a sum that adds up exactly: Investments (XIRR, gain) + Bank and cash + Other assets − Liabilities. Bank and cash shows months of cover: the balance divided by your average monthly spending over the last 3 complete months. Liabilities shows EMIs and what the cards owe after payments on their way. Click a line to open Net Worth with that section highlighted. The figures are the ones Net Worth shows."],
              ["Cash flow", "For this month, the FY so far or the last 12 months: money in, spent, invested, net cash flow, savings rate, and where the income went (spent, invested, kept, or taken from savings)."],
              ["Spending", "Average monthly spending over the last 3, 6 and 12 months and lifestyle drift (3-month against 12-month). Months with no spending are left out, and a tile covering fewer months says so ('avg over 4 months'). All spending | Without one-offs switches the averages and the 12-month bars; click a month to open it in Expense."],
              ["One-time spends", "Spends from the last three months that are not recurring and are at least 3 times the usual amount for their category. Subscriptions, premiums and other regular payments never count, however large, and refunds never count. Click one to open its month in Expense."],
            ].map(([dt, dd]) => (
              <div key={dt as string} style={{ marginBottom: "0.7692rem" }}>
                <dt style={{ fontWeight: 600, fontSize: "0.9615rem", color: "var(--text)" }}>{dt}</dt>
                <dd style={{ marginLeft: 0, paddingLeft: "1.0769rem", borderLeft: "2px solid var(--border)", fontSize: "0.9615rem", color: "var(--text2)", marginTop: 3 }}>{dd}</dd>
              </div>
            ))}
          </dl>
        </S>

        {/* ── Net Worth ── */}
        <S id="networth">
          <H2>Net Worth</H2>
          <P>One screen with four tabs: <strong>Overview</strong> (where the rail item lands), <strong>Investments</strong>, <strong>Liabilities</strong> and <strong>Allocation</strong>. The <strong>Liabilities</strong> rail item is a shortcut to the Liabilities tab.</P>
          <dl style={{ margin: "0.7692rem 0" }}>
            {[
              ["Overview", "Net worth as of today, the same figures as the Dashboard. The tab bar shows the date, whether the journal is valid, and a badge for a bank account with no activity for more than 14 days (its import is probably overdue). Below: four tiles (net worth with its change this FY and this month, assets, liabilities, and the investments return as XIRR over the lots you hold now), a bar of what your assets are made of with a 'Not counted' line for prepaid tax (TDS, advance tax), then one card per group: Investments, Property, Bank & cash and Other assets, then Loans & EMIs, Credit cards, People and Other liabilities. A group with no accounts has no card. Each card lists its top accounts. Click a card to open its tab (Investments, Liabilities) or the Ledger filtered to that group. Property, FD, PPF, NPS and LIC are at cost: no return is shown for them."],
              ["Investments", "The hub. One card per investment vehicle found in the journal — Mutual Fund, Stock, FD, IndWealth — each with invested, current value, gain and return, plus its own as-of date and live/cached/stale badge. A vehicle with no valuation source shows 'not yet valued' rather than a zero. Click a card to drill into that vehicle. The tab bar has a benchmark dropdown (Nifty 50 by default; also Nifty 500, Sensex, Midcap 150, gold, FD, PPF, CPI and Blended, which weighs each vehicle's own index by its share). Your choice is remembered in this browser. The 'Return by vehicle' table under the hub compares each vehicle's XIRR with it. Benchmark returns are rough long-run figures from config/benchmarks.yaml in the data folder; edit that file to change them."],
              ["Liabilities", "The former Liabilities screens as views: Credit cards, Interest, Repayment and Balance (see Liabilities below)."],
              ["Allocation", "Donut and ranked bars showing how the portfolio is distributed across vehicles and issuers."],
            ].map(([dt, dd]) => (
              <div key={dt as string} style={{ marginBottom: "0.7692rem" }}>
                <dt style={{ fontWeight: 600, fontSize: "0.9615rem", color: "var(--text)" }}>{dt}</dt>
                <dd style={{ marginLeft: 0, paddingLeft: "1.0769rem", borderLeft: "2px solid var(--border)", fontSize: "0.9615rem", color: "var(--text2)", marginTop: 3 }}>{dd}</dd>
              </div>
            ))}
          </dl>
          <H3>Vehicle screens</H3>
          <P>Reached by clicking a card on <strong>Net Worth → Investments</strong>; the breadcrumb walks back up.</P>
          <dl style={{ margin: "0.7692rem 0" }}>
            {[
              ["Mutual Fund", "One row per scheme — invested, value, gain, gain %, and current XIRR — with lifetime XIRR and absolute return in the stat blocks. Each row carries its own NAV date badge, and the header badge follows the OLDEST holding so a fresh scheme cannot mask a stale one. Schemes missing from the AMFI feed are listed at cost in a 'Not valued' panel. The Price Returns panel (1D/1M/1Y/SI) sits at the bottom of this screen."],
              ["Stock", "One row per symbol — invested, value, gain and a derived gain % — each with its own close date badge. Symbols with no close are listed at cost under 'Not valued' and excluded from the totals, which say so explicitly."],
              ["FD / IndWealth", "At-cost vehicles. No value, gain, return or freshness badge is shown, because accrued interest and user marks are not modelled yet; the screen states the reason instead of printing a figure it cannot support."],
            ].map(([dt, dd]) => (
              <div key={dt as string} style={{ marginBottom: "0.7692rem" }}>
                <dt style={{ fontWeight: 600, fontSize: "0.9615rem", color: "var(--text)" }}>{dt}</dt>
                <dd style={{ marginLeft: 0, paddingLeft: "1.0769rem", borderLeft: "2px solid var(--border)", fontSize: "0.9615rem", color: "var(--text2)", marginTop: 3 }}>{dd}</dd>
              </div>
            ))}
          </dl>
        </S>

        {/* ── NAV Prices ── */}
        <S id="nav-prices">
          <H2>NAV Prices</H2>
          <P>SpendSift fetches mutual fund NAV from AMFI and stock closes from the NSE bhavcopy. Both feeds are listed, dated and refreshed from the <strong>Refresh</strong> control on <strong>Net Worth → Investments</strong> (the arrow opens the per-feed list); every investment screen repeats its own as-of date and state in a strip above the figures.</P>
          <H3>Status badges</H3>
          <P>A badge classifies how old the price date is, not whether the last fetch succeeded — the same seven-day threshold everywhere:</P>
          <div style={{ display: "flex", flexDirection: "column", gap: "0.6154rem", margin: "0.7692rem 0" }}>
            {[
              { cls: "live",    label: "live",    desc: "Priced today" },
              { cls: "cached",  label: "cached",  desc: "Priced within the last 7 days" },
              { cls: "stale",   label: "stale",   desc: "Price date is more than 7 days old" },
            ].map(s => (
              <div key={s.cls} style={{ display: "flex", alignItems: "center", gap: "0.9231rem", fontSize: "0.9615rem", color: "var(--text2)" }}>
                <span className={`src ${s.cls}`} style={{ flexShrink: 0, fontFamily: "var(--mono)", fontSize: "0.7692rem", display: "inline-flex", alignItems: "center", gap: "0.4615rem", padding: "2px 0.6923rem 2px 0.5385rem", borderRadius: "1.5385rem" }}>
                  <span style={{ width: "0.5385rem", height: "0.5385rem", borderRadius: "50%", background: s.cls === "live" ? "var(--green)" : s.cls === "cached" ? "var(--blue)" : "var(--amber)", display: "inline-block" }} />
                  {s.label}
                </span>
                {s.desc}
              </div>
            ))}
            <div style={{ fontSize: "0.9615rem", color: "var(--text2)", display: "flex", alignItems: "center", gap: "0.9231rem" }}>
              <Code>Prices not fetched yet</Code>
              No prices on disk yet — no state is claimed at all
            </div>
          </div>
          <P>The strip renders the as-of date in amber once it is stale. Separately, the per-feed list under the Refresh control records what the last run actually did: fetched from the source, source had nothing newer, fetch failed (last known values kept), or never run.</P>
          <H3>Backfill for price-change windows</H3>
          <P>The <strong>Price Returns</strong> panel at the bottom of <strong>Net Worth → Investments → Mutual Fund</strong> shows 1D/1M/1Y/SI returns per fund and for the portfolio. A dash (—) means no historical NAV for that window yet. When the panel shows a backfill callout, click <strong>Run backfill</strong> — SpendSift fetches per-fund history from mfapi.in at 200 ms/fund.</P>
        </S>

        {/* ── Cash Flow ── */}
        <S id="cashflow">
          <H2>Cash Flow</H2>
          <dl style={{ margin: "0.7692rem 0" }}>
            {[
              ["Overview", "For the period in the header: income, expenses, invested, saved and savings rate; a daily or monthly cash-flow chart as a heatmap or bars (Both | Income | Expenses, Heatmap | Bars, Linear | Compressed, remembered); the Flow diagram of money moving between accounts, income and spending (Summary | Detailed), with refunds and repayments as loops underneath; and a statement of income and expenses by category. Click a day, month, account or flow for the transactions behind it."],
              ["Compare FYs", "The switch at the top of Overview shows financial years side by side, income and expenses by category, for a span of FYs you pick."],
              ["Recurring", "Auto-detected subscriptions and recurring expenses shown as cards — frequency, next expected date, average amount."],
            ].map(([dt, dd]) => (
              <div key={dt as string} style={{ marginBottom: "0.7692rem" }}>
                <dt style={{ fontWeight: 600, fontSize: "0.9615rem", color: "var(--text)" }}>{dt}</dt>
                <dd style={{ marginLeft: 0, paddingLeft: "1.0769rem", borderLeft: "2px solid var(--border)", fontSize: "0.9615rem", color: "var(--text2)", marginTop: 3 }}>{dd}</dd>
              </div>
            ))}
          </dl>
        </S>

        {/* ── Expense ── */}
        <S id="expense">
          <H2>Expense</H2>
          <dl style={{ margin: "0.7692rem 0" }}>
            {[
              ["Overview", "Spending for the period in the header: net spend, spent before refunds, refunds, and the card / bank / other split. A daily or monthly chart (heatmap or bars), categories ranked by amount (hover for the top merchants), and the transactions. All | Card | Bank | Other filters the whole screen by how you paid. Click a day, month or category for its transactions."],
              ["By Tag", "Total spend grouped by tag. Useful for cross-category groups like #vacation-goa or #tax-deductible."],
              ["Uncategorized", "Merchants still booked as uncategorized, so you can categorize them."],
              ["Budget", "Set a monthly target per category. Shows current-month spend vs target with a progress bar. This is where budget targets are configured — not in Settings."],
            ].map(([dt, dd]) => (
              <div key={dt as string} style={{ marginBottom: "0.7692rem" }}>
                <dt style={{ fontWeight: 600, fontSize: "0.9615rem", color: "var(--text)" }}>{dt}</dt>
                <dd style={{ marginLeft: 0, paddingLeft: "1.0769rem", borderLeft: "2px solid var(--border)", fontSize: "0.9615rem", color: "var(--text2)", marginTop: 3 }}>{dd}</dd>
              </div>
            ))}
          </dl>
        </S>

        {/* ── Liabilities ── */}
        <S id="liabilities">
          <H2>Liabilities</H2>
          <P>The <strong>Liabilities</strong> tab of <strong>Net Worth</strong> (the Liabilities rail item opens it). Its views:</P>
          <dl style={{ margin: "0.7692rem 0" }}>
            {[
              ["Credit Cards", "Cards shown as selectable chips. Click a card to see outstanding balance, latest statement date, and a breakdown of charges vs payments."],
              ["Interest", "Finance charges, late fees, and annual fees detected from statement narrations."],
              ["Repayment", "Timeline of payments made against each liability. Bank-to-card transfers are matched automatically."],
              ["Balance", "Full double-entry balance for the liabilities section — outstanding across all cards and loans."],
            ].map(([dt, dd]) => (
              <div key={dt as string} style={{ marginBottom: "0.7692rem" }}>
                <dt style={{ fontWeight: 600, fontSize: "0.9615rem", color: "var(--text)" }}>{dt}</dt>
                <dd style={{ marginLeft: 0, paddingLeft: "1.0769rem", borderLeft: "2px solid var(--border)", fontSize: "0.9615rem", color: "var(--text2)", marginTop: 3 }}>{dd}</dd>
              </div>
            ))}
          </dl>
        </S>

        {/* ── Income ── */}
        <S id="income">
          <H2>Income</H2>
          <P><strong>Overview</strong> shows money received in the period in the header: net, received before deductions, and deductions such as TDS; the same chart as Expense in green (a reversal shows red); sources (salary, interest, …) ranked by amount, with the payers on hover; and the list. All | Card | Bank | Other filters by where the money arrived. <strong>By Tag</strong> groups tagged income.</P>
        </S>

        {/* ── Ledger ── */}
        <S id="ledger">
          <H2>Ledger</H2>
          <dl style={{ margin: "0.7692rem 0" }}>
            {[
              ["Transactions", "The full transaction list — every committed journal entry. Filter by date, account, category, tag, amount, narration, or source file. Click any row to edit category, add tags, or add a note."],
              ["Raw", "CodeMirror beancount editor for the full journal. Syntax highlighting for dates, narrations, accounts, amounts, comments, and tags. Changes are validated before saving."],
              ["Import", "Upload a statement file, walk through column mapping, review, and commit. Also the entry point for ledger-cli / hledger journal merges."],
            ].map(([dt, dd]) => (
              <div key={dt as string} style={{ marginBottom: "0.7692rem" }}>
                <dt style={{ fontWeight: 600, fontSize: "0.9615rem", color: "var(--text)" }}>{dt}</dt>
                <dd style={{ marginLeft: 0, paddingLeft: "1.0769rem", borderLeft: "2px solid var(--border)", fontSize: "0.9615rem", color: "var(--text2)", marginTop: 3 }}>{dd}</dd>
              </div>
            ))}
          </dl>
        </S>

        {/* ── Planning ── */}
        <S id="planning">
          <H2>Planning</H2>
          <P>Five calculators — all local, no data sent anywhere:</P>
          <dl style={{ margin: "0.7692rem 0" }}>
            {[
              ["Goal Planner", "Define financial goals (amount + target year). Projects whether your current savings rate will reach each goal. Shows a funded/short verdict per goal."],
              ["SIP Calculator", "Given a monthly SIP amount and CAGR, projects corpus at maturity with an area chart of invested vs total value."],
              ["Lumpsum Calculator", "Projects future value of a one-time investment at a given rate."],
              ["CAGR Calculator", "Computes CAGR from initial/final value and duration; or reverse: given CAGR + duration, computes final value."],
              ["In-Hand Salary", "Gross-to-net salary calculator for Indian payroll — PF, income tax (old/new regime), professional tax, HRA. Includes a 'Tax working' breakdown panel."],
              ["FIRE Calculator", "Plans a Financial Independence / Retire Early target: total corpus needed, a 5-bucket allocation plan, the growth-bucket fund breakdown, required SIP during accumulation, and a year-by-year depletion projection during decumulation."],
            ].map(([dt, dd]) => (
              <div key={dt as string} style={{ marginBottom: "0.7692rem" }}>
                <dt style={{ fontWeight: 600, fontSize: "0.9615rem", color: "var(--text)" }}>{dt}</dt>
                <dd style={{ marginLeft: 0, paddingLeft: "1.0769rem", borderLeft: "2px solid var(--border)", fontSize: "0.9615rem", color: "var(--text2)", marginTop: 3 }}>{dd}</dd>
              </div>
            ))}
          </dl>
        </S>

        {/* ── Tax ── */}
        <S id="tax">
          <H2>Tax Workspace</H2>
          <P>Tax planning is a first-class surface. Everything traces back to imported transactions and holdings. A financial year selector at the top of the Tax rail group lets you switch between FYs.</P>
          <dl style={{ margin: "0.7692rem 0" }}>
            {[
              ["Overview", "Estimated total tax liability for the selected FY under old and new regime, side by side."],
              ["Income", "Gross income by source — salary, interest, dividends, rental. Each line traces to the underlying transactions."],
              ["Capital Gains", "STCG and LTCG from MF disposals using FIFO lot matching, grouped by asset class with applicable tax rate. Realized gains from equity/stock sales are recorded in the journal but not yet classified into this view."],
              ["Deductions", "80C, 80D, and other declared deductions. User-declared amounts (not auto-detected from imports yet)."],
              ["Regime Compare", "Side-by-side old vs new regime with your actual numbers."],
              ["Prepaid Tax", "TDS and advance tax payments found in transactions, matched against estimated liability."],
            ].map(([dt, dd]) => (
              <div key={dt as string} style={{ marginBottom: "0.7692rem" }}>
                <dt style={{ fontWeight: 600, fontSize: "0.9615rem", color: "var(--text)" }}>{dt}</dt>
                <dd style={{ marginLeft: 0, paddingLeft: "1.0769rem", borderLeft: "2px solid var(--border)", fontSize: "0.9615rem", color: "var(--text2)", marginTop: 3 }}>{dd}</dd>
              </div>
            ))}
          </dl>
          <Callout kind="warn" title="Not tax advice">SpendSift computes estimates from imported data and declared deductions. Consult a CA for filing decisions.</Callout>
        </S>

        {/* ── Quick Add ── */}
        <S id="quick-add">
          <H2>Quick Add</H2>
          <P>Record a one-off transaction without a statement file. Quick Add is in the rail footer — click <strong>＋ Quick add</strong> or press <Kbd>n</Kbd> from any screen (when no input has focus).</P>
          <ol style={{ margin: "0.6154rem 0 1.0769rem 1.3846rem", lineHeight: 1.9 }}>
            <li style={{ fontSize: "0.9615rem", color: "var(--text2)" }}>Press <Kbd>n</Kbd> or click <strong>＋ Quick add</strong> in the rail footer.</li>
            <li style={{ fontSize: "0.9615rem", color: "var(--text2)" }}>Toggle between <strong>Bank</strong> (debit/credit) and <strong>Investment</strong> (buy/sell).</li>
            <li style={{ fontSize: "0.9615rem", color: "var(--text2)" }}>Enter date, amount, narration, and accounts. A net-by-account summary updates live on the right.</li>
            <li style={{ fontSize: "0.9615rem", color: "var(--text2)" }}>Press <Kbd>⌘ Enter</Kbd> or click <strong>Save</strong>. Written to the journal immediately.</li>
          </ol>
        </S>

        {/* ── Settings ── */}
        <S id="settings">
          <H2>Settings</H2>
          <P>Settings has two screens (at the bottom of the rail):</P>
          <dl style={{ margin: "0.7692rem 0" }}>
            {[
              ["Accounts", "Add, rename, or close accounts in the beancount taxonomy. Closing an account prevents new postings while preserving history. Long account lists are shown in a scrollable section per root type."],
              ["Import Templates", "View all saved column-mapping templates. Rename or delete a template. The raw YAML is shown for inspection — you can also edit the file directly in storage/templates/."],
            ].map(([dt, dd]) => (
              <div key={dt as string} style={{ marginBottom: "0.7692rem" }}>
                <dt style={{ fontWeight: 600, fontSize: "0.9615rem", color: "var(--text)" }}>{dt}</dt>
                <dd style={{ marginLeft: 0, paddingLeft: "1.0769rem", borderLeft: "2px solid var(--border)", fontSize: "0.9615rem", color: "var(--text2)", marginTop: 3 }}>{dd}</dd>
              </div>
            ))}
          </dl>
          <Callout kind="info" title="Budget targets">Set monthly budget targets in <strong>Expense → Budget</strong>, not in Settings.</Callout>
          <Callout kind="info" title="Theme">The Midnight / Carbon / Daylight selector is at the very bottom of the rail (below the Settings nav item). Your choice persists in localStorage.</Callout>
        </S>

        {/* ── Journal file ── */}
        <S id="journal">
          <H2>Journal File</H2>
          <P>Every transaction is stored as a beancount entry under <Code>storage/journal/</Code> — one plain-text file per calendar year, included from <Code>main.beancount</Code>. You can read, audit, and version-control it independently of SpendSift.</P>
          <Pre>{`2025-04-15 * "Swiggy"
  ; source: HDFC_Apr2025.csv | tag: #lunch
  Expenses:Food             450.00 INR
  Assets:Checking:HDFC`}</Pre>
          <H3>Restoring from snapshot</H3>
          <P>SpendSift copies the whole <Code>journal/</Code> directory into <Code>storage/snapshots/&lt;timestamp&gt;/journal/</Code> before every write. To roll back:</P>
          <Pre>{`cp -r storage/snapshots/<timestamp>/journal storage/`}</Pre>
          <P>Then restart the server.</P>
        </S>

        {/* ── Troubleshooting ── */}
        <S id="troubleshoot">
          <H2>Troubleshooting</H2>
          <H3>Import fails with "no template matched"</H3>
          <P>Verify the file is the raw CSV export (not PDF/Excel). A new institution walks you through one-time column mapping — the template is saved after that.</P>
          <H3>Holdings show no value / ₹0</H3>
          <P>Prices haven't been fetched. Open <strong>Net Worth → Investments</strong>, open the list beside <strong>Refresh</strong> and refresh the feed that covers them — AMFI NAVAll for mutual funds, NSE bhavcopy for stocks.</P>
          <H3>Price Returns panel shows all dashes</H3>
          <P>Historical NAV not yet backfilled. Click <strong>Run backfill</strong> in the Price Returns panel when it shows a backfill callout.</P>
          <H3>Journal is corrupt / server won't start</H3>
          <Pre>{`cp -r storage/snapshots/<timestamp>/journal storage/`}</Pre>
          <H3>Frontend shows a blank page</H3>
          <Pre>{`cd frontend && npm run build`}</Pre>
          <P>Then restart the server.</P>
          <H3>Port 8765 in use</H3>
          <Pre>{`lsof -i :8765
SPENDSIFT_PORT=8766 ./.venv/bin/python -m backend.app`}</Pre>
        </S>

      </div>
    </div>
  );
}
