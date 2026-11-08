"""Display-only metadata per error code.

Each entry holds ONLY user-facing strings (str.format templates) and the
refresh hint. HTTP status is bound separately in errors.py at
ErrorModel.initialize time, so wording/language can change here without
touching services or status mappings.
"""

from __future__ import annotations

E0000 = {
    "description": "Something went wrong. The action could not be completed.",
    "resolution": "Try again. If it keeps happening, check the server logs.",
    "resolvedByRefresh": False,
}

E1001 = {
    "description": "The uploaded file is missing a filename.",
    "resolution": "Re-select the file and upload it again.",
    "resolvedByRefresh": False,
}

E1002 = {
    "description": "Could not auto-detect a header row in {filename}.",
    "resolution": "Check that the file is a statement export with a header row.",
    "resolvedByRefresh": False,
}

E1003 = {
    "description": "Could not auto-detect date/narration columns in {filename}.",
    "resolution": "Map the columns manually when creating the template.",
    "resolvedByRefresh": False,
}

E1004 = {
    "description": "Upload {filename} was not found.",
    "resolution": "Re-run detect on the file before creating a template.",
    "resolvedByRefresh": False,
}

E1005 = {
    "description": "Unknown template.",
    "resolution": "Detect and create the template before resolving markers.",
    "resolvedByRefresh": False,
}

E1006 = {
    "description": "Unknown import batch.",
    "resolution": "Re-run detect to start a fresh review batch.",
    "resolvedByRefresh": True,
}

E1007 = {
    "description": "Unknown candidate in this batch.",
    "resolution": "Reload the review screen and try again.",
    "resolvedByRefresh": True,
}

E1008 = {
    "description": "Splits must sum to the transaction amount.",
    "resolution": "Adjust the split amounts so they total the transaction.",
    "resolvedByRefresh": False,
}

E1009 = {
    "description": "Each split needs an account and amount.",
    "resolution": "Fill in an account and amount for every split.",
    "resolvedByRefresh": False,
}

E1010 = {
    "description": "Resolve account for candidates: {candidate_ids}",
    "resolution": "Assign an account to each flagged candidate, then commit.",
    "resolvedByRefresh": False,
}

E1011 = {
    "description": "The mutual-fund scheme master file was not found or could not be read. Looked for: {path}.",
    "resolution": "Fetch prices so the AMFI NAVAll dump lands at prices/navall.txt, or place an AMFI scheme-master CSV at the configured path, then retry the import.",
    "resolvedByRefresh": False,
}

E1012 = {
    "description": "Resolve the mutual-fund scheme for candidates: {candidate_ids}",
    "resolution": "Pick the matching scheme for each flagged row, then commit.",
    "resolvedByRefresh": False,
}

E1013 = {
    "description": (
        "These redemptions sell more units than the known lots can cover: "
        "{candidate_ids}"
    ),
    "resolution": (
        "Import the earlier purchases for these schemes first, or correct the "
        "redemption units, then commit."
    ),
    "resolvedByRefresh": False,
}

E1014 = {
    "description": "The NAV history store could not be read: {path}.",
    "resolution": "The file may be corrupt. Restore it from a snapshot or delete it to rebuild on the next refresh.",
    "resolvedByRefresh": False,
}

E1015 = {
    "description": "The AMFI price feed returned no schemes while funds are held.",
    "resolution": "This looks like a bad feed response. The cached NAVs were kept; try refreshing again later.",
    "resolvedByRefresh": True,
}

E1016 = {
    "description": "Investment valuation is inconsistent: held symbol {symbol} reports negative units.",
    "resolution": "Re-check the imported buy/redeem rows for this fund; a redemption likely exceeds the known lots.",
    "resolvedByRefresh": False,
}

E1017 = {
    "description": "{name} is not a valid account name.",
    "resolution": "Use Type:Component:... where Type is one of Assets, Liabilities, Equity, Income, Expenses and each component starts with an uppercase letter or digit (letters, digits, hyphens only; no spaces).",
    "resolvedByRefresh": False,
}

E1018 = {
    "description": "An account named {new_name} is already open.",
    "resolution": "Pick a name that is not already in use, or rename the existing account first.",
    "resolvedByRefresh": False,
}

E1019 = {
    "description": "{account} cannot be closed: it still holds {residual}.",
    "resolution": "Move the balance to zero (transfer it out or settle it) before closing the account.",
    "resolvedByRefresh": False,
}

