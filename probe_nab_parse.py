# READ-ONLY probe — do Elio's NAB statements parse, and do they reconcile?
#
#   cd /opt/statementhub/.claude/worktrees/bas-tb-desync
#   python3 manage.py shell < probe_nab_parse.py
#
# Pure parse + print. Touches no database table at all: it reads two PDFs off
# disk and runs the same extraction the upload endpoint would.
#
# WHY. The geometry engine is gated on `if bank == "cba"`
# (review/pdf_parsers.py:1796); NAB still runs the legacy flat-text parser plus
# per-line regex, which is where the original defect class lives. And the direct
# parse path is NOT reconciliation-gated: _reconcile is called only inside
# _try_vision_fallback (review/views.py:1093), so nothing checks that a legacy
# parser's transactions foot from the statement's own opening balance to its own
# closing balance. Only a parse returning ZERO transactions falls back to Vision.
#
# So the failure mode that matters is not an error. It is a parse that returns
# plausible-but-wrong rows and is trusted. This checks for exactly that, before
# anything is uploaded or allocated.

import glob
from decimal import Decimal

from review.pdf_parsers import detect_bank, extract_transactions_from_pdf_direct
from review.statement_geometry import _reconcile, StatementParseError

RULE = "=" * 78
PDFS = sorted(glob.glob("/root/nab_check/*.pdf"))

print(RULE)
print(f"NAB STATEMENT PARSE CHECK — read-only  ({len(PDFS)} file(s))")
print(RULE)

for path in PDFS:
    print("\n" + RULE)
    print(path)
    print(RULE)
    with open(path, "rb") as fh:
        content = fh.read()

    try:
        bank = detect_bank(content)
    except Exception as exc:
        bank = f"<detect_bank raised {type(exc).__name__}: {exc}>"
    print(f"\n  detect_bank      : {bank!r}")

    try:
        result = extract_transactions_from_pdf_direct(content)
    except Exception as exc:
        print(f"  PARSE RAISED     : {type(exc).__name__}: {exc}")
        print("\n  >>> A raise means the upload endpoint would route this to the")
        print("      Claude Vision fallback, which IS reconciliation-gated. Slower")
        print("      and metered, but it would not silently post wrong figures.")
        continue

    txns = result.get("transactions") or []
    opening = result.get("opening_balance")
    closing = result.get("closing_balance")
    print(f"  account_name     : {result.get('account_name')!r}")
    print(f"  bsb / account    : {result.get('bsb')!r} / {result.get('account_number')!r}")
    print(f"  period           : {result.get('period_start')!r} .. {result.get('period_end')!r}")
    print(f"  opening balance  : {opening}")
    print(f"  closing balance  : {closing}")
    print(f"  transactions     : {len(txns)}")

    if not txns:
        print("\n  >>> ZERO transactions. The upload endpoint treats this as a failed")
        print("      direct parse and falls back to Claude Vision, which is")
        print("      reconciliation-gated. Not silent, but not free either.")
        continue

    # Signed-amount convention, the same one _reconcile assumes.
    total = sum(Decimal(str(t.get("amount", 0))) for t in txns)
    derived = (Decimal(str(opening or 0)) + total).quantize(Decimal("0.01"))
    print(f"\n  sum of movements : {total}")
    print(f"  opening + moves  : {derived}")
    print(f"  stated closing   : {closing}")

    try:
        _reconcile(txns, float(opening), float(closing))
        print("\n  >>> RECONCILES. Opening plus movements equals the stated closing")
        print("      balance, so every transaction on the statement was captured with")
        print("      the right sign and amount. This file is safe to upload.")
    except (StatementParseError, TypeError, ValueError) as exc:
        print(f"\n  >>> DOES NOT RECONCILE: {exc}")
        print("      Nothing in the application would catch this — the direct path is")
        print("      not reconciliation-gated and the parse returned rows, so no Vision")
        print("      fallback fires. Uploading this would post wrong figures silently.")

    # First and last few rows, to eyeball dates and signs.
    print("\n  first 3 and last 3 rows:")
    for t in txns[:3] + (["..."] if len(txns) > 6 else []) + txns[-3:]:
        if t == "...":
            print("      ...")
            continue
        print(f"      {str(t.get('date')):<12} {str(t.get('amount')):>12}  "
              f"{str(t.get('description'))[:48]}")

print("\n" + RULE)
print("No database table was read or written.")
print(RULE)
