from __future__ import annotations

import threading
from datetime import date

import pytest

from backend.app.core.errors.errors import CodedError
from backend.app.persistence.journal import Ledger
from backend.app.persistence.templates import PdfTemplateStore
from backend.app.service.ledger.account_meta import (
    AccountMetaService,
    CardPropertiesUpdate,
    suggest_card_properties,
)
from backend.app.service.reports.health.ledger_view import (
    card_statement_dates,
    open_meta,
)
from tests.service.ingest.pdf_reference import HDFC_PDF_DRAFT

CARD = "Liabilities:CreditCard:Acme1234"
OTHER_CARD = "Liabilities:CreditCard:Zeta"
CHECKING = "Assets:Checking:Bank"


def _ledger(tmp_path) -> Ledger:
    led = Ledger(tmp_path)
    led.bootstrap(seed_accounts=(CHECKING,))
    return led


def _svc(tmp_path, *cards: str) -> AccountMetaService:
    led = _ledger(tmp_path)
    led.open_named_accounts(list(cards or (CARD,)))
    return AccountMetaService(led, PdfTemplateStore(tmp_path / "templates" / "pdf"))


def _append_main(led: Ledger, text: str) -> None:
    main = led.main_file.read_text(encoding="utf-8")
    led.main_file.write_text(main.rstrip() + "\n" + text, encoding="utf-8")


def _record(account: str, period_end: str, due: str | None = None) -> str:
    text = f'{period_end} custom "card-statement" {account}\n'
    if due is not None:
        text += f"  due-date: {due}\n"
    return text


def _save_template(svc: AccountMetaService, account: str, last4: str) -> None:
    draft = HDFC_PDF_DRAFT.model_copy(update={"default_account": account})
    svc._pdf_templates.save(draft.to_template(card_last4=last4))


def _by_account(rows: list[dict]) -> dict[str, dict]:
    return {row["account"]: row for row in rows}


def test_card_properties_lists_open_unclosed_cards_only(tmp_path):
    svc = _svc(tmp_path, CARD, OTHER_CARD)
    svc._ledger.close_account(OTHER_CARD, date(2026, 10, 1))

    rows = svc.card_properties()

    assert [row["account"] for row in rows] == [CARD]
    assert rows[0] == {
        "account": CARD,
        "statement_day": None,
        "due_day": None,
        "last4": None,
        "expiry": None,
        "suggested": {
            "statement_day": None,
            "due_day": None,
            "last4": "1234",
            "statement_count": 0,
            "last4_source": "account_name",
        },
    }


def test_card_properties_sorted_by_account(tmp_path):
    svc = _svc(tmp_path, OTHER_CARD, CARD)
    assert [row["account"] for row in svc.card_properties()] == [CARD, OTHER_CARD]


def test_card_properties_returns_stored_values(tmp_path):
    svc = _svc(tmp_path)
    svc._ledger.set_open_meta(
        CARD, {"statement-day": 20, "due-day": 9, "last4": "0042"}
    )

    row = _by_account(svc.card_properties())[CARD]

    assert (row["statement_day"], row["due_day"], row["last4"]) == (20, 9, "0042")
    # A stored value is returned as stored even when the suggestion differs.
    assert row["suggested"]["last4"] == "1234"


def test_card_statement_dates_reads_records_in_date_order(tmp_path):
    led = _ledger(tmp_path)
    led.open_named_accounts([CARD])
    _append_main(
        led,
        _record(CARD, "2026-10-15", "2026-11-03") + _record(CARD, "2026-09-15"),
    )

    dates = card_statement_dates(led.load().entries)

    assert dates == {
        CARD: [
            (date(2026, 9, 15), None),
            (date(2026, 10, 15), date(2026, 11, 3)),
        ]
    }


def test_suggest_statement_and_due_day_from_records(tmp_path):
    svc = _svc(tmp_path)
    _append_main(
        svc._ledger,
        _record(CARD, "2026-09-15", "2026-10-03")
        + _record(CARD, "2026-10-15", "2026-11-03"),
    )

    suggested = svc.card_property(CARD)["suggested"]

    assert suggested["statement_day"] == 15
    assert suggested["due_day"] == 3
    assert suggested["statement_count"] == 2