E1020 = {
    "description": "The mapping is missing entries for: {missing}.",
    "resolution": "Map every account and commodity the file references before committing.",
    "resolvedByRefresh": False,
}

E1021 = {
    "description": "These mapping targets are not legal beancount names: {offenders}.",
    "resolution": "Use a valid Type:Component account or an UPPERCASE commodity symbol.",
    "resolvedByRefresh": False,
}

E1022 = {
    "description": "These sells have no matching lot to reduce: {txn_ids}.",
    "resolution": "Import the covering purchases first, or exclude these rows, then commit.",
    "resolvedByRefresh": False,
}

E1023 = {
    "description": "Some transactions do not balance (lines: {lines}).",
    "resolution": "Add the missing leg in your ledger file, or enable MF gains/charges booking.",
    "resolvedByRefresh": False,
}

E1024 = {
    "description": "Investment charge residual exceeds tolerance or buy-gap is negative ({reason}; gap={gap} INR). Charges NOT booked — review the trade or adjust charge_tolerance.",
    "resolution": "Check stamp_duty_rate / stt_rate_sell / charge_tolerance in merge_booking.yaml against this trade. A negative gap means cash < cost in the source data.",
    "resolvedByRefresh": False,
}

E1025 = {
    "description": "Posting account {account!r} is not a legal beancount account name.",
    "resolution": "Use Type:Component format, e.g. Expenses:Food or Assets:Checking:HDFC.",
    "resolvedByRefresh": False,
}

E1030 = {
    "description": "mfapi.in is unavailable or returned an HTTP error ({reason}).",
    "resolution": "Check network connectivity. Backfill will skip this fund and continue.",
    "resolvedByRefresh": True,
}

E1031 = {
    "description": "mfapi.in returned an unparseable response for scheme {code}.",
    "resolution": "The fund code may be invalid or the API format changed. Skipping this fund.",
    "resolvedByRefresh": True,
}

E1032 = {
    "description": "No transaction found with id {txn_id}.",
    "resolution": "Reload the transaction list; the transaction ID may be stale or incorrect.",
    "resolvedByRefresh": False,
}

E1033 = {
    "description": "expected_hash is required to annotate a posting.",
    "resolution": "Include the year file hash returned by the last read before submitting the annotation.",
    "resolvedByRefresh": False,
}

E1034 = {
    "description": "Transaction {txn_id} has no posting for account {account}.",
    "resolution": "Reload the transaction and confirm the account name matches an existing posting.",
    "resolvedByRefresh": False,
}

E1035 = {
    "description": "The year file changed since it was loaded (expected {expected}, got {actual}).",
    "resolution": "Reload the year file and retry your edit.",
    "resolvedByRefresh": True,
}

E1036 = {
    "description": "force_kind must be one of {valid_kinds}; got {got}.",
    "resolution": 'Pass force_kind as "ledger" or "statement", or omit it to auto-detect.',
    "resolvedByRefresh": False,
}

E1037 = {
    "description": "The edit could not be applied: {detail}",
    "resolution": "Check the values you're submitting for this candidate and try again.",
    "resolvedByRefresh": False,
}

E1038 = {
    "description": "The categorization import could not be applied: {detail}",
    "resolution": "Check the exported file format and try again.",
    "resolvedByRefresh": False,
}

E1039 = {
    "description": "Account {account} is not open.",
    "resolution": "Check the account name; only open accounts can be renamed or closed.",
    "resolvedByRefresh": False,
}

E1040 = {
    "description": "The template body fingerprint ({body_fingerprint}) does not match the path fingerprint ({path_fingerprint}).",
    "resolution": "Ensure the fingerprint field in the request body matches the URL.",
    "resolvedByRefresh": False,
}

E1041 = {
    "description": "fys must be a comma-separated list of integer financial years; got {fys}.",
    "resolution": 'Pass fys as e.g. "2024,2025" — comma-separated integer years.',
    "resolvedByRefresh": False,
}

E1042 = {
    "description": "The NSE bhavcopy returned no quotes while equities are held.",
    "resolution": "This looks like a bad feed response. The cached prices were kept; try refreshing again later.",
    "resolvedByRefresh": True,
}

E1043 = {
    "description": (
        "These stock orders' per-lot postings do not reconcile to the order "
        "value within {tolerance} INR: {candidate_ids}"
    ),
    "resolution": (
        "Nothing was committed. On the import path a stock order's lot "
        "postings are priced from the order's own value, so they cannot "
        "normally disagree with it -- this check is defence-in-depth against a "
        "regression in how lots are built. Report it rather than editing the "
        "rows."
    ),
    "resolvedByRefresh": False,
}

