"""The owner's existing register must survive the repair, a migration and a restart.

Every record in fixtures/expected-records.json is compared field by field against
a working copy of fixtures/existing-register.sqlite3 after the current schema and
migration have been applied. Money is compared as exact two-decimal strings, so a
float rounding change cannot pass silently.

These tests never touch fixtures/ and never use .local/; each one works on its own
temporary copy.
"""
import json
import shutil
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from ledger import importing, reporting, storage

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / 'fixtures' / 'existing-register.sqlite3'
EXPECTED = json.loads((ROOT / 'fixtures' / 'expected-records.json').read_text())

INVOICE_HEADER = 'customer_id,invoice_number,amount,due_date\n'
PAYMENT_HEADER = 'payment_id,customer_id,invoice_number,amount\n'


def money(value):
    """Two-decimal string, so 56.78 stored as a float still compares exactly."""
    return str(Decimal(str(value)).quantize(Decimal('0.01')))


class ExistingRegister(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'clearledger.sqlite3'
        shutil.copy2(FIXTURE, self.path)   # a working copy; the fixture stays intact
        self.db = storage.connect(self.path)   # applies the migration

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def reopen(self):
        """Close and reconnect, standing in for an application restart."""
        self.db.close()
        self.db = storage.connect(self.path)

    # ---- preservation -------------------------------------------------

    def test_all_customers_are_preserved(self):
        rows = {r['customer_id']: r['name']
                for r in self.db.execute('SELECT customer_id, name FROM customers')}
        self.assertEqual(rows, {c['customer_id']: c['name'] for c in EXPECTED['customers']})

    def test_every_invoice_keeps_its_id_identity_amount_and_due_date(self):
        rows = {r['id']: r for r in self.db.execute('SELECT * FROM invoices')}
        self.assertEqual(len(rows), len(EXPECTED['invoices']))
        for want in EXPECTED['invoices']:
            got = rows.get(want['id'])
            self.assertIsNotNone(got, f"invoice id {want['id']} is missing")
            self.assertEqual(got['customer_id'], want['customer_id'])
            self.assertEqual(got['invoice_number'], want['invoice_number'])
            self.assertEqual(money(got['amount']), want['amount'])
            self.assertEqual(got['due_date'], want['due_date'])

    def test_every_payment_keeps_its_identity_amount_and_allocation(self):
        rows = {r['payment_id']: r for r in self.db.execute('SELECT * FROM payments')}
        self.assertEqual(len(rows), len(EXPECTED['payments']))
        for want in EXPECTED['payments']:
            got = rows.get(want['payment_id'])
            self.assertIsNotNone(got, f"payment {want['payment_id']} is missing")
            self.assertEqual(got['customer_id'], want['customer_id'])
            self.assertEqual(got['invoice_number'], want['invoice_number'])
            self.assertEqual(money(got['amount']), want['amount'])
            self.assertEqual(got['invoice_id'], want['invoice_id'],
                             'payment allocation must not move')

    def test_the_unmatched_payment_stays_unmatched(self):
        """KEEP-U1 references MAPLE/WAIT-900, an invoice that does not exist."""
        unmatched = reporting.overview(self.db)['unmatched_payments']
        self.assertEqual([p['payment_id'] for p in unmatched], ['KEEP-U1'])
        self.assertEqual(money(unmatched[0]['amount']), '33.33')

    def test_summary_matches_the_documented_starting_totals(self):
        summary = reporting.overview(self.db)['summary']
        self.assertEqual(summary['invoice_count'], EXPECTED['summary']['invoice_count'])
        self.assertEqual(summary['open_count'], EXPECTED['summary']['open_count'])
        self.assertEqual(money(summary['outstanding']), EXPECTED['summary']['outstanding'])

    # ---- migration ----------------------------------------------------

    def test_migration_adds_the_unique_invoice_index(self):
        index = self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='invoices_identity'"
        ).fetchone()
        self.assertIsNotNone(index, 'the unique-identity index should exist after migration')

    def test_migration_is_idempotent_and_repeated_connects_are_safe(self):
        before = reporting.overview(self.db)['summary']
        for _ in range(3):
            self.reopen()
        self.assertEqual(reporting.overview(self.db)['summary'], before)

    def test_migration_refuses_a_register_that_already_has_duplicates(self):
        """Rather than guessing which copy to delete, the migration reports it."""
        raw = sqlite3.connect(self.path)
        raw.execute('DROP INDEX IF EXISTS invoices_identity')
        raw.execute('''INSERT INTO invoices (customer_id, invoice_number, amount, due_date)
                       VALUES ('HARBOR','INV-100',1250.00,'2026-09-01')''')
        raw.commit()
        raw.close()
        with self.assertRaises(ValueError) as caught:
            storage.connect(self.path)
        self.assertIn('HARBOR/INV-100', str(caught.exception))

    # ---- still usable afterwards --------------------------------------

    def test_new_invoice_and_payment_import_after_migration(self):
        invoices = importing.import_csv(
            self.db, INVOICE_HEADER + 'HARBOR,NEW-800,500.00,2026-09-20\n', 'invoices')
        self.assertEqual(invoices['imported'], 1)
        payments = importing.import_csv(
            self.db, PAYMENT_HEADER + 'NEW-P1,HARBOR,NEW-800,200.00\n', 'payments')
        self.assertEqual(payments['imported'], 1)
        row = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'NEW-800')
        self.assertEqual(money(row['balance']), '300.00')

    def test_existing_and_new_records_both_survive_a_restart(self):
        importing.import_csv(
            self.db, INVOICE_HEADER + 'HARBOR,NEW-800,500.00,2026-09-20\n', 'invoices')
        importing.import_csv(
            self.db, PAYMENT_HEADER + 'NEW-P1,HARBOR,NEW-800,200.00\n', 'payments')
        self.reopen()

        rows = {(r['customer_id'], r['invoice_number']): r
                for r in reporting.invoices(self.db)}
        self.assertEqual(money(rows[('HARBOR', 'NEW-800')]['balance']), '300.00',
                         'the newly imported records should survive a restart')
        keep = rows[('HARBOR', 'KEEP-700')]
        self.assertEqual(money(keep['paid']), '56.78',
                         'the original allocation should survive a restart')
        self.assertEqual(money(keep['balance']), '400.00')
        self.assertEqual(len(rows), len(EXPECTED['invoices']) + 1)

    def test_reimporting_an_existing_invoice_does_not_inflate_the_register(self):
        """The defect that motivated the migration, checked on the owner's data."""
        before = reporting.overview(self.db)['summary']['outstanding']
        result = importing.import_csv(
            self.db, INVOICE_HEADER + 'HARBOR,KEEP-700,456.78,2026-09-09\n', 'invoices')
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(reporting.overview(self.db)['summary']['outstanding'], before)

    def test_fixture_files_are_never_modified_by_the_tests(self):
        self.assertTrue(FIXTURE.exists())
        probe = sqlite3.connect(f'file:{FIXTURE}?mode=ro', uri=True)
        try:
            count = probe.execute('SELECT COUNT(*) FROM invoices').fetchone()[0]
        finally:
            probe.close()
        self.assertEqual(count, len(EXPECTED['invoices']))


if __name__ == '__main__':
    unittest.main()
