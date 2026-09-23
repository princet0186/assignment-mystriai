"""Improvement: preview an import before committing it.

The owner's words were "when I retry an import, the numbers sometimes move
again". Fixing duplicate handling stops the numbers moving, but it does not tell
the owner what a file will do *before* they commit it. A dry run answers that:
same code path, same counts, nothing written.
"""
import tempfile
import unittest
from pathlib import Path

from ledger import importing, reporting, storage

INVOICE_HEADER = 'customer_id,invoice_number,amount,due_date\n'
PAYMENT_HEADER = 'payment_id,customer_id,invoice_number,amount\n'

MIXED = (INVOICE_HEADER
         + 'HARBOR,DRY-1,84.00,2026-09-12\n'      # line 2, new
         + 'NORTH,DRY-BAD,not-a-number,2026-09-12\n'  # line 3, rejected
         + 'HARBOR,INV-100,1250.00,2026-09-01\n'  # line 4, already present
         + 'MAPLE,DRY-2,100.00,2026-09-13\n')     # line 5, new


class DryRunImport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'demo.sqlite3'
        self.db = storage.connect(self.path)
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def snapshot(self):
        return (reporting.overview(self.db)['summary'],
                [dict(r) for r in reporting.invoices(self.db)],
                [dict(r) for r in self.db.execute('SELECT * FROM payments ORDER BY payment_id')])

    def test_dry_run_reports_the_same_counts_as_the_real_import(self):
        preview = importing.import_csv(self.db, MIXED, 'invoices', dry_run=True)
        real = importing.import_csv(self.db, MIXED, 'invoices')
        for key in ('imported', 'skipped', 'rejected', 'errors'):
            self.assertEqual(preview[key], real[key], f'{key} should match the real import')

    def test_dry_run_changes_nothing(self):
        before = self.snapshot()
        importing.import_csv(self.db, MIXED, 'invoices', dry_run=True)
        self.assertEqual(self.snapshot(), before)

    def test_dry_run_result_is_flagged(self):
        preview = importing.import_csv(self.db, MIXED, 'invoices', dry_run=True)
        self.assertIs(preview['dry_run'], True)
        self.assertNotIn('dry_run', importing.import_csv(self.db, MIXED, 'invoices'))

    def test_dry_run_survives_a_reconnect(self):
        """Nothing may leak to disk, even after the connection is closed."""
        before = self.snapshot()
        importing.import_csv(self.db, MIXED, 'invoices', dry_run=True)
        self.db.close()
        self.db = storage.connect(self.path)
        self.assertEqual(self.snapshot(), before)

    def test_dry_run_of_payments_does_not_change_balances(self):
        before = self.snapshot()
        preview = importing.import_csv(
            self.db, PAYMENT_HEADER + 'DRY-P1,MAPLE,INV-200,1250.00\n',
            'payments', dry_run=True)
        self.assertEqual(preview['imported'], 1)
        self.assertEqual(self.snapshot(), before)

    def test_dry_run_predicts_an_unmatched_payment(self):
        preview = importing.import_csv(
            self.db, PAYMENT_HEADER + 'DRY-P2,HARBOR,NO-SUCH,10.00\n',
            'payments', dry_run=True)
        self.assertEqual(preview['imported'], 1)
        self.assertEqual(reporting.overview(self.db)['unmatched_payments'], [])

    def test_dry_run_still_rejects_an_invalid_header(self):
        with self.assertRaises(ValueError):
            importing.import_csv(self.db, 'customer,invoice,value\nHARBOR,X,1\n',
                                 'invoices', dry_run=True)

    def test_dry_run_on_a_file_with_nothing_to_do(self):
        """A valid file whose rows are all already present proposes no changes."""
        importing.import_csv(
            self.db, INVOICE_HEADER + 'HARBOR,DONE-1,10.00,2026-09-12\n', 'invoices')
        before = self.snapshot()
        preview = importing.import_csv(
            self.db, INVOICE_HEADER + 'HARBOR,DONE-1,10.00,2026-09-12\n',
            'invoices', dry_run=True)
        self.assertEqual((preview['imported'], preview['skipped'], preview['rejected']), (0, 1, 0))
        self.assertEqual(self.snapshot(), before)

    def test_dry_run_of_an_empty_file_is_a_clean_zero_report(self):
        preview = importing.import_csv(self.db, INVOICE_HEADER, 'invoices', dry_run=True)
        self.assertEqual((preview['imported'], preview['skipped'], preview['rejected']), (0, 0, 0))
        self.assertEqual(preview['errors'], [])

    def test_a_real_import_after_a_dry_run_still_writes(self):
        """The rollback must not leave the connection unable to commit."""
        importing.import_csv(self.db, MIXED, 'invoices', dry_run=True)
        importing.import_csv(self.db, MIXED, 'invoices')
        self.db.close()
        self.db = storage.connect(self.path)
        numbers = {r['invoice_number'] for r in reporting.invoices(self.db)}
        self.assertIn('DRY-1', numbers)
        self.assertIn('DRY-2', numbers)


if __name__ == '__main__':
    unittest.main()