E1044 = {
    "description": "These investment rows cannot be booked as lots: {details}",
    "resolution": (
        "Nothing was committed. Correct the flagged rows in review — a lot row "
        "needs a recognised investment source account, a symbol beancount "
        "accepts as a commodity name (letters, digits and ' . _ - only), and "
        "-- for a stock order -- the broker's Exchange Order Id."
    ),
    "resolvedByRefresh": False,
}

E1045 = {
    "description": (
        "These candidates' broker order-id already exists in the journal "
        "attached to a transaction with different content — a restated "
        "order, not a re-import: {candidate_ids}"
    ),
    "resolution": (
        "Nothing was committed. A previously-imported order's quantity, "
        "value, side, date or symbol appears to have changed in the broker "
        "export. Confirm which content is correct — the journal's or this "
        "row's — before importing; silently overwriting or silently "
        "keeping the stale entry are both refused."
    ),
    "resolvedByRefresh": False,
}

E1046 = {
    "description": (
        "The journal already holds {count} stock transaction(s) with no "
        "broker order-id, so an import cannot tell which of them this file "
        "has already recorded: {examples}"
    ),
    "resolution": (
        "Nothing was committed. Stock imports dedup on the broker's Exchange "
        "Order Id, and hand-entered stock transactions carry none — importing "
        "over them would book a second copy of history that already exists. "
        "Run scripts/backfill_stock_order_ids.py (dry run first, then "
        "--apply) to stamp the existing transactions with their order-ids, "
        "then import again."
    ),
    "resolvedByRefresh": False,
}

E1047 = {
    "description": (
        "Transaction {txn_id} could not be located in {year_file}: the text "
        "at the line the journal was read from is not that transaction, or "
        "the rewritten text would not read back as the intended transaction."
    ),
    "resolution": (
        "Nothing was changed — the journal was restored. This normally means "
        "the journal file changed while the request ran (an edit in another "
        "window or editor). Reload and try again; if it keeps failing, open "
        "Ledger → Raw edit and check that transaction's entry."
    ),
    "resolvedByRefresh": False,
}

E1048 = {
    "description": "{account} is not an expense account.",
    "resolution": (
        "Classifying a merchant only moves spend between expense categories "
        "— the amounts are never touched, so a target outside Expenses: "
        "would silently turn spend into an asset, a liability or income. "
        "Pick or type an Expenses:... account."
    ),
    "resolvedByRefresh": False,
}

E1049 = {
    "description": "{account} is the backlog itself, not a category.",
    "resolution": (
        "Pick the category this merchant really belongs to. Classifying into "
        "Expenses:Uncategorized would move nothing and would pin the merchant "
        "to the backlog for every future import."
    ),
    "resolvedByRefresh": False,
}

E1050 = {
    "description": "The prompt file {path} is missing.",
    "resolution": (
        "Copy-paste categorization reads a user-owned prompt from the data "
        "directory's prompts/ folder; this one is not there (deleted or "
        "renamed by hand). Restore the file — the shipped default is in "
        "the app's llm/prompts/ folder under the same name — or "
        "delete the whole prompts/ folder and restart the app, which re-seeds "
        "the defaults. Nothing was read or written."
    ),
    "resolvedByRefresh": False,
}

E1051 = {
    "description": (
        "`{key}` under `income` in {path} is not a readable number (found `{value}`)."
    ),
    "resolution": (
        "Declared tax inputs are hand-edited, so this is almost always a "
        "typing slip. Write the value as plain digits in quotes with no digit "
        'grouping, no currency symbol and no blank -- `{key}: "1200000"`, '
        'never `{key}: "12,00,000"` and never a bare `{key}:` with nothing '
        "after it. Fix the `{key}` line in {path} and reload the tax "
        "workspace; nothing was read or written."
    ),
    "resolvedByRefresh": False,
}

E1052 = {
    "description": (
        "This import template cannot say which rows are credits: the columns "
        "that carry direction are either not set, name both money legs at "
        "once, or are absent from this file's header row."
    ),
    "resolution": (
        "Pick the column holding the Dr/Cr (or Debit/Credit) marker, or switch "
        "the template to separate debit and credit amount columns. Without it "
        "every row would be imported as money going out, including payments "
        "and refunds."
    ),
    "resolvedByRefresh": False,
}

