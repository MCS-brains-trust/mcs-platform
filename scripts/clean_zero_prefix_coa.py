"""
clean_zero_prefix_coa.py

Removes all account entries whose code starts with '0' from the
company, partnership, and sole_trader entity types in all_accounts.json.

Trust accounts are intentionally left untouched because their 0xxx codes
have no non-zero counterparts and are legitimate unique accounts.

Usage:
    python3 scripts/clean_zero_prefix_coa.py [--apply]
"""

import json
import sys
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "all_accounts.json"
ENTITY_TYPES_TO_CLEAN = {"company", "partnership", "sole_trader"}


def main():
    apply = "--apply" in sys.argv

    with open(DATA_FILE) as f:
        data = json.load(f)

    summary = {}
    for entity_type, accounts in data.items():
        if entity_type not in ENTITY_TYPES_TO_CLEAN:
            print(f"[SKIP] {entity_type}: not in clean list, leaving untouched.")
            continue

        original_count = len(accounts)
        to_remove = [a for a in accounts if str(a["account_code"]).startswith("0")]
        cleaned = [a for a in accounts if not str(a["account_code"]).startswith("0")]

        print(f"\n[{entity_type.upper()}] {original_count} accounts → {len(cleaned)} after removal")
        print(f"  Removing {len(to_remove)} accounts:")
        for a in to_remove:
            print(f"    {a['account_code']}  {a['account_name']}")

        summary[entity_type] = {"removed": len(to_remove), "remaining": len(cleaned)}

        if apply:
            data[entity_type] = cleaned

    print("\n" + "=" * 60)
    print(f"  Mode: {'APPLYING' if apply else 'DRY RUN'}")
    for et, s in summary.items():
        print(f"  {et}: removed {s['removed']}, remaining {s['remaining']}")
    print("=" * 60)

    if apply:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2)
        print("\nall_accounts.json updated successfully.")
    else:
        print("\nDry run complete. Run with --apply to save changes.")


if __name__ == "__main__":
    main()
