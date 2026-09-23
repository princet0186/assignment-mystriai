import csv
import io

from .money import format_amount, to_amount, to_paise

STATUSES = ('all', 'open', 'paid')


def _paid_paise_by_invoice(db):
    """Total paid per invoice id, summed in integer paise.

    Each payment is converted individually before summing rather than using
    SQL SUM(), because adding REAL values accumulates representation error:
    10.00 + 9.99 against a 19.99 invoice yielded a balance of -3.55e-15 and a
    paid figure of 19.990000000000002 in the original implementation.
    """
    totals = {}
    for row in db.execute('SELECT invoice_id, amount FROM payments WHERE invoice_id IS NOT NULL'):
        totals[row['invoice_id']] = totals.get(row['invoice_id'], 0) + to_paise(row['amount'])
    return totals


def _rows_in_paise(db):
    """Every invoice with its amount, paid total and balance as integer paise."""
    paid_by_invoice = _paid_paise_by_invoice(db)
    rows = db.execute('''
        SELECT i.id, i.customer_id, c.name AS customer_name, i.invoice_number,
               i.amount, i.due_date
        FROM invoices i JOIN customers c ON c.customer_id = i.customer_id
        ORDER BY i.id
    ''').fetchall()
    for row in rows:
        amount = to_paise(row['amount'])
        paid = paid_by_invoice.get(row['id'], 0)
        yield row, amount, paid, amount - paid


def invoices(db, status='all'):
    """Return invoice rows with paid, balance and status.

    Balances are exact at currency precision, so the same record reads
    identically on screen and in the export.
    """
    if status not in STATUSES:
        raise ValueError('status must be all, open or paid')
    result = []
    for row, amount, paid, balance in _rows_in_paise(db):
        item = dict(row)
        item['amount'] = to_amount(amount)
        item['paid'] = to_amount(paid)
        item['balance'] = to_amount(balance)
        # 'open' is a positive balance at currency precision; zero or negative
        # (an overpayment) is paid.
        item['status'] = 'open' if balance > 0 else 'paid'
        result.append(item)
    if status != 'all':
        result = [r for r in result if r['status'] == status]
    return result


def overview(db):
    invoice_count = 0
    open_count = 0
    outstanding = 0
    for _row, _amount, _paid, balance in _rows_in_paise(db):
        invoice_count += 1
        if balance > 0:
            outstanding += balance
            open_count += 1
    unmatched = [{**dict(r), 'amount': to_amount(to_paise(r['amount']))}
                 for r in db.execute('''SELECT payment_id, customer_id, invoice_number,
                 amount FROM payments WHERE invoice_id IS NULL ORDER BY payment_id''')]
    return {'invoices': invoices(db), 'unmatched_payments': unmatched, 'summary': {
        'invoice_count': invoice_count,
        'open_count': open_count,
        'outstanding': to_amount(outstanding),
    }}


def export_csv(db):
    """CSV of all invoices. Values must agree with the same record on screen."""
    output = io.StringIO(newline='')
    fields = ['customer_id', 'invoice_number', 'amount', 'paid', 'balance', 'status']
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row, amount, paid, balance in _rows_in_paise(db):
        writer.writerow({
            'customer_id': row['customer_id'],
            'invoice_number': row['invoice_number'],
            'amount': format_amount(amount),
            'paid': format_amount(paid),
            'balance': format_amount(balance),
            'status': 'open' if balance > 0 else 'paid',
        })
    return output.getvalue()
