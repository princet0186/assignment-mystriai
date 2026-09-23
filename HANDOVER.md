# Handover

- Name: Prince Trivedi
- Email used for this application: development@amrrtechsols.com
- Chosen track: A — Repair the register
- Why this track (one or two sentences): I prefer inheriting a system, finding out
  what it actually does versus what it promises, and leaving it verifiable. The
  defects here are business-visible rather than cosmetic, which is the kind of
  repair I enjoy evidencing.
- Approximate total time, including setup and handover: ~4 hours across two sessions.

## Run and verify

Python 3.10+ (tested on 3.14.7). No third-party dependencies; nothing to install.
Use `python` instead of `python3` on Windows.

```bash
# 1. Unit and regression suite (68 tests, ~0.2s)
python3 -m unittest discover -s tests -v

# 2. End-to-end over HTTP against the owner's existing register
python3 restore_fixture.py --replace     # server must be stopped
python3 app.py --port 8788               # leave running in another terminal
python3 verify.py --port 8788            # exits 0 when all 38 checks pass

# 3. The browser workflow
python3 app.py                           # open http://127.0.0.1:8787
```

`verify.py` restarts the server on port 8829 by itself to prove records survive a
restart. Recorded runs of all of the above are in `evidence/`.

## What I delivered

The register could not be trusted for the three things the owner uses it for:
knowing what is owed, retrying an import safely, and sending an export.

**Totals were wrong.** Re-importing an invoice inserted a second row rather than
skipping it, so outstanding grew by the invoice's full amount on every retry —
INR 3,209.99 became 4,459.99 on the demo register after one repeat import. Fixed
with `UNIQUE(customer_id, invoice_number)` plus an in-place migration
([storage.py](ledger/storage.py)) and skip/reject semantics matching the identity
rules. The migration preserves invoice ids and payment allocations, is idempotent,
and refuses rather than guesses if a register already contains duplicates.

**Payments landed on the wrong invoice.** `find_invoice` matched on amount before
identity, so a payment for MAPLE/INV-200 was attached to HARBOR/INV-100 (both
INR 1,250.00) — one customer's payment clearing another's debt.
[matching.py](ledger/matching.py) now requires both customer and invoice number.

**Imports silently lost data.** Validation ran in a list comprehension before the
per-row `try`, so a single bad value discarded the whole file and returned 400 —
while the browser ignored the response and printed "Import complete. Your records
are ready." That pair is the owner's "an import said it was complete, but I
couldn't find the records". Fixed in [importing.py](ledger/importing.py) (per-row
validation, true CSV line numbers) and [app.js](web/app.js) (real counts, each
rejected line and reason).

**The open view and the export both lied.** `invoices()` mapped `open` and `paid`
to the same status, so the open filter returned paid invoices. Separately, money
was summed as floats and the export truncated with `int(v*100)/100`, printing
636.30 for a 636.31 balance. Balances are now derived in integer paise
([money.py](ledger/money.py)) and converted only at the edge.

**Improvement — import preview.** `POST /api/import?kind=...&dry_run=1` and a
"Check without saving" button run the real import path inside a transaction that
is always rolled back, returning true counts and rejected lines with nothing
written. It answers the owner's "when I retry an import, the numbers sometimes
move again" by making the answer visible *before* committing. Tests assert the
preview's counts equal the subsequent real import's.

## Evidence and limits

| What | Where |
| --- | --- |
| Failing before (15 failures on the baseline) | [evidence/01-before-repair.txt](evidence/01-before-repair.txt), commit `e8b87c7` |
| Passing after (68 tests) | [evidence/02-after-repair.txt](evidence/02-after-repair.txt) |
| End-to-end, existing register + restart (38 checks) | [evidence/03-end-to-end.txt](evidence/03-end-to-end.txt) |
| Changed input, predicted then observed | [evidence/04-changed-input.txt](evidence/04-changed-input.txt) |

**Failing-before/passing-after.** The regression suite was committed before any
fix (`e8b87c7`), failing 15 checks against the untouched starter; `git diff
3f64490 HEAD -- ledger/` is the whole repair.

**Existing-register check.** [test_preservation.py](tests/test_preservation.py)
compares a working copy of `fixtures/existing-register.sqlite3` field by field
against `expected-records.json` — ids, names, amounts as exact two-decimal
strings, due dates and payment allocations — then imports new records, reconnects
and re-checks. KEEP-U1 stays unmatched. The fixture itself is never written to.

**Changed input.** `samples/changed-input.csv` re-uses HARBOR/KEEP-700 with a
different amount, adds a valid invoice, a three-decimal amount and an unknown
customer. I predicted `imported=1, rejected=3` on lines 2/4/5 with KEEP-700
preserved at 456.78 and outstanding moving 3,698.19 → 3,948.69. Observed: exactly
that, every line number and reason matching.

**Tested vs assumed.** Everything above was executed. The browser layer is
verified through the API and by hand, not by an automated DOM test — that is the
weakest link and the first thing I would add. Concurrency is untested and out of
scope per the rules; the dry run holds a write transaction, so a second writer
would block.

**Known gaps and next steps.** Unmatched payments are not re-matched when a later
invoice import would satisfy them (explicitly out of scope, but the owner will
eventually hit it — KEEP-U1 sits there permanently). `sqlite3.IntegrityError`
remains caught in [http_app.py](ledger/http_app.py) as defence-in-depth although
`insert_invoice` now rejects first. The next highest-value step is an automated
browser check of the import feedback path, since that defect was invisible to
every server-side test.

**A question I would ask a real owner:** when a payment arrives whose invoice
number does not exist yet, should it stay unmatched forever, or attach when the
invoice appears? The rules defer it, but the answer changes the storage model.

## Tools and judgment

I used **Claude Opus 5** in Claude Code for investigation and as a drafting
assistant, then verified everything by execution.

**1. Defect triage — accepted after independent reproduction.** I asked it to read
`ledger/` against `BUSINESS_RULES.md` and list suspected defects. It produced six
candidates. Rather than trust the list, I reproduced each against a live server
with `curl` and recorded the actual output before changing anything — that is what
`evidence/01` captures. Two "defects" only mattered in combination (the import
abort and the browser's false success), which the reading alone did not reveal.

**2. Rejected: `GROUP_CONCAT` for summing payments.** Its first fix for the float
error summed payments via SQL `GROUP_CONCAT`, then parsed the text. I tested the
round-trip and it passed on all eight awkward values I tried — but it depends on
SQLite's float-to-text conversion, and I could not defend it as obviously correct.
I replaced it with an explicit per-payment loop into integer paise. Slower on
paper, trivially auditable, and identical in result.

**3. Corrected: my own prioritisation.** I had planned to leave the export
truncation unfixed as lower value. Before committing to that, I ran a search for
inputs where `int(v*100)/100` and `round(v,2)` diverge, and found ordinary ones —
`721.28 − 84.97` exports as 636.30 against a screen reading 636.31. A full paisa
wrong on a customer-facing document is not cosmetic, so I fixed it. The search is
what changed my mind, not the reasoning that preceded it.
