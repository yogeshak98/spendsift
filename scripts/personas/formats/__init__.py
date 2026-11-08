"""Statement writers: render a persona's simulated truth as the files each
institution exports, laid out like the real ones.

Every writer has the same signature,
`write(account, events, period, persona, out_dir) -> list[Path]`, and
returns its statement files: one for the whole period for a bank or broker,
one per statement cycle for a card. Beside each file it writes
`<file>.truth.json`, the ordered mapping from printed row to truth event the
checker matches rows with. Sidecars are build output: never committed, and
never handed to the agent in a live demo.
"""
