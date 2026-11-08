from backend.app.persistence.learned_rules import LearnedRules


def test_add_and_match(tmp_path):
    rules = LearnedRules(tmp_path / "learned.yaml")
    rules.add("SUNRISE TIFFINS", "Expenses:Food")
    assert rules.match("UPI SUNRISE TIFFINS 200") == "Expenses:Food"


def test_persists_across_instances(tmp_path):
    path = tmp_path / "learned.yaml"
    LearnedRules(path).add("RAZP*9XK2", "Expenses:Food")
    assert LearnedRules(path).match("UPI RAZP*9XK2QF") == "Expenses:Food"


def test_no_match_returns_none(tmp_path):
    assert LearnedRules(tmp_path / "learned.yaml").match("WHATEVER") is None


def test_last_rule_wins_on_conflict(tmp_path):
    rules = LearnedRules(tmp_path / "learned.yaml")
    rules.add("AMAZON", "Expenses:Shopping")
    rules.add("AMAZON", "Expenses:Work")
    assert rules.match("AMAZON PAY") == "Expenses:Work"


def test_re_adding_the_same_pattern_replaces_it_instead_of_duplicating(tmp_path):
    import yaml

    path = tmp_path / "learned.yaml"
    rules = LearnedRules(path)
    rules.add("AMAZON", "Expenses:Shopping")
    rules.add("amazon", "Expenses:Work")  # same pattern once upper-cased
    stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert stored == [{"pattern": "AMAZON", "category": "Expenses:Work"}]
    assert rules.match("AMAZON PAY") == "Expenses:Work"


def test_re_adding_a_pattern_promotes_it_over_other_matching_rules(tmp_path):
    # The three-rule misroute: dedup must not DEMOTE the user's newest
    # decision. `match()` iterates in order and later rules win, so a re-added
    # pattern left at its old index loses to any later rule that also matches
    # -- the user re-classifies AMAZON as Household and future AMAZON PAY
    # imports silently keep landing in Work, with nothing on screen.
    import yaml

    path = tmp_path / "learned.yaml"
    rules = LearnedRules(path)
    rules.add("AMAZON", "Expenses:Shopping")
    rules.add("AMAZON PAY", "Expenses:Work")
    rules.add("AMAZON", "Expenses:Household")  # newest explicit decision

    assert rules.match("UPI AMAZON PAY 123") == "Expenses:Household"
    assert rules.match("UPI AMAZON RETAIL") == "Expenses:Household"
    # ... and still no duplicate AMAZON row: removed, then re-appended last.
    stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert [r["pattern"] for r in stored] == ["AMAZON PAY", "AMAZON"]


def test_later_wins_is_unchanged_for_patterns_added_once_each(tmp_path):
    rules = LearnedRules(tmp_path / "learned.yaml")
    rules.add("AMAZON", "Expenses:Shopping")
    rules.add("AMAZON PAY", "Expenses:Work")
    assert rules.match("UPI AMAZON PAY 123") == "Expenses:Work"
    assert rules.match("UPI AMAZON RETAIL") == "Expenses:Shopping"


def test_a_hand_edited_rule_row_missing_its_pattern_key_does_not_crash_add(tmp_path):
    # The rules file is user-owned, so a row without a `pattern` key is
    # possible; `add`'s dedup scan must not be what raises on it.
    import yaml

    path = tmp_path / "learned.yaml"
    path.write_text("- category: Expenses:Food\n", encoding="utf-8")
    rules = LearnedRules(path)
    rules.add("SWIGGY", "Expenses:Food")
    stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert stored[-1] == {"pattern": "SWIGGY", "category": "Expenses:Food"}


def test_add_many_writes_the_file_once_for_the_whole_batch(tmp_path, monkeypatch):
    # The bulk backlog classify saves ~241 rules in one request. `add`
    # rewrites the WHOLE yaml, so a loop of `add` was 241 whole-file
    # rewrites, and a failure partway through them left the rules file
    # half-written after the journal had already been rewritten. Counting
    # writes is the only way this is observable: swap add_many's body back
    # for a loop of `add` and this reads 3.
    from pathlib import Path

    real_write = Path.write_text
    calls = {"n": 0}

    def counting_write(self, *args, **kwargs):
        calls["n"] += 1
        return real_write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", counting_write)

    LearnedRules(tmp_path / "learned.yaml").add_many(
        [
            ("SWIGGY", "Expenses:Food"),
            ("BLUEBIRD CAB", "Expenses:Travel"),
            ("LANDLORD", "Expenses:Rent"),
        ]
    )

    assert calls["n"] == 1


def test_add_many_keeps_remove_then_append_so_the_newest_pair_outranks(tmp_path):
    # Same promotion guarantee as `add`'s, within one batch: a pattern
    # re-answered later in the same paste must end up LAST, or `match`'s
    # later-wins iteration demotes the newest decision below an earlier rule
    # that also matches.
    import yaml

    path = tmp_path / "learned.yaml"
    rules = LearnedRules(path)
    rules.add_many(
        [
            ("AMAZON", "Expenses:Shopping"),
            ("AMAZON PAY", "Expenses:Work"),
            ("amazon", "Expenses:Household"),
        ]
    )

    assert rules.match("UPI AMAZON PAY 123") == "Expenses:Household"
    stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert [r["pattern"] for r in stored] == ["AMAZON PAY", "AMAZON"]


def test_add_still_persists_one_pair_after_delegating_to_add_many(tmp_path):
    path = tmp_path / "learned.yaml"
    LearnedRules(path).add("SWIGGY", "Expenses:Food")
    assert LearnedRules(path).match("UPI SWIGGY 99") == "Expenses:Food"


def test_exact_returns_the_category_of_the_rule_with_exactly_that_pattern(tmp_path):
    rules = LearnedRules(tmp_path / "learned.yaml")
    rules.add("FICTIVEBIKES", "Expenses:Travel:Bike")
    assert rules.exact("FICTIVEBIKES") == "Expenses:Travel:Bike"
    assert rules.exact("fictivebikes") == "Expenses:Travel:Bike"  # upper-cased first


def test_exact_ignores_a_rule_that_only_matches_as_a_substring(tmp_path):
    # Q12: the preview's `replaces` must name only the rule add_many will
    # replace. A substring rule matches the pattern but is not replaced.
    rules = LearnedRules(tmp_path / "learned.yaml")
    rules.add("BIKES", "Expenses:Travel:Bike")
    assert rules.match("FICTIVEBIKES") == "Expenses:Travel:Bike"
    assert rules.exact("FICTIVEBIKES") is None
    assert rules.exact("FICTIVEBIKES RENTAL") is None


def test_exact_takes_the_last_of_hand_edited_duplicates_and_skips_rows_without_a_pattern(
    tmp_path,
):
    path = tmp_path / "learned.yaml"
    path.write_text(
        "- pattern: FICTIVEBIKES\n  category: Expenses:Travel:Bike\n"
        "- category: Expenses:Food\n"
        "- pattern: FICTIVEBIKES\n  category: Expenses:Travel\n",
        encoding="utf-8",
    )
    assert LearnedRules(path).exact("FICTIVEBIKES") == "Expenses:Travel"


def test_exact_is_none_when_there_are_no_rules(tmp_path):
    assert LearnedRules(tmp_path / "learned.yaml").exact("FICTIVEBIKES") is None