def test_suggest_tie_prefers_latest_record():
    statements = [
        (date(2026, 8, 16), None),
        (date(2026, 9, 14), date(2026, 10, 2)),
        (date(2026, 10, 16), date(2026, 11, 4)),
        (date(2026, 11, 14), None),
    ]
    out = suggest_card_properties("Liabilities:CreditCard:Acme", statements, set())
    # 14 and 16 tie (2 each); the latest record is on the 14th.
    assert out["statement_day"] == 14
    # Due days 2 and 4 tie; the latest record carrying one is due on the 4th.
    assert out["due_day"] == 4
    assert out["statement_count"] == 4

    out = suggest_card_properties(
        "Liabilities:CreditCard:Acme",
        [(date(2026, 9, 14), None), (date(2026, 10, 16), None)],
        set(),
    )
    assert out["statement_day"] == 16
    assert out["due_day"] is None


def test_suggest_most_frequent_day_beats_recency():
    statements = [
        (date(2026, 7, 15), None),
        (date(2026, 8, 15), None),
        (date(2026, 9, 14), None),
    ]
    out = suggest_card_properties("Liabilities:CreditCard:Acme", statements, set())
    assert out["statement_day"] == 15


def test_suggest_last4_from_single_pdf_template(tmp_path):
    svc = _svc(tmp_path)
    _save_template(svc, CARD, "5678")
    _save_template(svc, OTHER_CARD, "9999")  # another card's template is ignored

    suggested = svc.card_property(CARD)["suggested"]
    assert (suggested["last4"], suggested["last4_source"]) == ("5678", "pdf_template")

    _save_template(svc, CARD, "4321")  # two templates now disagree
    suggested = svc.card_property(CARD)["suggested"]
    assert (suggested["last4"], suggested["last4_source"]) == ("1234", "account_name")


@pytest.mark.parametrize(
    ("account", "expected"),
    [
        ("Liabilities:CreditCard:Acme1234", ("1234", "account_name")),
        ("Liabilities:CreditCard:Acme-5678", ("5678", "account_name")),
        ("Liabilities:CreditCard:Acme:4321", ("4321", "account_name")),
        ("Liabilities:CreditCard:Acme", (None, None)),
        ("Liabilities:CreditCard:Acme12345", (None, None)),
        ("Liabilities:CreditCard:Acme123", (None, None)),
        # Only the digits that end the leaf count, not an earlier run.
        ("Liabilities:CreditCard:Acme1234x5678", ("5678", "account_name")),
        ("Liabilities:CreditCard:Acme1234x", (None, None)),
    ],
)
def test_suggest_last4_from_account_leaf_digits(account, expected):
    out = suggest_card_properties(account, [], set())
    assert (out["last4"], out["last4_source"]) == expected


def test_set_card_properties_writes_open_meta(tmp_path):
    svc = _svc(tmp_path)

    row = svc.set_card_properties(CARD, 15, 3, "1234")

    assert open_meta(svc._ledger)[CARD] == {
        "statement-day": 15,
        "due-day": 3,
        "last4": "1234",
    }
    assert (row["statement_day"], row["due_day"], row["last4"]) == (15, 3, "1234")


def test_set_card_properties_snapshots_once(tmp_path):
    svc = _svc(tmp_path)
    snaps = svc._ledger.snapshots_dir

    def count() -> int:
        return len(list(snaps.iterdir())) if snaps.exists() else 0

    before = count()
    svc.set_card_properties(CARD, 15, 3, "1234")
    assert count() == before + 1
    svc.set_card_properties(CARD, 15, 3, "1234")  # unchanged: no write
    assert count() == before + 1


def test_set_card_properties_null_removes_key(tmp_path):
    svc = _svc(tmp_path)
    svc.set_card_properties(CARD, 15, 3, "1234")

    row = svc.set_card_properties(CARD, 15, None, None)

    assert open_meta(svc._ledger)[CARD] == {"statement-day": 15}
    assert (row["due_day"], row["last4"]) == (None, None)


