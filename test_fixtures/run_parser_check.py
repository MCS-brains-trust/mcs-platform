#!/usr/bin/env python3
"""
Phase D test gate: parse CBA fixtures via the geometry engine and verify
they reconcile to the cent against the known footer totals.

Run from the repo root:
    python test_fixtures/run_parser_check.py

Both fixtures must PASS before committing.
"""
import sys
import os
import io
import re

# Repo root on path (two levels up from this file)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Minimal Django bootstrap (needed for the review package import chain)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
try:
    import django
    django.setup()
except Exception as e:
    print(f"WARNING: Django setup failed ({e}) — continuing anyway")

from review.statement_geometry import (
    parse_cba_geometry,
    StatementParseError,
    _money_columns,
    _rows,
    MOVE_RE,
)

FIXTURES = [
    {
        'name': 'cba_stmt9',
        'path': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cba_stmt9.pdf'),
        'opening':        27440.30,
        'total_debits':  206501.18,
        'total_credits': 187887.10,
        'closing':         8826.22,
    },
    {
        'name': 'cba_stmt10',
        'path': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cba_stmt10.pdf'),
        'opening':         8826.22,
        'total_debits':  174130.93,
        'total_credits': 191725.24,
        'closing':        26420.53,
    },
]

TOL = 0.01
all_pass = True


def _check(label, got, expected, tol=TOL):
    delta = round(got - expected, 2)
    ok = abs(delta) <= tol
    status = 'OK' if ok else f'FAIL (delta {delta:+.2f})'
    print(f'  {label:<18} {got:>12.2f}  expected {expected:>12.2f}  {status}')
    return ok


for fx in FIXTURES:
    print(f"\n{'='*65}")
    print(f"Fixture: {fx['name']}")
    print(f"  Path: {fx['path']}")

    if not os.path.exists(fx['path']):
        print(f"  FAIL — file not found: {fx['path']}")
        all_pass = False
        continue

    with open(fx['path'], 'rb') as fh:
        content = fh.read()

    try:
        result = parse_cba_geometry(content)
    except StatementParseError as exc:
        print(f"  FAIL — StatementParseError: {exc}")
        all_pass = False
        # Dump diagnostic info
        import pdfplumber
        print("\n  Diagnostic — bare-amount tokens with x1:")
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            rows_data = _rows(pdf)
        cols = _money_columns(rows_data)
        print(f"  Detected column centres: {cols}")
        for row in rows_data:
            for w in row:
                if MOVE_RE.match(w['text']):
                    print(f"    '{w['text']:>12}'  x1={w['x1']:.1f}")
        continue
    except Exception as exc:
        print(f"  FAIL — Unexpected error: {type(exc).__name__}: {exc}")
        all_pass = False
        continue

    txns = result['transactions']
    opening = result['opening_balance']
    closing = result['closing_balance']
    total_debits = sum(abs(t['amount']) for t in txns if t['amount'] < 0)
    total_credits = sum(t['amount'] for t in txns if t['amount'] > 0)
    derived = round(opening + sum(t['amount'] for t in txns), 2)

    print(f"  Transactions: {len(txns)}")
    ok_open    = _check('opening_balance', opening,       fx['opening'])
    ok_debits  = _check('total_debits',    total_debits,  fx['total_debits'])
    ok_credits = _check('total_credits',   total_credits, fx['total_credits'])
    ok_close   = _check('closing_balance', closing,       fx['closing'])
    ok_recon   = _check('recon check',     derived,       closing)

    fixture_pass = all([ok_open, ok_debits, ok_credits, ok_close, ok_recon])

    # Assert no transaction description contains leaked page furniture.
    _COL_HEADER_FLAT = 'DateTransactionDebitCreditBalance'
    desc_clean = True
    for t in txns:
        d = t['description']
        d_flat = d.replace(' ', '')
        if re.search(r'Page\d*of', d_flat) or _COL_HEADER_FLAT in d_flat:
            print(f"  FAIL — furniture in description: {d[:100]!r}")
            desc_clean = False
    if desc_clean:
        print('  desc_furniture_check: OK')
    else:
        all_pass = False

    if not fixture_pass:
        all_pass = False
        print(f"\n  FAIL — {fx['name']}")
        # Dump bare-amount tokens for column diagnosis
        import pdfplumber
        print("\n  Diagnostic — bare-amount tokens with x1:")
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            rows_data = _rows(pdf)
        cols = _money_columns(rows_data)
        print(f"  Detected column centres: {cols}")
        for row in rows_data:
            for w in row:
                if MOVE_RE.match(w['text']):
                    print(f"    '{w['text']:>12}'  x1={w['x1']:.1f}")
    else:
        print(f"\n  PASS — {fx['name']}")

print(f"\n{'='*65}")
print(f"Overall: {'PASS' if all_pass else 'FAIL'}")
sys.exit(0 if all_pass else 1)