E1053 = {
    "description": "This import template has no date format.",
    "resolution": (
        "Set the date format to the pattern this file's date column actually "
        "uses -- for example %d/%m/%Y for 05/04/2026. A blank format matches "
        "no date at all, so every row would be skipped and the import would "
        "come back empty with nothing on screen to say why."
    ),
    "resolvedByRefresh": False,
}

E1054 = {
    "description": "The tax rules file {path} cannot be read as rules ({problem}).",
    "resolution": (
        "This file is hand-edited reference data, so the usual cause is a "
        "deleted or misspelt key, a section that is a single value instead of "
        "a block, a value that is not a plain number, or broken YAML "
        "indentation. Correct {path} against a working rules file and reload "
        "the tax workspace; nothing was read or written."
    ),
    "resolvedByRefresh": False,
}

E1055 = {
    "description": (
        "`{key}` under `deductions` in {path} is not a readable number "
        "(found `{value}`)."
    ),
    "resolution": (
        "Declared tax inputs are hand-edited, so this is almost always a "
        "typing slip. Write the amount as plain digits in quotes with no digit "
        'grouping, no currency symbol and no blank -- `{key}: "150000"`, '
        'never `{key}: "1,50,000"` and never a bare `{key}:` with nothing '
        "after it. Fix the `{key}` line in {path} and reload the tax "
        "workspace; nothing was read or written."
    ),
    "resolvedByRefresh": False,
}

E1056 = {
    "description": (
        "`{key}` in {path} is not an amount this form can save (found `{value}`)."
    ),
    "resolution": (
        "{hint}Amounts must be plain digits, zero or more, at most two "
        "decimal places and under a lakh crore -- no digit grouping, no "
        "currency symbol, no sign, no exponent and no blank. Correct the "
        "value and save again; nothing was written."
    ),
    "resolvedByRefresh": False,
}

E1057 = {
    "description": "`{key}` is not a key {where} of {path} recognises.",
    "resolution": (
        "A key nothing reads would record a declaration that quietly has no "
        "effect on your tax estimate, so it is refused instead of written. "
        "Use one of: {allowed}. Nothing was written."
    ),
    "resolvedByRefresh": False,
}

E1058 = {
    "description": (
        "{path} changed since you loaded it (you sent revision {expected}, "
        "the file is now {current})."
    ),
    "resolution": (
        "The file was edited elsewhere -- another tab, an editor, or a hand "
        "edit -- after this form read it, so saving would overwrite that "
        "change. The current contents are attached: merge what you typed into "
        "them and save again. Nothing was written."
    ),
    "resolvedByRefresh": True,
}

E1059 = {
    "description": "{path} cannot be read as declared tax inputs ({problem}).",
    "resolution": (
        "This file is hand-editable, so the usual cause is broken YAML "
        "indentation, a duplicated key, or a section written as a single "
        "value or a list instead of a block. Fix it in the tax inputs form "
        "using 'replace the whole file', which is the one save that works on "
        "an unparseable file, or correct {path} by hand. Nothing was written."
    ),
    "resolvedByRefresh": False,
}

E1060 = {
    "description": (
        "Prepaid-tax account `{account}` belongs to no known prepaid-tax component."
    ),
    "resolution": (
        "The tax workspace splits prepaid tax into TDS (which a declared "
        "salary TDS replaces) and advance tax (which it never replaces), so an "
        "account it cannot place would be counted as TDS and then silently "
        "dropped by a declaration. Add the account's prefix to the prepaid "
        "component map in backend/app/service/tax/report.py, beside the known ones "
        "({known}), or post the amount under one of them. Nothing was changed."
    ),
    "resolvedByRefresh": False,
}

E1061 = {
    "description": (
        "Posting metadata `tax-fy: {value}` at {filename}:{lineno} is not a "
        "financial year."
    ),
    "resolution": (
        "`tax-fy` says which financial year a direct-tax payment was made FOR, "
        "and its value is that FY's START year as a quoted four-digit string: "
        'FY2025-26 is `tax-fy: "2025"`. A challan\'s assessment year is one '
        'ahead of the financial year, so AY 2026-27 is `tax-fy: "2025"`. '
        "Correct the value in {filename} at line {lineno}, or remove the tag to "
        "credit the payment to the year its date falls in. Nothing was changed."
    ),
    "resolvedByRefresh": False,
}


