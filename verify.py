"""End-to-end verification against a running server, over real HTTP.

The unit suite covers the modules directly. This checks the same behaviour
through the public API a reviewer actually uses, on a working copy of the
owner's existing register, and proves the records survive a restart.

    python3 restore_fixture.py --replace
    python3 app.py --port 8788          # in another terminal
    python3 verify.py --port 8788

Exits 0 if every check passes, 1 otherwise. Writes nothing outside .local/.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXPECTED = json.loads((ROOT / 'fixtures' / 'expected-records.json').read_text())

PASS, FAIL = [], []


def check(label, got, want):
    if got == want:
        PASS.append(label)
        print(f'  PASS  {label}')
    else:
        FAIL.append(label)
        print(f'  FAIL  {label}\n          expected: {want!r}\n          actual:   {got!r}')


def api(base, path, body=None, method=None):
    url = f'{base}{path}'
    data = body.encode('utf-8') if body is not None else None
    request = urllib.request.Request(
        url, data=data, method=method or ('POST' if data is not None else 'GET'),
        headers={'Content-Type': 'text/csv'} if data is not None else {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode('utf-8')
            return response.status, raw
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode('utf-8')


def money(value):
    return f'{value:.2f}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8787)
    args = parser.parse_args()
    base = f'http://127.0.0.1:{args.port}'

    try:
        api(base, '/api/overview')
    except OSError:
        print(f'No server on {base}. Start it with: python3 app.py --port {args.port}')
        return 2

    print(f'\nVerifying {base} against fixtures/expected-records.json\n')

    # --- the owner's register is intact ------------------------------
    print('Existing register')
    status, raw = api(base, '/api/overview')
    overview = json.loads(raw)
    check('GET /api/overview returns 200', status, 200)
    check('invoice_count', overview['summary']['invoice_count'],
          EXPECTED['summary']['invoice_count'])
    check('open_count', overview['summary']['open_count'], EXPECTED['summary']['open_count'])
    check('outstanding', money(overview['summary']['outstanding']),
          EXPECTED['summary']['outstanding'])
    check('unmatched payment KEEP-U1 is retained',
          [p['payment_id'] for p in overview['unmatched_payments']], ['KEEP-U1'])

    by_key = {(r['customer_id'], r['invoice_number']): r for r in overview['invoices']}
    for want in EXPECTED['invoices']:
        row = by_key.get((want['customer_id'], want['invoice_number']))
        check(f"invoice {want['customer_id']}/{want['invoice_number']} amount",
              money(row['amount']) if row else None, want['amount'])
    check('KEEP-700 paid allocation preserved',
          money(by_key[('HARBOR', 'KEEP-700')]['paid']), '56.78')

    # --- D1: the open filter agrees with the overview ----------------
    print('\nD1 open/paid filter')
    _, raw = api(base, '/api/invoices?status=open')
    open_rows = json.loads(raw)
    check('every row in ?status=open is open',
          sorted({r['status'] for r in open_rows}), ['open'])
    check('open rows match summary.open_count', len(open_rows),
          overview['summary']['open_count'])
    _, raw = api(base, '/api/invoices?status=paid')
    paid_rows = json.loads(raw)
    check('every row in ?status=paid is paid',
          sorted({r['status'] for r in paid_rows}) or ['paid'], ['paid'])
    check('all == open + paid', len(overview['invoices']), len(open_rows) + len(paid_rows))
    status, _ = api(base, '/api/invoices?status=bogus')
    check('invalid status returns 400', status, 400)

    # --- D5: a bad row rejects only itself ---------------------------
    print('\nD5 partial import')
    mixed = (ROOT / 'samples' / 'invoices-mixed.csv').read_text()
    status, raw = api(base, '/api/import?kind=invoices&dry_run=1', mixed)
    preview = json.loads(raw)
    check('dry run returns 200', status, 200)
    check('dry run reports 2 imported, 1 rejected',
          (preview['imported'], preview['rejected']), (2, 1))
    check('dry run reports the bad line number', [e['line'] for e in preview['errors']], [3])

    # --- improvement: the preview changed nothing --------------------
    print('\nImprovement: import preview')
    _, raw = api(base, '/api/overview')
    check('register unchanged after the dry run',
          json.loads(raw)['summary'], overview['summary'])

    status, raw = api(base, '/api/import?kind=invoices', mixed)
    real = json.loads(raw)
    check('real import returns 200', status, 200)
    check('real import matches what the preview predicted',
          (real['imported'], real['skipped'], real['rejected'], real['errors']),
          (preview['imported'], preview['skipped'], preview['rejected'], preview['errors']))

    # --- D2: retrying the same file moves nothing --------------------
    print('\nD2 duplicate import')
    _, raw = api(base, '/api/overview')
    after_first = json.loads(raw)['summary']
    _, raw = api(base, '/api/import?kind=invoices', mixed)
    retry = json.loads(raw)
    check('retry skips both rows', (retry['imported'], retry['skipped']), (0, 2))
    _, raw = api(base, '/api/overview')
    check('outstanding is unchanged by the retry',
          json.loads(raw)['summary'], after_first)

    # --- D3: payments match on identity, not amount ------------------
    print('\nD3 payment matching')
    payments = (ROOT / 'samples' / 'payments.csv').read_text()
    api(base, '/api/import?kind=payments', payments)
    _, raw = api(base, '/api/overview')
    after = json.loads(raw)
    rows = {(r['customer_id'], r['invoice_number']): r for r in after['invoices']}
    check('PAY-201 paid MAPLE/INV-200', money(rows[('MAPLE', 'INV-200')]['paid']), '1250.00')
    check('HARBOR/INV-100 was not touched', money(rows[('HARBOR', 'INV-100')]['paid']), '0.00')
    check('PAY-404 is retained as unmatched',
          'PAY-404' in [p['payment_id'] for p in after['unmatched_payments']], True)

    # --- D4: the export agrees with the screen -----------------------
    print('\nD4 export precision')
    _, csv_text = api(base, '/api/export')
    lines = csv_text.strip().splitlines()
    check('export header', lines[0],
          'customer_id,invoice_number,amount,paid,balance,status')
    exported = {(p[0], p[1]): p for p in (line.split(',') for line in lines[1:])}
    mismatched = [key for key, row in exported.items()
                  if key in rows and (row[2] != money(rows[key]['amount'])
                                      or row[3] != money(rows[key]['paid'])
                                      or row[4] != money(rows[key]['balance'])
                                      or row[5] != rows[key]['status'])]
    check('every exported row matches the screen', mismatched, [])
    check('export covers every invoice', len(exported), len(after['invoices']))

    # --- restart: everything survives --------------------------------
    print('\nRestart')
    before_restart = after['summary']
    print('  restarting the server on a spare port...')
    process = subprocess.Popen([sys.executable, 'app.py', '--port', str(args.port + 41)],
                               cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(2)
        _, raw = api(f'http://127.0.0.1:{args.port + 41}', '/api/overview')
        restarted = json.loads(raw)
        check('summary is identical after a restart', restarted['summary'], before_restart)
        restarted_rows = {(r['customer_id'], r['invoice_number']): r
                          for r in restarted['invoices']}
        check('the original KEEP-700 allocation survived',
              money(restarted_rows[('HARBOR', 'KEEP-700')]['paid']), '56.78')
        check('the newly imported INV-103 survived',
              ('HARBOR', 'INV-103') in restarted_rows, True)
        check('KEEP-U1 is still unmatched',
              'KEEP-U1' in [p['payment_id'] for p in restarted['unmatched_payments']], True)
    finally:
        process.terminate()
        process.wait(timeout=5)

    print(f'\n{len(PASS)} passed, {len(FAIL)} failed')
    if FAIL:
        print('\nFailed checks:')
        for label in FAIL:
            print(f'  - {label}')
        return 1
    print('All end-to-end checks passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
