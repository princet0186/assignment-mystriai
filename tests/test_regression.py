"""Regression checks for the defects found during investigation.

Each test names the defect it guards and the BUSINESS_RULES.md rule it enforces.
All of these fail against the baseline starter and pass after the repair.
"""
import tempfile
import unittest
from pathlib import Path

from ledger import importing, reporting, storage

INVOICE_HEADER = 'customer_id,invoice_number,amount,due_date\n'
PAYMENT_HEADER = 'payment_id,customer_id,invoice_number,amount\n'


class LedgerTestCase(unittest.TestCase):
    """Fresh seeded demo register per test: 6 invoices, 5 open, INR 3,209.99."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.tmp.name) / 'demo.sqlite3')
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def invoice(self, customer_id, invoice_number):
        return next(r for r in reporting.invoices(self.db)
                    if r['customer_id'] == customer_id
                    and r['invoice_number'] == invoice_number)


class OpenPaidFilter(LedgerTestCase):
    """D1: reporting.invoices mapped 'open' to the 'paid' status."""

    def test_open_filter_returns_only_open_invoices(self):
        rows = reporting.invoices(self.db, 'open')
        self.assertTrue(rows, 'the demo register has open invoices')
        self.assertTrue(all(r['status'] == 'open' for r in rows),
                        'the open filter must not contain paid invoices')

    def test_paid_filter_returns_only_paid_invoices(self):
        rows = reporting.invoices(self.db, 'paid')
        self.assertTrue(all(r['status'] == 'paid' for r in rows))

    def test_open_filter_agrees_with_overview_open_count(self):
        """The owner's complaint: the open view disagreed with the overview."""
        rows = reporting.invoices(self.db, 'open')
        self.assertEqual(len(rows), reporting.overview(self.db)['summary']['open_count'])

    def test_all_is_the_union_of_open_and_paid(self):
        every = reporting.invoices(self.db, 'all')
        opened = reporting.invoices(self.db, 'open')
        paid = reporting.invoices(self.db, 'paid')
        self.assertEqual(len(every), len(opened) + len(paid))


class DuplicateInvoiceImport(LedgerTestCase):
    """D2: re-importing an invoice inserted a second copy and inflated totals."""

    ROW = 'HARBOR,INV-100,1250.00,2026-09-01\n'

    def test_identical_reimport_is_skipped_and_totals_are_unchanged(self):
        before = reporting.overview(self.db)['summary']['outstanding']
        result = importing.import_csv(self.db, INVOICE_HEADER + self.ROW, 'invoices')
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['imported'], 0)
        self.assertEqual(reporting.overview(self.db)['summary']['outstanding'], before)

    def test_reimport_does_not_create_a_second_row(self):
        importing.import_csv(self.db, INVOICE_HEADER + self.ROW, 'invoices')
        count = self.db.execute(
            'SELECT COUNT(*) FROM invoices WHERE customer_id=? AND invoice_number=?',
            ('HARBOR', 'INV-100')).fetchone()[0]
        self.assertEqual(count, 1)

    def test_same_identity_with_different_amount_is_rejected(self):
        result = importing.import_csv(
            self.db, INVOICE_HEADER + 'HARBOR,INV-100,999.00,2026-09-01\n', 'invoices')
        self.assertEqual(result['rejected'], 1)
        self.assertEqual(self.invoice('HARBOR', 'INV-100')['amount'], 1250.00,
                         'the original invoice must be preserved')

    def test_same_identity_with_different_due_date_is_rejected(self):
        result = importing.import_csv(
            self.db, INVOICE_HEADER + 'HARBOR,INV-100,1250.00,2026-12-25\n', 'invoices')
        self.assertEqual(result['rejected'], 1)
        self.assertEqual(self.invoice('HARBOR', 'INV-100')['due_date'], '2026-09-01')

    def test_same_invoice_number_for_a_different_customer_is_allowed(self):
        """Identity is the (customer_id, invoice_number) pair, not the number alone."""
        result = importing.import_csv(
            self.db, INVOICE_HEADER + 'MAPLE,INV-100,10.00,2026-09-20\n', 'invoices')
        self.assertEqual(result['imported'], 1)