E1074 = {
    "description": "The stored upload {stored_name} no longer matches the file this run inspected.",
    "resolution": "Run /ingest-stmt again on the file; the agent will re-inspect it.",
    "resolvedByRefresh": False,
}
E1075 = {
    "description": "Proving run {prove_id} is not known (the app may have restarted).",
    "resolution": "Run /ingest-stmt again; a saved template makes it a direct match.",
    "resolvedByRefresh": False,
}
E1076 = {
    "description": "This proving run has used all {max} dry runs.",
    "resolution": "Map the file in the browser import wizard, or re-run /ingest-stmt with a hint (e.g. which row is the header).",
    "resolvedByRefresh": False,
}
E1077 = {
    "description": "The template names accounts that are not open in the journal: {accounts}.",
    "resolution": "Pick an open account (GET /api/accounts) or open the account in Settings first.",
    "resolvedByRefresh": False,
}
E1078 = {
    "description": "Cannot tell whether this statement is a bank or card statement from its accounts: {accounts}.",
    "resolution": "Use a single Assets:Checking:* / Assets:Bank:* (bank) or Liabilities:CreditCard:* (card) account.",
    "resolvedByRefresh": False,
}
E1079 = {
    "description": "The statement was described as {given} but its account makes it {derived}.",
    "resolution": "Drop the kind hint or choose the matching account.",
    "resolvedByRefresh": False,
}
E1080 = {
    "description": "{kind} files can only be imported through a dedicated parser, and this file is not a Groww export.",
    "resolution": "Kite and other investment exports are not supported by the agent; record these trades by hand.",
    "resolvedByRefresh": False,
}
E1081 = {
    "description": "The parse did not pass validation: {checks}.",
    "resolution": "Nothing was saved. Re-prove the layout (/ingest-stmt) or map it in the browser wizard.",
    "resolvedByRefresh": False,
}
E1082 = {
    "description": "{field} {value} is not valid: {reason}.",
    "resolution": "Correct the value and save again. Nothing was changed.",
    "resolvedByRefresh": False,
}
E1083 = {
    "description": "{account} does not take these properties: they apply only to {expected} accounts.",
    "resolution": "Pick an account of the right kind. Nothing was changed.",
    "resolvedByRefresh": False,
}
E1084 = {
    "description": "{account} is closed, so its properties cannot be changed.",
    "resolution": "Closed accounts are read-only. Nothing was changed.",
    "resolvedByRefresh": False,
}
E1085 = {
    "description": "Transaction {txn_id} no longer has a posting on the EMI account, so the split is out of date.",
    "resolution": "Reload the split review and apply it again. Nothing was changed.",
    "resolvedByRefresh": True,
}
E1086 = {
    "description": "The EMI split cannot be applied: {reason}.",
    "resolution": "Correct the plans in the split review and apply again. Nothing was changed.",
    "resolvedByRefresh": False,
}
E1087 = {
    "description": "The report period is not valid: {reason}.",
    "resolution": "Pass both from and to (YYYY-MM-DD) with from on or before to, or pass fy alone where the report takes one, never both.",
    "resolvedByRefresh": False,
}
E1088 = {
    "description": "{account} already has an opening balance: {ledger_amount} INR on {date}.",
    "resolution": "Use Correct to change it. Nothing was changed.",
    "resolvedByRefresh": False,
}
E1089 = {
    "description": "The opening date {date} for {account} is not valid: {reason}.",
    "resolution": "Pick an earlier date: on or before {limit} when the date is after today, before {limit} otherwise. Nothing was changed.",
    "resolvedByRefresh": False,
}
E1090 = {
    "description": "{account} cannot take this opening balance: {reason}.",
    "resolution": "Pick a balance-sheet account and one of the directions it allows. Nothing was changed.",
    "resolvedByRefresh": False,
}
E1091 = {
    "description": "The opening balance of {account} is not the one this correction was made against.",
    "resolution": "Reload the opening balances and correct it again. Nothing was changed.",
    "resolvedByRefresh": True,
}
E1092 = {
    "description": "The opening lot for {symbol} is not valid: {field} {reason}.",
    "resolution": "Correct the lot and save again; for a scheme that already has orders, import the full order history instead. Nothing was changed.",
    "resolvedByRefresh": False,
}
E1093 = {
    "description": "{field} {value} is not a valid amount: {reason}.",
    "resolution": "Enter an amount of zero or more, with at most 2 decimal places.",
    "resolvedByRefresh": False,
}
E1094 = {
    "description": "Renaming {account} to {new_name} would make {template} invalid: {reason}.",
    "resolution": "Pick a name it accepts, or delete or fix that file first. Nothing was changed.",
    "resolvedByRefresh": False,
}
E1095 = {
    "description": "{account} cannot be renamed while a ledger import that maps to it is waiting for review ({files}).",
    "resolution": 'Open Import, find that plain-text ledger review (under "Already keep a plain-text ledger?") and either Post it or press Discard below it, then rename. If you have already left that review, restart the app to drop it. Its account mapping is held by the review screen, so the rename cannot reach it. Nothing was changed.',
    "resolvedByRefresh": False,
}
E1098 = {
    "description": "The cash-flow rows selection is not valid: {reason}.",
    "resolution": "Pass at most one of node, source and target together, or category; pass side only with category or a plain range.",
    "resolvedByRefresh": False,
}
E1062 = {
    "description": "Proving run {prove_id} has no dry run that passed validation.",
    "resolution": "Let the prover finish a passing dry run before staging.",
    "resolvedByRefresh": False,
}
E1063 = {
    "description": "This file has no {use} to stage from.",
    "resolution": "Stage with the proving run's passing draft instead (use: proof).",
    "resolvedByRefresh": False,
}
E1064 = {
    "description": "A template with fingerprint {fingerprint} already exists.",
    "resolution": "Stage with use: template_match, or open an upgrade run (inspect --upgrade) to replace it.",
    "resolvedByRefresh": False,
}
E1065 = {
    "description": "Duplicate status changed for {count} row(s) since staging: {ids}.",
    "resolution": "Review the changed rows; committing again will use the updated statuses.",
    "resolvedByRefresh": False,
}
E1066 = {
    "description": "Proving run {prove_id} has no dry-run result to show yet.",
    "resolution": "Run a dry run first.",
    "resolvedByRefresh": False,
}
E1067 = {
    "description": "The upgraded draft's header fingerprints as {got}, not the template being upgraded ({expected}).",
    "resolution": "For a CSV or XLS upgrade, keep the same header row as the template being upgraded. For a PDF statement of another card, do not upgrade: if this card replaced the upgraded card on the same account, remove the old card's saved layout by hand (its file in <storage>/templates/pdf/), then run /ingest-stmt again; otherwise run /ingest-stmt again with the account this card belongs to.",
    "resolvedByRefresh": False,
}
E1068 = {
    "description": "The password did not open {filename}.",
    "resolution": "Check the password from your bank's e-mail and try again. SpendSift never stores it.",
    "resolvedByRefresh": False,
}
E1069 = {
    "description": "{filename} could not be read as a PDF statement: {reason}.",
    "resolution": "Scanned or damaged PDFs are not supported. Download the statement again from your bank.",
    "resolvedByRefresh": False,
}
E1070 = {
    "description": "This statement did not reconcile with its own summary, so nothing was imported: {checks}.",
    "resolution": "Nothing was saved. Run /ingest-stmt on this file in Claude Code once to update the saved layout for this card.",
    "resolvedByRefresh": False,
}
E1071 = {
    "description": "{account} already belongs to another card's statement layout.",
    "resolution": "If this card replaced an older card on the same account (a new card number), remove the old card's saved layout by hand: the file in <storage>/templates/pdf/ whose default_account is {account} and whose card_last4 is the old card's last 4 digits. Then run /ingest-stmt again. Otherwise run /ingest-stmt again with the account this card belongs to. Never open a second account for the same card debt.",
    "resolvedByRefresh": False,
}
E1072 = {
    "description": "{tag!r} is not a valid tag. A tag may hold only letters, digits and - _ / . (spaces become -).",
    "resolution": "Rename the tag, e.g. travel/madikeri-oct-2026.",
    "resolvedByRefresh": False,
}
E1073 = {
    "description": "the rows were committed; the learned rules were not saved: {reason}",
    "resolution": "Do not commit this batch again: its rows are in the journal. To keep the rules, add them to rules/learned.yaml by hand: {rules}.",
    "resolvedByRefresh": False,
}
E1096 = {
    "description": "{benchmark} is not a benchmark in the benchmarks file.",
    "resolution": "Pick one of the benchmarks the screen offers, or add it to config/benchmarks.yaml in your data folder.",
    "resolvedByRefresh": False,
}
E1097 = {
    "description": "The benchmarks file {file} cannot be used: {reason}.",
    "resolution": "Fix the file (it is plain YAML with comments at the top) or delete it and a default one is written next time. Nothing was changed.",
    "resolvedByRefresh": False,
}
