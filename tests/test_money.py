"""Money precision: the screen, the JSON and the export must agree exactly.

The starter summed REAL payment amounts and truncated the export with
int(value * 100) / 100. Those are different bugs with the same symptom, which
the owner reported as "the downloaded report and the screen don't always agree".
"""
import tempfile
import unittest
from pathlib import Path

from ledger import importing, reporting, storage
from ledger.money import format_amount, to_amount, to_paise

INVOICE_HEADER = 'customer_id,invoice_number,amount,due_date\n'
PAYMENT_HEADER = 'payment_id,customer_id,invoice_number,amount\n'


class MoneyHelpers(unittest.TestCase):
    def test_to_paise_ignores_float_representation_error(self):
        self.assertEqual(to_paise(19.990000000000002), 1999)
        self.assertEqual(to_paise(1699.5699999999997), 169957)
        self.assertEqual(to_paise(0.07), 7)

    def test_to_paise_rounds_half_up(self):
        self.assertEqual(to_paise('0.005'), 1)
        self.assertEqual(to_paise('2.675'), 268)

    def test_format_amount_does_not_truncate(self):
        """int(v * 100) / 100 reported 1699.56 for a balance of 1699.57."""
        self.assertEqual(format_amount(169957), '1699.57')
        self.assertEqual(format_amount(63631), '636.31')

    def test_round_trip_through_to_amount(self):
        for paise in (0, 7, 1999, 169957, 1234567899):
            self.assertEqual(to_paise(to_amount(paise)), paise)

    def test_large_permitted_amount_is_exact(self):
        self.assertEqual(format_amount(to_paise(10000000.00)), '10000000.00')


class ReportedMoney(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.tmp.name) / 'demo.sqlite3')
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def invoice(self, number):
        return next(r for r in reporting.invoices(self.db) if r['invoice_number'] == number)

    def export_row(self, number):
        line = next(line for line in reporting.export_csv(self.db).splitlines()
                    if f',{number},' in line)
        return dict(zip(['customer_id', 'invoice_number', 'amount', 'paid', 'balance', 'status'],
                        line.split(',')))

    def test_two_payments_summing_to_the_invoice_leave_no_residue(self):
        """INV-300 is 19.99 and already has a 10.00 payment; 9.99 closes it."""
        importing.import_csv(
            self.db, PAYMENT_HEADER + 'P-REST,NORTH,INV-300,9.99\n', 'payments')
        row = self.invoice('INV-300')
        self.assertEqual(row['paid'], 19.99, 'paid must not read 19.990000000000002')
        self.assertEqual(row['balance'], 0.0, 'balance must be exactly zero, not -3.55e-15')
        self.assertEqual(row['status'], 'paid')

    def test_many_small_payments_accumulate_exactly(self):
        importing.import_csv(
            self.db, INVOICE_HEADER + 'HARBOR,CENTS-1,1.00,2026-09-20\n', 'invoices')
        rows = ''.join(f'CENT-{n},HARBOR,CENTS-1,0.01\n' for n in range(100))
        result = importing.import_csv(self.db, PAYMENT_HEADER + rows, 'payments')
        self.assertEqual(result['imported'], 100)
        row = self.invoice('CENTS-1')
        self.assertEqual(row['paid'], 1.00)
        self.assertEqual(row['balance'], 0.0)
        self.assertEqual(row['status'], 'paid')

    def test_export_agrees_with_the_screen_on_a_truncation_case(self):
        """721.28 - 84.97 truncated to 636.30 while the screen showed 636.31."""
        importing.import_csv(
            self.db, INVOICE_HEADER + 'HARBOR,TRUNC-1,721.28,2026-09-20\n', 'invoices')
        importing.import_csv(
            self.db, PAYMENT_HEADER + 'TRUNC-P,HARBOR,TRUNC-1,84.97\n', 'payments')
        row = self.invoice('TRUNC-1')
        exported = self.export_row('TRUNC-1')
        self.assertEqual(exported['balance'], '636.31')
        self.assertEqual(exported['balance'], f"{row['balance']:.2f}")

    def test_export_agrees_with_the_screen_for_every_invoice(self):
        importing.import_csv(self.db, INVOICE_HEADER
                             + 'HARBOR,AGREE-1,2679.41,2026-09-20\n'
                             + 'MAPLE,AGREE-2,4881.28,2026-09-21\n'
                             + 'NORTH,AGREE-3,2265.93,2026-09-22\n', 'invoices')
        importing.import_csv(self.db, PAYMENT_HEADER
                             + 'A-P1,HARBOR,AGREE-1,979.84\n'
                             + 'A-P2,MAPLE,AGREE-2,227.39\n'
                             + 'A-P3,NORTH,AGREE-3,679.26\n', 'payments')
        exported = {r['invoice_number']: r for r in
                    [dict(zip(['customer_id', 'invoice_number', 'amount', 'paid',
                               'balance', 'status'], line.split(',')))
                     for line in reporting.export_csv(self.db).splitlines()[1:]]}
        for row in reporting.invoices(self.db):
            with self.subTest(invoice=row['invoice_number']):
                csv_row = exported[row['invoice_number']]
                self.assertEqual(csv_row['amount'], f"{row['amount']:.2f}")
                self.assertEqual(csv_row['paid'], f"{row['paid']:.2f}")
                self.assertEqual(csv_row['balance'], f"{row['balance']:.2f}")
                self.assertEqual(csv_row['status'], row['status'])

    def test_outstanding_equals_the_sum_of_exported_positive_balances(self):
        importing.import_csv(self.db, PAYMENT_HEADER
                             + 'SUM-P1,NORTH,INV-300,9.99\n'
                             + 'SUM-P2,HARBOR,INV-100,0.01\n', 'payments')
        exported = sum(float(line.split(',')[4])
                       for line in reporting.export_csv(self.db).splitlines()[1:]
                       if float(line.split(',')[4]) > 0)
        self.assertEqual(reporting.overview(self.db)['summary']['outstanding'],
                         round(exported, 2))

    def test_money_fields_are_json_numbers(self):
        """BUSINESS_RULES.md: money fields in JSON are numbers, not strings."""
        row = self.invoice('INV-100')
        for key in ('amount', 'paid', 'balance'):
            self.assertIsInstance(row[key], (int, float))
            self.assertNotIsInstance(row[key], str)
        summary = reporting.overview(self.db)['summary']
        self.assertIsInstance(summary['outstanding'], (int, float))


if __name__ == '__main__':
    unittest.main()
