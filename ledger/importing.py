import csv
import io
from .validation import HEADERS, normalize
from .storage import insert_invoice, insert_payment
from .matching import find_invoice


def import_csv(db, text, kind, dry_run=False):
    """Import one CSV of invoices or payments and report per-row outcomes.

    An invalid header rejects the whole import and writes nothing. An invalid
    data row rejects only that row: validation happens inside the per-row try
    block so one bad value cannot discard the rest of the file
    (BUSINESS_RULES.md, "CSV imports").

    With dry_run=True the same work is done inside a transaction that is always
    rolled back, so the caller gets the real counts and errors without changing
    a single record. The counts are produced by the same code path as a real
    import, not by a separate estimate that could drift out of step.
    """
    if kind not in HEADERS:
        raise ValueError('Unknown import kind')
    reader = csv.DictReader(io.StringIO(text.lstrip('﻿')))
    if reader.fieldnames != HEADERS[kind]:
        raise ValueError('Expected CSV header: ' + ','.join(HEADERS[kind]))
    customers = {r[0] for r in db.execute('SELECT customer_id FROM customers')}
    result = {'imported': 0, 'skipped': 0, 'rejected': 0, 'errors': []}
    if dry_run:
        result['dry_run'] = True
    # enumerate over the reader itself so `line` stays the true CSV line number
    # (header is line 1) regardless of how many earlier rows were rejected.
    for line, raw in enumerate(reader, 2):
        try:
            row = normalize(raw, kind, customers)
            if kind == 'invoices':
                outcome = insert_invoice(db, row)
            else:
                outcome = insert_payment(db, row, find_invoice(db, row))
            result[outcome] += 1
            if not dry_run:
                db.commit()
        except ValueError as exc:
            if not dry_run:
                db.rollback()
            result['rejected'] += 1
            result['errors'].append({'line': line, 'reason': str(exc)})
    if dry_run:
        # Discard everything this preview did, including rows already counted.
        db.rollback()
    return result
