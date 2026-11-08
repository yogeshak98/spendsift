You are categorizing spending for one person's personal finance journal.

The numbered list below is MERCHANT NAMES, one per line, in the form
`<number>\t<merchant>`. They are not transactions: each name stands for every
past payment to that merchant, and many are opaque UPI handles or card
descriptors.

For each merchant, answer with one line `<number>\t<account>`.

Answer with an `Expenses:` account only. Prefer one of the categories this
journal already uses:

{{OPEN_EXPENSE_ACCOUNTS}}

If none of them fits, you may answer with a new name of the form
`Expenses:Group` or `Expenses:Group:Subgroup` — each segment starting with a
capital letter, letters and digits only. It will be created.

Never answer `Expenses:Uncategorized`, and never answer an account outside the
`Expenses:` tree — both are rejected.

If you cannot tell what a merchant is, LEAVE THAT NUMBER OUT of your answer
altogether. A merchant you skip simply stays on the to-do list, which is safe;
a guessed one silently misfiles real spending into the wrong category totals.

Output only the `<number>\t<account>` lines, nothing else.

Merchants:
