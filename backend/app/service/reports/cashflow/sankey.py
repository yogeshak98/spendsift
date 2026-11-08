"""The cash-flow Sankey, account by account (Paisa-style): payer -> income type
-> (tax withheld) -> the account it landed in -> account to account
(investments, the other bank, cash, people, each credit card through its
bills) -> expense category -> sub-account.

Its output is `SankeyResponse` (`backend/app/models/api/reports.py`).

- **Nodes** (`classify`). `Income:*` with a `TDS` segment -> `Tax withheld`
  (role `tax`); other `Income:*` -> its type (`Income:Salary`, role
  `income`); spending `Expenses:*` -> its category (`Expenses:Food`, role
  `expense`); `Expenses:Investment:*` -> `Expenses:Investment` ("Investment
  charges", an asset `account`); every other `Assets:*` / `Liabilities:*` ->
  its first three segments (role `account`). `Equity:*` maps to nothing.
  Detailed depth adds the payer column (`Income:Salary:314E`, the first three
  segments, or `"<type> (direct)"` for a posting on the type itself) and the
  sub-account column (`Expenses:Bike:Servicing`, first three segments).
- **Columns** (`COLUMN`): payer 0, income 1, tax 2, account 3, expense 4,
  sub 5.
- **Card bills.** The card clearing account is never a node. A bank payment
  into it is drawn to the cards `liabilities/in_transit.match_clearing` ties
  it to: the card legs of the receipt that cleared it, split by their
  amounts, else the one card it was attributed to, else `Card bills`. A
  receipt's clearing leg is split over its own card legs, so it nets to
  nothing; any other clearing leg is `Card bills`.
- **Flows.** Per transaction, legs are netted per node; each node that gives
  money (net < 0) sends to each that takes (net > 0) its share:
  `give * take / max(total given, total taken)`. The payer and sub-account
  links come per posting: payer -> type for income, type -> payer for income
  handed back; category -> sub for spending, sub -> category for a refund.
- **No netting; back links.** Flows are summed per (source, target) and both
  directions kept. A link that does not run to a later column (other than
  one between two accounts) is a back link (`back: true`). The rest are taken
  largest first, and one whose target already reaches its source over the
  links kept so far is a back link too. The forward links form a DAG that
  runs left to right.
- **Folding.** Expense categories under 3% of total expenses fold into one
  `Other` node (its sub-accounts are dropped); `folded` lists them.
- **Amounts.** Every node but an account takes its amount from its postings
  only (one that nets to zero in the range is 0): a payer or type, minus its
  postings (gross of TDS); tax withheld, its postings; a category or
  sub-account, its postings. An account's is max(in, out) over its links.
  Payers and types each sum to `totals.income` + tax withheld; categories to
  `totals.expenses` (the cash-flow overview's totals for the range).
  Rounding is allocated per column so each column's strings sum to its
  rounded total.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from backend.app.models.domain.accounts import (
    CREDIT_CARD_PREFIX,
    INVESTMENT_EXPENSE,
    is_spending_account,
)
from backend.app.service.reports.cashflow.money import allocate, cents, q
from backend.app.service.reports.cashflow.period import DateRange
from backend.app.service.reports.health.ledger_view import (
    PostingRow,
    TransactionRow,
    display_account,
    top_category,
)
from backend.app.service.reports.liabilities.in_transit import match_clearing

NodeKind = Literal["income", "asset", "liability", "expense"]
NodeRole = Literal["payer", "income", "tax", "account", "expense", "sub"]
Depth = Literal["summary", "detailed"]

_ZERO = Decimal(0)
# Below this a summed link is rounding noise and is dropped.
_EPSILON = Decimal("0.005")
FOLD_SHARE = Decimal("0.03")
DIRECT_SUFFIX = " (direct)"


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    kind: NodeKind
    role: NodeRole


CARD_BILLS = Node("Card bills", "Card bills", "liability", "account")
OTHER = Node("Other", "Other", "expense", "expense")
TAX_WITHHELD = Node("Tax withheld", "Tax withheld", "expense", "tax")
INVESTMENT_CHARGES = Node(INVESTMENT_EXPENSE, "Investment charges", "asset", "account")
# Columns dropped at summary depth.
_DETAIL_ROLES = frozenset({"payer", "sub"})
# Left to right. A link is forward only when it runs to a later column, or
# between two accounts (their order comes from the flows themselves).
COLUMN: dict[str, int] = {
    "payer": 0,
    "income": 1,
    "tax": 2,
    "account": 3,
    "expense": 4,
    "sub": 5,
}


def _head(account: str, n: int) -> str:
    return ":".join(account.split(":")[:n])


def _tail(node_id: str) -> str:
    return node_id.split(":", 1)[-1]


def is_withheld(account: str) -> bool:
    """TDS withheld from income: an `Income:*` account with a `TDS` segment
    (the cash-flow overview's rule)."""
    return account.startswith("Income:") and "TDS" in account.split(":")


def classify(account: str) -> Node | None:
    """The node `account`'s postings flow through (before the payer and
    sub-account columns), or None."""
    if is_withheld(account):
        return TAX_WITHHELD
    if account.startswith("Income:"):
        cat = top_category(account)
        return Node(cat, _tail(cat), "income", "income")
    if account.startswith("Expenses:"):
        if not is_spending_account(account):
            return INVESTMENT_CHARGES
        cat = top_category(account)
        return Node(cat, _tail(cat), "expense", "expense")
    if account.startswith(("Assets:", "Liabilities:")):
        node_id = _head(account, 3)
        kind: NodeKind = "asset" if account.startswith("Assets:") else "liability"
        return Node(node_id, _tail(node_id), kind, "account")
    return None


def payer(account: str) -> Node:
    """The payer node of a (not withheld) `Income:*` account."""
    segs = account.split(":")
    if len(segs) >= 3:
        return Node(_head(account, 3), segs[2], "income", "payer")
    cat = top_category(account)
    return Node(cat + DIRECT_SUFFIX, _tail(cat), "income", "payer")


def sub_account(account: str) -> Node | None:
    """The sub-account node of a spending account, None when booked on the
    category itself."""
    segs = account.split(":")
    if len(segs) < 3:
        return None
    return Node(_head(account, 3), segs[2], "expense", "sub")


def is_clearing(account: str, clearing: str) -> bool:
    return account == clearing or account.startswith(clearing + ":")


# (card account, weight): a payment's or receipt's split across cards.
CardShares = list[tuple[str, Decimal]]


def _card_legs(txn: TransactionRow) -> CardShares:
    """The card legs of a transaction, weighted by their amounts."""
    legs: dict[str, Decimal] = {}
    for p in txn.postings:
        if p.account.startswith(CREDIT_CARD_PREFIX) and p.inr_amount != 0:
            legs[p.account] = legs.get(p.account, _ZERO) + abs(p.inr_amount)
    return sorted(legs.items())


def card_of_payment(
    txns: list[TransactionRow],
    clearing: str,
    *,
    closed: frozenset[str] = frozenset(),
    card_postings: list[PostingRow] | None = None,
) -> dict[str, CardShares]:
    """Bank payment txn id -> the cards it paid (`in_transit.match_clearing`):
    the card legs of the receipt that cleared it, split by their amounts,
    else the one card it is attributed to. A payment with neither is absent."""
    pending, cleared = match_clearing(
        txns, clearing, closed=closed, card_postings=card_postings
    )
    by_id = {t.txn_id: t for t in txns}
    out: dict[str, CardShares] = {
        p.txn_id: [(p.account, Decimal(1))] for p in pending if p.account is not None
    }
    for c in cleared:
        receipt = by_id.get(c.receipt_txn_id)
        legs = _card_legs(receipt) if receipt is not None else []
        if legs:
            out[c.txn_id] = legs
    return out


@dataclass(frozen=True)
class Flow:
    source: str
    target: str
    amount: Decimal  # > 0


def _clearing_nodes(
    txn: TransactionRow, cards: dict[str, CardShares]
) -> list[tuple[Node, Decimal]]:
    """Where a clearing leg of `txn` is drawn, as (node, share of the leg):
    a receipt's own card legs; a matched payment's cards; else `Card bills`."""
    shares = _card_legs(txn) or cards.get(txn.txn_id) or []
    total = sum((w for _, w in shares), _ZERO)
    out = [(classify(card), w / total) for card, w in shares if total > 0]
    resolved = [(n, w) for n, w in out if n is not None]
    return resolved or [(CARD_BILLS, Decimal(1))]


def txn_flows(
    txn: TransactionRow,
    clearing: str,
    cards: dict[str, CardShares],
    nodes: dict[str, Node],
) -> list[Flow]:
    """Every flow of one transaction, payer and sub-account links included;
    registers the nodes it touches in `nodes`."""
    nets: dict[str, Decimal] = {}
    flows: list[Flow] = []

    def reg(node: Node) -> str:
        nodes.setdefault(node.id, node)
        return node.id

    for p in txn.postings:
        amt = p.inr_amount
        if amt == 0:
            continue
        if is_clearing(p.account, clearing):
            for node, share in _clearing_nodes(txn, cards):
                _add(nets, reg(node), amt * share)
            continue
        node = classify(p.account)
        if node is None:
            continue
        nid = reg(node)
        _add(nets, nid, amt)
        if node.role == "income":
            pid = reg(payer(p.account))
            flows.append(Flow(pid, nid, -amt) if amt < 0 else Flow(nid, pid, amt))
        elif node.role == "expense":
            sub = sub_account(p.account)
            if sub is not None:
                sid = reg(sub)
                flows.append(Flow(nid, sid, amt) if amt > 0 else Flow(sid, nid, -amt))
    given = -sum((v for v in nets.values() if v < 0), _ZERO)
    taken = sum((v for v in nets.values() if v > 0), _ZERO)
    moved = min(given, taken)
    if moved == 0:
        return flows
    for src, g in nets.items():
        if g >= 0:
            continue
        for dst, t in nets.items():
            if t > 0:
                flows.append(Flow(src, dst, -g / given * t / taken * moved))
    return flows


def _windowed(txns: list[TransactionRow], r: DateRange) -> list[TransactionRow]:
    return [t for t in txns if r.start <= t.date <= r.end]


def _add(d: dict[str, Decimal], key: str, v: Decimal) -> None:
    d[key] = d.get(key, _ZERO) + v


@dataclass
class Graph:
    nodes: dict[str, Node]  # every node met, before folding
    mapping: dict[str, str | None]  # node id -> drawn id; None: not drawn
    links: dict[tuple[str, str], Decimal]
    back: set[tuple[str, str]]
    flows: list[tuple[TransactionRow, list[Flow]]]  # windowed, unmapped
    # Node amounts from postings (zero-net entries kept: C1).
    payers: dict[str, Decimal]
    types: dict[str, Decimal]
    withheld: dict[str, Decimal]
    categories: dict[str, Decimal]  # after folding
    subs: dict[str, Decimal]
    folded: list[tuple[str, Decimal]]  # the categories in `Other`
    income: Decimal
    expenses: Decimal

    def drawn(self, node_id: str) -> str | None:
        return self.mapping.get(node_id, node_id)

    def node(self, node_id: str) -> Node:
        return OTHER if node_id == OTHER.id else self.nodes[node_id]


def is_back_by_column(source: Node, target: Node) -> bool:
    """A link that does not run to a later column, other than one between
    two accounts."""
    s, t = COLUMN[source.role], COLUMN[target.role]
    return t < s or (t == s and source.role != "account")


def _back_edges(
    links: dict[tuple[str, str], Decimal], node: Callable[[str], Node]
) -> set[tuple[str, str]]:
    """A link against the column order is a back edge. The rest are taken
    largest first; one whose target reaches its source over the links kept so
    far is a back edge too (only links between accounts can do that)."""
    edges: dict[str, set[str]] = {}
    back: set[tuple[str, str]] = set()
    for (a, b), _ in sorted(links.items(), key=lambda kv: (-kv[1], kv[0])):
        if is_back_by_column(node(a), node(b)) or _reaches(edges, b, a):
            back.add((a, b))
        else:
            edges.setdefault(a, set()).add(b)
    return back


def _reaches(edges: dict[str, set[str]], start: str, goal: str) -> bool:
    stack, seen = [start], {start}
    while stack:
        cur = stack.pop()
        if cur == goal:
            return True
        for nxt in edges.get(cur, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False


def build_graph(
    txns: list[TransactionRow],
    r: DateRange,
    *,
    depth: Depth,
    clearing: str,
    closed: frozenset[str] = frozenset(),
    card_postings: list[PostingRow] | None = None,
) -> Graph:
    """The graph for `r`. `txns` may extend beyond `r` (card bills are matched
    over all of them); only those dated in `r` are drawn."""
    cards = card_of_payment(txns, clearing, closed=closed, card_postings=card_postings)
    nodes: dict[str, Node] = {}
    flows: list[tuple[TransactionRow, list[Flow]]] = []
    payers: dict[str, Decimal] = {}
    types: dict[str, Decimal] = {}
    withheld: dict[str, Decimal] = {}
    cats: dict[str, Decimal] = {}
    subs: dict[str, Decimal] = {}
    for txn in _windowed(txns, r):
        flows.append((txn, txn_flows(txn, clearing, cards, nodes)))
        for p in txn.postings:
            if p.inr_amount == 0:
                continue
            if is_withheld(p.account):
                _add(withheld, TAX_WITHHELD.id, p.inr_amount)
            elif p.account.startswith("Income:"):
                _add(payers, payer(p.account).id, -p.inr_amount)
                _add(types, top_category(p.account), -p.inr_amount)
            elif is_spending_account(p.account):
                _add(cats, top_category(p.account), p.inr_amount)
                sub = sub_account(p.account)
                if sub is not None:
                    _add(subs, sub.id, p.inr_amount)
    income = sum(types.values(), _ZERO) - sum(withheld.values(), _ZERO)
    expenses = sum(cats.values(), _ZERO)

    mapping: dict[str, str | None] = {}
    small = (
        {c for c, v in cats.items() if v < expenses * FOLD_SHARE}
        if expenses > 0
        else set()
    )
    for c in small:
        mapping[c] = OTHER.id
    for node in nodes.values():
        if node.role in _DETAIL_ROLES and depth == "summary":
            mapping[node.id] = None
        elif node.role == "sub" and top_category(node.id) in small:
            mapping[node.id] = None
    folded = sorted(((c, cats[c]) for c in small), key=lambda kv: (-kv[1], kv[0]))
    categories = {c: v for c, v in cats.items() if c not in small}
    if small:
        categories[OTHER.id] = sum((v for _, v in folded), _ZERO)

    g = Graph(
        nodes=nodes,
        mapping=mapping,
        links={},
        back=set(),
        flows=flows,
        payers=payers,
        types=types,
        withheld=withheld,
        categories=categories,
        subs=subs,
        folded=folded,
        income=income,
        expenses=expenses,
    )
    raw: dict[tuple[str, str], Decimal] = {}
    for _, fl in flows:
        for f in fl:
            a, b = g.drawn(f.source), g.drawn(f.target)
            if a is not None and b is not None and a != b:
                raw[(a, b)] = raw.get((a, b), _ZERO) + f.amount
    g.links = {k: v for k, v in raw.items() if v >= _EPSILON}
    g.back = _back_edges(g.links, g.node)
    return g


def _column(parts: dict[str, Decimal]) -> dict[str, str]:
    return allocate(parts, cents(sum(parts.values(), _ZERO)))


def _node_amounts(g: Graph, depth: Depth) -> dict[str, str]:
    """Drawn id -> amount string. Every node but an account takes its amount
    from its postings only (a node that nets to zero is "0.00", never its
    link size). Payers, types, withheld tax and categories are each allocated
    to the rounded sum of their own column, so each column's strings sum to
    its rounded total. An account's amount is max(in, out) over its links."""
    out: dict[str, str] = {}
    if depth == "detailed":
        out.update(_column(g.payers))
        out.update({k: q(v) for k, v in g.subs.items() if g.drawn(k) is not None})
    out.update(_column(g.types))
    out.update(_column(g.withheld))
    out.update(_column(g.categories))
    flow_in: dict[str, Decimal] = {}
    flow_out: dict[str, Decimal] = {}
    for (a, b), v in g.links.items():
        _add(flow_out, a, v)
        _add(flow_in, b, v)
    for nid in flow_in.keys() | flow_out.keys():
        if nid not in out and g.node(nid).role == "account":
            out[nid] = q(max(flow_in.get(nid, _ZERO), flow_out.get(nid, _ZERO)))
    return out


def _folded(g: Graph, amounts: dict[str, str]) -> list[dict]:
    if not g.folded:
        return []
    parts = allocate(dict(g.folded), Decimal(amounts[OTHER.id]))
    return [{"id": c, "label": _tail(c), "amount": parts[c]} for c, _ in g.folded]


def sankey(
    txns: list[TransactionRow],
    r: DateRange,
    *,
    depth: Depth = "detailed",
    clearing: str,
    closed: frozenset[str] = frozenset(),
    card_postings: list[PostingRow] | None = None,
    names: dict[str, str] | None = None,
) -> dict:
    """The `SankeyResponse` shape for `r`. `txns`: every transaction, those
    without a txn-id too (`ledger_view.all_flow_transactions`); `clearing`:
    `Settings.card_clearing_account`; `closed` / `card_postings` as
    `in_transit.match_clearing`; `names` (`ledger_view.commodity_names`)
    resolves a scheme code in a label."""
    g = build_graph(
        txns,
        r,
        depth=depth,
        clearing=clearing,
        closed=closed,
        card_postings=card_postings,
    )
    amounts = _node_amounts(g, depth)
    linked = {n for pair in g.links for n in pair}
    ids = [n for n in amounts if n in linked or Decimal(amounts[n]) != 0]
    ids.sort(key=lambda n: (COLUMN[g.node(n).role], -abs(Decimal(amounts[n])), n))
    rank = {n: i for i, n in enumerate(ids)}
    other = _folded(g, amounts)
    return {
        "start": r.start.isoformat(),
        "end": r.end.isoformat(),
        "depth": depth,
        "totals": {"income": q(g.income), "expenses": q(g.expenses)},
        "nodes": [
            {
                "id": n,
                "label": display_account(g.node(n).label, names or {}),
                "kind": g.node(n).kind,
                "role": g.node(n).role,
                "amount": amounts[n],
                "folded": other if n == OTHER.id else [],
            }
            for n in ids
        ],
        "links": [
            {"source": a, "target": b, "value": q(v), "back": (a, b) in g.back}
            for (a, b), v in sorted(
                g.links.items(),
                key=lambda kv: ((kv[0] in g.back), rank[kv[0][0]], rank[kv[0][1]]),
            )
        ],
    }


@dataclass(frozen=True)
class LinkFlow:
    """One transaction's share of one drawn link. `source` / `target` are the
    drawn nodes; `real_source` / `real_target` the nodes before folding (a
    member category where the drawn node is `Other`)."""

    txn: TransactionRow
    source: Node
    target: Node
    real_source: Node
    real_target: Node
    amount: Decimal


def link_flows(g: Graph) -> list[LinkFlow]:
    """Every drawn flow per transaction, summed per (transaction, link,
    pre-fold pair)."""
    out: list[LinkFlow] = []
    for txn, fl in g.flows:
        per: dict[tuple[str, str, str, str], Decimal] = {}
        for f in fl:
            a, b = g.drawn(f.source), g.drawn(f.target)
            if a is not None and b is not None and (a, b) in g.links:
                key = (a, b, f.source, f.target)
                per[key] = per.get(key, _ZERO) + f.amount
        out.extend(
            LinkFlow(txn, g.node(a), g.node(b), g.nodes[ra], g.nodes[rb], v)
            for (a, b, ra, rb), v in per.items()
        )
    return out


def contributions(
    txns: list[TransactionRow],
    r: DateRange,
    source: str,
    target: str,
    *,
    depth: Depth = "detailed",
    clearing: str,
    closed: frozenset[str] = frozenset(),
    card_postings: list[PostingRow] | None = None,
) -> list[TransactionRow]:
    """Transactions in `r` behind the drawn link `source` -> `target` (node
    ids as in `sankey` at `depth`, back links included; `Other` stands for
    its members). A pair that is not a drawn link is []."""
    g = build_graph(
        txns,
        r,
        depth=depth,
        clearing=clearing,
        closed=closed,
        card_postings=card_postings,
    )
    seen: dict[tuple[str, date], TransactionRow] = {}
    for lf in link_flows(g):
        if (lf.source.id, lf.target.id) == (source, target):
            seen.setdefault((lf.txn.txn_id, lf.txn.date), lf.txn)
    return list(seen.values())
