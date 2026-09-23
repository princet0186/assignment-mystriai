import csv
import io
from .validation import HEADERS, normalize
from .storage import insert_invoice, insert_payment
from .matching import find_invoice


def import_csv(db, text, kind):
    """Import one CSV of invoices or payments and report per-row outcomes.

    An invalid header rejects the whole import and writes nothing. An invalid
    data row rejects only that row: validation happens inside the per-row try
    block so one bad value cannot discard the rest of the file
    (BUSINESS_RULES.md, "CSV imports").
    """
    if kind not in HEADERS:
        raise ValueError('Unknown import kind')
    reader = csv.DictReader(io.StringIO(text.lstrip('﻿')))
    if reader.fieldnames != HEADERS[kind]:
        raise ValueError('Expected CSV header: ' + ','.join(HEADERS[kind]))
    customers = {r[0] for r in db.execute('SELECT customer_id FROM customers')}
    result = {'imported': 0, 'skipped': 0, 'rejected': 0, 'errors': []}
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
            db.commit()
        except ValueError as exc:
            db.rollback()
            result['rejected'] += 1
            result['errors'].append({'line': line, 'reason': str(exc)})
    return result