@pytest.mark.parametrize(
    ("statement_day", "due_day", "last4", "field", "value"),
    [
        (32, 3, "1234", "statement_day", "32"),
        (0, 3, "1234", "statement_day", "0"),
        (15, 32, "1234", "due_day", "32"),
        (15, -1, "1234", "due_day", "-1"),
        (15, 3, "12a4", "last4", "12a4"),
        (15, 3, "12345", "last4", "12345"),
        (15, 3, "", "last4", ""),
        # The first bad field is named.
        (40, 50, "x", "statement_day", "40"),
    ],
)
def test_set_card_properties_rejects_out_of_range(
    tmp_path, statement_day, due_day, last4, field, value
):
    svc = _svc(tmp_path)
    before = svc._ledger.main_file.read_text(encoding="utf-8")

    with pytest.raises(CodedError) as err:
        svc.set_card_properties(CARD, statement_day, due_day, last4)

    assert err.value.code == "E1082"
    assert err.value.data is not None
    assert err.value.data["field"] == field
    assert err.value.data["value"] == value
    assert svc._ledger.main_file.read_text(encoding="utf-8") == before


def test_set_card_properties_rejects_non_card(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(CodedError) as err:
        svc.set_card_properties(CHECKING, 1, 2, None)
    assert err.value.code == "E1083"
    assert err.value.data == {
        "account": CHECKING,
        "expected": "Liabilities:CreditCard:*",
    }


def test_set_card_properties_rejects_closed(tmp_path):
    svc = _svc(tmp_path)
    svc._ledger.close_account(CARD, date(2026, 10, 1))
    before = svc._ledger.main_file.read_text(encoding="utf-8")
    # Closed is checked before the values: a bad value still answers E1084.
    with pytest.raises(CodedError) as err:
        svc.set_card_properties(CARD, 99, 2, None)
    assert err.value.code == "E1084"
    assert err.value.data == {"account": CARD}
    assert svc._ledger.main_file.read_text(encoding="utf-8") == before


def test_set_card_properties_unknown_account_raises_lookup(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(LookupError):
        svc.set_card_properties("Liabilities:CreditCard:Nope", 1, 2, None)
    with pytest.raises(LookupError):
        svc.card_property("Liabilities:CreditCard:Nope")


def test_cycle_properties_fall_back_to_suggestions_per_field(tmp_path):
    svc = _svc(tmp_path, CARD, OTHER_CARD)
    _append_main(
        svc._ledger,
        _record(CARD, "2026-09-15", "2026-10-03")
        + _record(CARD, "2026-10-15", "2026-11-03"),
    )
    svc.set_card_properties(CARD, None, 9, None)

    props = svc.cycle_properties()

    # statement_day suggested, due_day stored, last4 from the account name.
    assert props[CARD] == {
        "statement_day": 15,
        "due_day": 9,
        "last4": "1234",
        "expiry": None,
        "source": "suggested",
    }
    assert props[OTHER_CARD] == {
        "statement_day": None,
        "due_day": None,
        "last4": None,
        "expiry": None,
        "source": None,
    }

    svc.set_card_properties(CARD, 20, 9, None)
    assert svc.cycle_properties()[CARD]["source"] == "account"
    assert svc.cycle_properties()[CARD]["statement_day"] == 20


# --- expiry ------------------------------------------------------------------


def test_set_card_properties_stores_expiry_as_a_quoted_month(tmp_path):
    svc = _svc(tmp_path)

    row = svc.set_card_properties(CARD, 15, 3, "1234", expiry="2029-08")

    assert open_meta(svc._ledger)[CARD]["expiry"] == "2029-08"
    assert '  expiry: "2029-08"\n' in svc._ledger.main_file.read_text(encoding="utf-8")
    assert row["expiry"] == "2029-08"
    assert svc.cycle_properties()[CARD]["expiry"] == "2029-08"


def test_set_card_properties_without_expiry_leaves_it_unchanged(tmp_path):
    svc = _svc(tmp_path)
    svc.set_card_properties(CARD, 15, 3, "1234", expiry="2029-08")

    row = svc.set_card_properties(CARD, 16, 3, "1234")

    assert row["expiry"] == "2029-08"
    assert svc.set_card_properties(CARD, 16, 3, "1234", expiry=None)["expiry"] is None
    assert "expiry" not in open_meta(svc._ledger)[CARD]


def test_a_past_expiry_is_allowed(tmp_path):
    svc = _svc(tmp_path)
    assert (
        svc.set_card_properties(CARD, None, None, None, expiry="2001-01")["expiry"]
        == "2001-01"
    )


@pytest.mark.parametrize(
    "expiry", ["2029-13", "2029-00", "abc", "08/29", "2029-8", "29-08", ""]
)
def test_set_card_properties_rejects_a_bad_expiry(tmp_path, expiry):
    svc = _svc(tmp_path)
    before = svc._ledger.main_file.read_text(encoding="utf-8")

    with pytest.raises(CodedError) as err:
        svc.set_card_properties(CARD, 15, 3, "1234", expiry=expiry)

    assert err.value.code == "E1082"
    assert err.value.data is not None
    assert (err.value.data["field"], err.value.data["value"]) == ("expiry", expiry)
    assert svc._ledger.main_file.read_text(encoding="utf-8") == before


def test_expiry_is_checked_after_the_other_fields(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(CodedError) as err:
        svc.set_card_properties(CARD, 15, 3, "12a4", expiry="abc")
    assert err.value.data is not None
    assert err.value.data["field"] == "last4"


def test_a_hand_written_malformed_expiry_reads_as_null(tmp_path):
    svc = _svc(tmp_path)
    svc._ledger.set_open_meta(CARD, {"expiry": "Aug 2029"})
    assert _by_account(svc.card_properties())[CARD]["expiry"] is None


# --- set_card_properties_many: one write for the whole panel ------------------

THIRD_CARD = "Liabilities:CreditCard:Nova"


def _snaps(svc: AccountMetaService) -> int:
    snaps = svc._ledger.snapshots_dir
    return len(list(snaps.iterdir())) if snaps.exists() else 0


def _upd(account, statement_day=None, due_day=None, last4=None, expiry=None):
    return CardPropertiesUpdate(account, statement_day, due_day, last4, expiry)


def test_batch_saves_three_cards_with_one_snapshot(tmp_path):
    svc = _svc(tmp_path, CARD, OTHER_CARD, THIRD_CARD)
    before = _snaps(svc)

    rows = svc.set_card_properties_many(
        [
            _upd(CARD, 15, 3, "1234", "2029-08"),
            _upd(OTHER_CARD, 20, 8),
            _upd(THIRD_CARD, None, None, "9999"),
        ]
    )

    assert _snaps(svc) == before + 1
    meta = open_meta(svc._ledger)
    assert meta[CARD] == {
        "statement-day": 15,
        "due-day": 3,
        "last4": "1234",
        "expiry": "2029-08",
    }
    assert meta[OTHER_CARD] == {"statement-day": 20, "due-day": 8}
    assert meta[THIRD_CARD] == {"last4": "9999"}
    # The whole list after the write, the GET's shape.
    assert rows == svc.card_properties()
    assert [r["account"] for r in rows] == [CARD, THIRD_CARD, OTHER_CARD]


def test_batch_with_one_invalid_card_writes_nothing(tmp_path):
    svc = _svc(tmp_path, CARD, OTHER_CARD)
    before, text = _snaps(svc), svc._ledger.main_file.read_text(encoding="utf-8")

    with pytest.raises(CodedError) as err:
        svc.set_card_properties_many(
            [_upd(CARD, 15, 3, "1234"), _upd(OTHER_CARD, 20, 32)]
        )

    assert err.value.code == "E1082"
    assert err.value.data == {
        "account": OTHER_CARD,
        "field": "due_day",
        "value": "32",
        "reason": "must be a whole number from 1 to 31",
    }
    assert _snaps(svc) == before
    assert svc._ledger.main_file.read_text(encoding="utf-8") == text


def test_batch_names_the_card_with_a_bad_expiry(tmp_path):
    svc = _svc(tmp_path, CARD, OTHER_CARD)
    with pytest.raises(CodedError) as err:
        svc.set_card_properties_many(
            [_upd(CARD, expiry="2029-08"), _upd(OTHER_CARD, expiry="2029-13")]
        )
    assert err.value.data is not None
    assert (err.value.data["account"], err.value.data["field"]) == (
        OTHER_CARD,
        "expiry",
    )
    assert "expiry" not in open_meta(svc._ledger)[CARD]


def test_batch_all_unchanged_takes_no_snapshot(tmp_path):
    svc = _svc(tmp_path, CARD, OTHER_CARD)
    svc.set_card_properties_many([_upd(CARD, 15, 3), _upd(OTHER_CARD, 20)])
    before = _snaps(svc)
    text = svc._ledger.main_file.read_bytes()

    rows = svc.set_card_properties_many([_upd(CARD, 15, 3), _upd(OTHER_CARD, 20)])

    assert _snaps(svc) == before
    assert svc._ledger.main_file.read_bytes() == text
    assert rows == svc.card_properties()


def test_batch_does_not_rewrite_an_unchanged_card(tmp_path):
    svc = _svc(tmp_path, OTHER_CARD)
    # Hand-written odd spacing: re-rendering would normalise it.
    _append_main(
        svc._ledger,
        f'2000-01-01 open {CARD} INR\n  statement-day:   15\n  last4:  "1234"\n',
    )

    svc.set_card_properties_many([_upd(CARD, 15, None, "1234"), _upd(OTHER_CARD, 20)])

    text = svc._ledger.main_file.read_text(encoding="utf-8")
    assert '  statement-day:   15\n  last4:  "1234"\n' in text
    assert open_meta(svc._ledger)[OTHER_CARD] == {"statement-day": 20}


@pytest.mark.parametrize(
    ("make", "code", "data"),
    [
        # Each card is checked in full, in request order; the first error wins.
        (
            lambda: [_upd(CARD, 99), _upd("Liabilities:CreditCard:Nope")],
            "E1082",
            {
                "account": CARD,
                "field": "statement_day",
                "value": "99",
                "reason": "must be a whole number from 1 to 31",
            },
        ),
        (
            lambda: [_upd(CARD, 1), _upd(CHECKING, 1)],
            "E1083",
            {"account": CHECKING, "expected": "Liabilities:CreditCard:*"},
        ),
    ],
)
def test_batch_errors_name_the_failing_card(tmp_path, make, code, data):
    svc = _svc(tmp_path, CARD)
    with pytest.raises(CodedError) as err:
        svc.set_card_properties_many(make())
    assert (err.value.code, err.value.data) == (code, data)
    assert open_meta(svc._ledger)[CARD] == {}


def test_batch_closed_card_answers_e1084(tmp_path):
    svc = _svc(tmp_path, CARD, OTHER_CARD)
    svc._ledger.close_account(OTHER_CARD, date(2026, 10, 1))
    with pytest.raises(CodedError) as err:
        svc.set_card_properties_many([_upd(CARD, 1), _upd(OTHER_CARD, 99)])
    assert (err.value.code, err.value.data) == ("E1084", {"account": OTHER_CARD})


def test_batch_unknown_account_raises_lookup_naming_it(tmp_path):
    svc = _svc(tmp_path, CARD)
    with pytest.raises(LookupError) as err:
        svc.set_card_properties_many(
            [_upd(CARD, 1), _upd("Liabilities:CreditCard:Nope", 1)]
        )
    assert err.value.args == ("Liabilities:CreditCard:Nope",)
    assert open_meta(svc._ledger)[CARD] == {}


def test_batch_rejects_a_card_listed_twice(tmp_path):
    svc = _svc(tmp_path, CARD)
    with pytest.raises(CodedError) as err:
        svc.set_card_properties_many([_upd(CARD, 1), _upd(CARD, 2)])
    assert err.value.code == "E1082"
    assert err.value.data is not None
    assert (err.value.data["account"], err.value.data["field"]) == (CARD, "account")
    assert open_meta(svc._ledger)[CARD] == {}


def test_batch_holds_the_write_lock_from_its_checks_through_the_write(
    tmp_path, monkeypatch
):
    svc = _svc(tmp_path, CARD, OTHER_CARD)
    led = svc._ledger
    seen: list[bool] = []
    real_load = Ledger.load

    def load(self, *args, **kwargs):
        # Another thread tries the lock while this batch is checking/writing.
        got: list[bool] = []

        def probe() -> None:
            lock = led.write_lock()
            ok = lock.acquire(blocking=False)
            if ok:
                lock.release()
            got.append(ok)

        t = threading.Thread(target=probe)
        t.start()
        t.join()
        seen.append(got[0])
        return real_load(self, *args, **kwargs)

    monkeypatch.setattr(Ledger, "load", load)
    svc.set_card_properties_many([_upd(CARD, 1), _upd(OTHER_CARD, 2)])
    monkeypatch.undo()

    # Every load inside the batch (its checks, then the write's fresh parse)
    # found the lock held: no other write could slip in between.
    assert seen and not any(seen)