class PaymentMatching(LedgerTestCase):
    """D3: find_invoice matched on amount first, attaching payments to the wrong invoice."""

    def test_payment_attaches_to_its_own_customer_and_invoice(self):
        """INV-100 and INV-200 both cost 1250.00; the amount must not decide."""
        importing.import_csv(
            self.db, PAYMENT_HEADER + 'PAY-201,MAPLE,INV-200,1250.00\n', 'payments')
        self.assertEqual(self.invoice('MAPLE', 'INV-200')['paid'], 1250.00)
        self.assertEqual(self.invoice('HARBOR', 'INV-100')['paid'], 0)

    def test_payment_without_a_matching_invoice_stays_unmatched(self):
        result = importing.import_csv(
            self.db, PAYMENT_HEADER + 'PAY-404,HARBOR,INV-NOT-FOUND,50.00\n', 'payments')
        self.assertEqual(result['imported'], 1)
        unmatched = reporting.overview(self.db)['unmatched_payments']
        self.assertIn('PAY-404', [p['payment_id'] for p in unmatched])

    def test_unmatched_payment_changes_no_invoice_balance(self):
        before = reporting.overview(self.db)['summary']['outstanding']
        importing.import_csv(
            self.db, PAYMENT_HEADER + 'PAY-404,HARBOR,INV-NOT-FOUND,50.00\n', 'payments')
        self.assertEqual(reporting.overview(self.db)['summary']['outstanding'], before)

    def test_payment_for_the_right_number_but_wrong_customer_is_unmatched(self):
        """Both customer_id and invoice_number must agree before attaching."""
        importing.import_csv(
            self.db, PAYMENT_HEADER + 'PAY-X,MAPLE,INV-100,5.00\n', 'payments')
        self.assertEqual(self.invoice('HARBOR', 'INV-100')['paid'], 0)
        unmatched = reporting.overview(self.db)['unmatched_payments']
        self.assertIn('PAY-X', [p['payment_id'] for p in unmatched])


class PartialImport(LedgerTestCase):
    """D5: one invalid data row aborted the whole file and wrote nothing."""

    MIXED = (INVOICE_HEADER
             + 'HARBOR,INV-103,84.00,2026-09-12\n'
             + 'NORTH,INV-302,not-a-number,2026-09-12\n'
             + 'MAPLE,INV-203,100.00,2026-09-13\n')

    def test_valid_rows_import_despite_one_invalid_row(self):
        result = importing.import_csv(self.db, self.MIXED, 'invoices')
        self.assertEqual(result['imported'], 2)
        self.assertEqual(result['rejected'], 1)

    def test_rejected_row_reports_its_csv_line_number(self):
        """The header is line 1, so the bad row is line 3."""
        result = importing.import_csv(self.db, self.MIXED, 'invoices')
        self.assertEqual([e['line'] for e in result['errors']], [3])
        self.assertTrue(result['errors'][0]['reason'].strip())

    def test_line_numbers_stay_correct_when_an_early_row_is_rejected(self):
        """Rejected rows must not shift the reported line numbers of later ones."""
        text = (INVOICE_HEADER
                + 'NORTH,BAD-1,not-a-number,2026-09-12\n'   # line 2
                + 'HARBOR,OK-1,10.00,2026-09-12\n'          # line 3
                + 'NORTH,BAD-2,-5.00,2026-09-12\n')         # line 4
        result = importing.import_csv(self.db, text, 'invoices')
        self.assertEqual(result['imported'], 1)
        self.assertEqual([e['line'] for e in result['errors']], [2, 4])

    def test_valid_header_with_no_data_rows_is_a_successful_empty_import(self):
        result = importing.import_csv(self.db, INVOICE_HEADER, 'invoices')
        self.assertEqual((result['imported'], result['skipped'], result['rejected']), (0, 0, 0))

    def test_invalid_header_rejects_the_whole_import_and_writes_nothing(self):
        before = len(reporting.invoices(self.db))
        with self.assertRaises(ValueError):
            importing.import_csv(self.db, 'customer,invoice,value\nHARBOR,INV-110,50.00\n',
                                 'invoices')
        self.assertEqual(len(reporting.invoices(self.db)), before)

    def test_all_rows_invalid_is_still_a_processed_import(self):
        result = importing.import_csv(
            self.db, INVOICE_HEADER + 'NOPE,INV-900,10.00,2026-09-12\n', 'invoices')
        self.assertEqual(result['rejected'], 1)
        self.assertEqual(result['imported'], 0)


