"""The transactions behind a cash-flow view: a Sankey node or link, or the
income/expense lines of a range, a category or a side. Feeds the detail modal
(`GET /api/reports/cashflow/rows`); output is `CashflowRowsResponse`.

- **Sankey selection** (`node`, or `source` + `target`, at a `depth`): one row
  per transaction and drawn flow, its amount the flow's share
  (`sankey.link_flows`). For a node, every flow into or out of it. A flow
  into or out of `Other` keeps its real (pre-fold) category, so `by_category`
  breaks `Other` into its members.
- **Line selection** (otherwise): one row per income or spending posting in
  the range (`Income:*` and `is_spending_account`, as the cash-flow overview
  counts them), filtered by `category` (that account or under it) and `side`.
- **Row.** `account` is where the money sat: for a flow, its own-account end
  (for a move between two accounts, the selected node, else the source; none
  between two income/expense nodes); for a line, the account that funded it
  (`funding.funding_leg`; the card clearing account reads as `Card bills`).
  `category` is the other end (for a payer or sub-account flow, the payer or
  sub-account). Both are Sankey node ids, with their labels for display
  (`*_display`, scheme codes resolved). `amount` is positive; `direction`
  says which way: `in` is money received (income, or a refund out of an
  expense), `out` money spent (into an expense, tax withheld, or income
  handed back), `move` anything between own accounts.
- Unpaginated: a whole range with no selector returns every line.
- **Totals.** `received`, `spent`, `moved` sum the `in`, `out`, `move` rows at
  full precision; `count` is distinct transactions. `by_category` gives the
  same per category, largest first.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from backend.app.core.errors.errors import ROWS_SELECTOR_INVALID
from backend.app.models.domain.accounts import is_spending_account
from backend.app.service.reports.cashflow.funding import funding_leg
from backend.app.service.reports.cashflow.money import q
from backend.app.service.reports.cashflow.period import DateRange
from backend.app.service.reports.cashflow.sankey import (
    CARD_BILLS,
    Depth,
    Node,
    build_graph,
    classify,
    is_clearing,
    link_flows,
)
from backend.app.service.reports.health.ledger_view import (
    PostingRow,
    TransactionRow,
    display_account,
)

Direction = Literal["in", "out", "move"]
RowSide = Literal["all", "expense", "income"]

_ZERO = Decimal(0)


@dataclass(frozen=True)
class Row:
    txn: TransactionRow
    account: Node | None
    category: Node
    amount: Decimal  # > 0
    direction: Direction


def _direction(source: Node, target: Node) -> Direction:
    # Tax withheld from income, or income handed back (type -> payer).
    if target.role in ("payer", "tax"):
        return "out"
    if source.kind == "income":
        return "in"
    if target.kind in ("income", "expense"):
        return "out"
    if source.kind == "expense":
        return "in"
    return "move"


def _ends(source: Node, target: Node, selected: str | None) -> tuple[Node | None, Node]:
    """(account, category) of a flow."""
    s_acct, t_acct = source.role == "account", target.role == "account"
    if s_acct and t_acct:
        return (target, source) if selected == target.id else (source, target)
    if s_acct:
        return source, target
    if t_acct:
        return target, source
    if source.role == "payer":
        return None, source
    return None, target


def _graph_rows(
    txns: list[TransactionRow],
    r: DateRange,
    node: str | None,
    link: tuple[str, str] | None,
    *,
    depth: Depth,
    clearing: str,
    closed: frozenset[str],
    card_postings: list[PostingRow] | None,
) -> list[Row]:
    g = build_graph(
        txns,
        r,
        depth=depth,
        clearing=clearing,
        closed=closed,
        card_postings=card_postings,
    )
    out: list[Row] = []
    for lf in link_flows(g):
        pair = (lf.source.id, lf.target.id)
        if link is not None and pair != link:
            continue
        if node is not None and node not in pair:
            continue
        # The pre-fold nodes: a flow into `Other` keeps its real category.
        src, tgt = lf.real_source, lf.real_target
        account, category = _ends(src, tgt, node)
        out.append(Row(lf.txn, account, category, lf.amount, _direction(src, tgt)))
    return out


def _funding_node(account: str, clearing: str) -> Node | None:
    """The node a line's funding account is drawn as; the card clearing
    account is never a node, so it reads as `Card bills`."""
    return CARD_BILLS if is_clearing(account, clearing) else classify(account)


def _line_rows(
    txns: list[TransactionRow],
    r: DateRange,
    category: str | None,
    side: RowSide,
    clearing: str,
) -> list[Row]:
    out: list[Row] = []
    for txn in txns:
        if not r.start <= txn.date <= r.end:
            continue
        for p in txn.postings:
            income = p.account.startswith("Income:")
            if p.inr_amount == 0 or not (income or is_spending_account(p.account)):
                continue
            if side != "all" and side != ("income" if income else "expense"):
                continue
            if category is not None and not (
                p.account == category or p.account.startswith(category + ":")
            ):
                continue
            node = classify(p.account)
            if node is None:  # unreachable: Income:* / Expenses:* classify
                continue
            via = funding_leg(txn, "Income:" if income else "Expenses:")
            # Money received is positive: income legs are credits.
            signed = -p.inr_amount if income else p.inr_amount
            out.append(
                Row(
                    txn,
                    _funding_node(via.account, clearing) if via else None,
                    node,
                    abs(signed),
                    ("in" if signed > 0 else "out")
                    if income
                    else ("out" if signed > 0 else "in"),
                )
            )
    return out


def _sum(rows: list[Row], direction: Direction) -> str:
    return q(sum((x.amount for x in rows if x.direction == direction), _ZERO))


def _totals(rows: list[Row]) -> dict:
    return {
        "received": _sum(rows, "in"),
        "spent": _sum(rows, "out"),
        "moved": _sum(rows, "move"),
        "count": len({x.txn.txn_id for x in rows}),
    }


def rows(
    txns: list[TransactionRow],
    r: DateRange,
    *,
    node: str | None = None,
    source: str | None = None,
    target: str | None = None,
    category: str | None = None,
    side: RowSide = "all",
    depth: Depth = "detailed",
    clearing: str,
    closed: frozenset[str] = frozenset(),
    card_postings: list[PostingRow] | None = None,
    names: dict[str, str] | None = None,
) -> dict:
    """The `CashflowRowsResponse` shape. At most one selector: `node`,
    `source` + `target` together, or `category` (with `side` only on a line
    selection); anything else is E1098. `txns`, `clearing`, `closed` and
    `card_postings` as `sankey.sankey`; `names` (`ledger_view.commodity_names`)
    resolves a scheme code in a displayed label."""
    names = names or {}
    has_link = source is not None or target is not None
    selectors = sum([node is not None, has_link, category is not None])
    if selectors > 1:
        raise ROWS_SELECTOR_INVALID.exc(
            {"reason": "pass one of node, source and target, or category"}
        )
    if has_link and (source is None or target is None):
        raise ROWS_SELECTOR_INVALID.exc({"reason": "pass both source and target"})
    if (node is not None or has_link) and side != "all":
        raise ROWS_SELECTOR_INVALID.exc(
            {"reason": "side applies only without node or source and target"}
        )
    if node is not None or has_link:
        found = _graph_rows(
            txns,
            r,
            node,
            (source, target) if source is not None and target is not None else None,
            depth=depth,
            clearing=clearing,
            closed=closed,
            card_postings=card_postings,
        )
    else:
        found = _line_rows(txns, r, category, side, clearing)
    found.sort(key=lambda x: (-x.amount, -x.txn.date.toordinal(), x.txn.narration))

    by_cat: dict[str, list[Row]] = {}
    for x in found:
        by_cat.setdefault(x.category.id, []).append(x)
    groups = [
        {
            "category": cid,
            "label": display_account(group[0].category.label, names),
            **_totals(group),
        }
        for cid, group in by_cat.items()
    ]
    groups.sort(
        key=lambda gr: (
            -sum(Decimal(gr[k]) for k in ("received", "spent", "moved")),
            gr["category"],
        )
    )
    return {
        "start": r.start.isoformat(),
        "end": r.end.isoformat(),
        "totals": _totals(found),
        "by_category": groups,
        "rows": [
            {
                "txn_id": x.txn.txn_id,
                "date": x.txn.date.isoformat(),
                "name": x.txn.narration,
                "account": x.account.id if x.account else None,
                "account_display": (
                    display_account(x.account.label, names) if x.account else None
                ),
                "category": x.category.id,
                "category_display": display_account(x.category.label, names),
                "amount": q(x.amount),
                "direction": x.direction,
                "editable": x.txn.tracked,
            }
            for x in found
        ],
    }