class MoneyPrecision(LedgerTestCase):
    """Cents must survive reporting and export (BUSINESS_RULES: preserve cents)."""

    def test_export_agrees_with_the_screen_for_the_same_record(self):
        importing.import_csv(
            self.db, PAYMENT_HEADER + 'P-CENT,NORTH,INV-301,0.07\n', 'payments')
        row = self.invoice('NORTH', 'INV-301')
        line = next(line for line in reporting.export_csv(self.db).splitlines()
                    if line.startswith('NORTH,INV-301,'))
        self.assertEqual(line.split(',')[4], f"{row['balance']:.2f}")

    def test_overpayment_marks_the_invoice_paid_with_a_negative_balance(self):
        importing.import_csv(
            self.db, PAYMENT_HEADER + 'P-OVER,NORTH,INV-301,150.00\n', 'payments')
        row = self.invoice('NORTH', 'INV-301')
        self.assertEqual(row['balance'], -50.00)
        self.assertEqual(row['status'], 'paid')

    def test_overpayment_does_not_reduce_another_invoice_outstanding(self):
        other_before = self.invoice('HARBOR', 'INV-100')['balance']
        importing.import_csv(
            self.db, PAYMENT_HEADER + 'P-OVER,NORTH,INV-301,150.00\n', 'payments')
        self.assertEqual(self.invoice('HARBOR', 'INV-100')['balance'], other_before)

    def test_outstanding_excludes_overpaid_invoices(self):
        """Overview outstanding sums positive balances only."""
        importing.import_csv(
            self.db, PAYMENT_HEADER + 'P-OVER,NORTH,INV-301,150.00\n', 'payments')
        expected = sum(r['balance'] for r in reporting.invoices(self.db) if r['balance'] > 0)
        self.assertAlmostEqual(
            reporting.overview(self.db)['summary']['outstanding'], round(expected, 2), places=2)


class DuplicatePayment(LedgerTestCase):
    """Payment identity rules from BUSINESS_RULES.md."""

    ROW = 'PAY-1,HARBOR,INV-100,20.00\n'

    def test_identical_payment_reimport_is_skipped(self):
        importing.import_csv(self.db, PAYMENT_HEADER + self.ROW, 'payments')
        result = importing.import_csv(self.db, PAYMENT_HEADER + self.ROW, 'payments')
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(self.invoice('HARBOR', 'INV-100')['paid'], 20.00)

    def test_reused_payment_id_with_different_amount_is_rejected(self):
        importing.import_csv(self.db, PAYMENT_HEADER + self.ROW, 'payments')
        result = importing.import_csv(
            self.db, PAYMENT_HEADER + 'PAY-1,HARBOR,INV-100,99.00\n', 'payments')
        self.assertEqual(result['rejected'], 1)
        self.assertEqual(self.invoice('HARBOR', 'INV-100')['paid'], 20.00)


class InputHygiene(LedgerTestCase):
    """Trimming and validation rules that the repair must not regress."""

    def test_surrounding_whitespace_is_trimmed(self):
        result = importing.import_csv(
            self.db, INVOICE_HEADER + '  HARBOR , INV-777 , 12.50 , 2026-09-12 \n', 'invoices')
        self.assertEqual(result['imported'], 1)
        self.assertEqual(self.invoice('HARBOR', 'INV-777')['amount'], 12.50)

    def test_identifiers_stay_case_sensitive(self):
        result = importing.import_csv(
            self.db, INVOICE_HEADER + 'harbor,INV-778,12.50,2026-09-12\n', 'invoices')
        self.assertEqual(result['rejected'], 1, 'lowercase harbor is not a known customer')

    def test_three_decimal_amount_is_rejected(self):
        result = importing.import_csv(
            self.db, INVOICE_HEADER + 'HARBOR,INV-779,12.505,2026-09-12\n', 'invoices')
        self.assertEqual(result['rejected'], 1)

    def test_impossible_calendar_date_is_rejected(self):
        result = importing.import_csv(
            self.db, INVOICE_HEADER + 'HARBOR,INV-780,12.50,2026-02-30\n', 'invoices')
        self.assertEqual(result['rejected'], 1)

    def test_byte_order_mark_is_accepted(self):
        result = importing.import_csv(
            self.db, '﻿' + INVOICE_HEADER + 'HARBOR,INV-781,12.50,2026-09-12\n', 'invoices')
        self.assertEqual(result['imported'], 1)


if __name__ == '__main__':
    unittest.main()
