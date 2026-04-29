"""
Trust COA mop-up — 2026-04-29 follow-up to commits 292f2ba + ad821bb.

Four tasks, each wrapped in transaction.atomic():
  A — Scarton: delete 13 leaked-code EntityChartOfAccount rows (with escalation gate)
  B — Liebac / Cleary: rename account 2000 from "Cash at bank - CBA" → "Cash at bank"
  C — Template hygiene: rename vehicle-specific names in trust ChartOfAccount and propagate
  D — Verify: run check_template_hygiene() and assert zero suspects

Plus a Chiaravalle duplication investigation (read-only, no DB writes).

Usage:
    python3 manage.py trust_coa_mopup
    python3 manage.py trust_coa_mopup --dry-run
"""
import re

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import (
    AdjustingJournal,
    ChartOfAccount,
    Entity,
    EntityChartOfAccount,
    FinancialYear,
    JournalLine,
    SUSPICIOUS_NAME_REGEX,
    TrialBalanceLine,
)

SCARTON_CODES = [
    '2000', '2001', '2141', '2476', '3104', '3352',
    '3501', '3502', '3503', '3504', '3628', '3629', '3630',
]

# Pattern that isolates the vehicle make+model suffix from the account name so
# we can strip it and derive the prefix family for sequential numbering.
_VEHICLE_SUFFIX_RE = re.compile(
    r'\s*-\s*(?:toyota|ford|holden|mazda|hyundai|kia)\s+(?:hilux|ranger|colorado|navara|amarok)',
    re.IGNORECASE,
)


def _vehicle_generic_name(old_name: str, already_renamed: dict) -> str:
    """
    Strip the vehicle make+model suffix, then assign the next sequential
    Vehicle N number within the same prefix family.

    already_renamed: {prefix_lower: count_so_far} — mutated in place.
    """
    prefix = _VEHICLE_SUFFIX_RE.split(old_name)[0].strip()
    key = prefix.lower()
    n = already_renamed.get(key, 0) + 1
    already_renamed[key] = n
    return f"{prefix} - Vehicle {n}"


class Command(BaseCommand):
    help = "Trust COA mop-up: Scarton delete, Liebac/Cleary rename, vehicle template hygiene"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview all changes without writing to the database",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        label = "[DRY RUN] " if dry else ""
        out = self.stdout.write
        err = self.stderr.write

        out(f"\n{'=' * 66}")
        out(f"Trust COA mop-up  {label}")
        out(f"{'=' * 66}\n")

        # Counters that feed the final VERDICT block
        a_deleted = 0
        a_escalated = []
        b_liebac = 0
        b_cleary = 0
        c_hits = 0
        c_tpl_renamed = 0
        c_eca_renamed = 0
        d_result = {}

        # ------------------------------------------------------------------ #
        # TASK A — Scarton deletions                                          #
        # ------------------------------------------------------------------ #
        out("TASK A — Scarton deletions")
        out("-" * 40)

        scarton_entities = list(
            Entity.objects.filter(entity_name__icontains='Scarton')
        )
        if not scarton_entities:
            err(self.style.ERROR("  No entity matching 'Scarton' found — aborting Task A"))
        else:
            out(f"  Scarton entities ({len(scarton_entities)}): "
                f"{[e.entity_name for e in scarton_entities]}")

            scarton_fy_ids = list(
                FinancialYear.objects.filter(entity__in=scarton_entities)
                .values_list('pk', flat=True)
            )
            out(f"  FinancialYear IDs: {len(scarton_fy_ids)} years")

            rows_to_check = EntityChartOfAccount.objects.filter(
                entity__in=scarton_entities,
                account_code__in=SCARTON_CODES,
            )
            out(f"  ECA rows found for these codes: {rows_to_check.count()}")

            with transaction.atomic():
                for row in rows_to_check.select_related('entity'):
                    reasons = []

                    if row.is_custom:
                        reasons.append("is_custom=True")

                    tb_refs = TrialBalanceLine.objects.filter(
                        financial_year_id__in=scarton_fy_ids,
                        account_code=row.account_code,
                    ).count()
                    if tb_refs:
                        reasons.append(f"TB_refs={tb_refs}")

                    jl_refs = JournalLine.objects.filter(
                        journal__financial_year_id__in=scarton_fy_ids,
                        account_code=row.account_code,
                    ).count()
                    if jl_refs:
                        reasons.append(f"JL_refs={jl_refs}")

                    if reasons:
                        a_escalated.append({
                            'code': row.account_code,
                            'name': row.account_name,
                            'entity': row.entity.entity_name,
                            'reasons': reasons,
                        })
                        out(self.style.WARNING(
                            f"  ESCALATE  {row.account_code} ({row.account_name}) "
                            f"on {row.entity.entity_name} — {reasons}"
                        ))
                    else:
                        out(f"  {'WOULD DELETE' if dry else 'DELETE'}  "
                            f"{row.account_code} ({row.account_name})")
                        if not dry:
                            row.delete()
                        a_deleted += 1

                if dry:
                    transaction.set_rollback(True)

        out(f"\n  Task A result: {a_deleted} deleted, {len(a_escalated)} escalated\n")

        # ------------------------------------------------------------------ #
        # TASK B — Liebac and Cleary rename                                   #
        # ------------------------------------------------------------------ #
        out("TASK B — Liebac / Cleary rename (2000 → 'Cash at bank')")
        out("-" * 40)

        liebac_qs = EntityChartOfAccount.objects.filter(
            entity__entity_name__icontains='Liebac',
            account_code='2000',
        )
        cleary_qs = EntityChartOfAccount.objects.filter(
            entity__entity_name__icontains='Cleary',
            account_code='2000',
        )
        out(f"  Liebac rows eligible: {liebac_qs.count()}")
        out(f"  Cleary rows eligible: {cleary_qs.count()}")

        with transaction.atomic():
            if not dry:
                b_liebac = liebac_qs.update(account_name='Cash at bank')
                b_cleary = cleary_qs.update(account_name='Cash at bank')
            else:
                b_liebac = liebac_qs.count()
                b_cleary = cleary_qs.count()
            if dry:
                transaction.set_rollback(True)

        out(f"  Liebac renamed: {b_liebac}")
        out(f"  Cleary renamed: {b_cleary}\n")

        # ------------------------------------------------------------------ #
        # TASK C — Template hygiene sweep                                     #
        # ------------------------------------------------------------------ #
        out("TASK C — Template hygiene sweep (trust ChartOfAccount)")
        out("-" * 40)

        trust_coas = ChartOfAccount.objects.filter(entity_type='trust')
        hits = [coa for coa in trust_coas if SUSPICIOUS_NAME_REGEX.search(coa.account_name)]
        c_hits = len(hits)
        out(f"  Suspicious trust template rows: {c_hits}")
        for h in hits:
            out(f"    {h.account_code} | {h.account_name}")

        if hits:
            already_renamed: dict = {}

            with transaction.atomic():
                for coa in hits:
                    old_name = coa.account_name
                    new_name = _vehicle_generic_name(old_name, already_renamed)

                    out(f"  {'WOULD RENAME' if dry else 'RENAME'}  template "
                        f"{coa.account_code}: '{old_name}' → '{new_name}'")

                    if not dry:
                        ChartOfAccount.objects.filter(pk=coa.pk).update(account_name=new_name)
                    c_tpl_renamed += 1

                    # Propagate to per-entity rows seeded from template
                    eca_qs = EntityChartOfAccount.objects.filter(
                        account_code=coa.account_code,
                        account_name=old_name,
                        is_custom=False,
                    )
                    eca_count = eca_qs.count()
                    out(f"    → propagating to {eca_count} ECA rows (is_custom=False)")
                    if not dry:
                        eca_qs.update(account_name=new_name)
                    c_eca_renamed += eca_count

                if dry:
                    transaction.set_rollback(True)

        out(f"\n  Task C result: {c_hits} hits, "
            f"{c_tpl_renamed} template renamed, {c_eca_renamed} ECA rows renamed\n")

        # ------------------------------------------------------------------ #
        # TASK D — Verify                                                     #
        # ------------------------------------------------------------------ #
        out("TASK D — Verify (check_template_hygiene)")
        out("-" * 40)

        if dry:
            out("  [DRY RUN] Skipping verification (would read post-rollback state)")
            d_result = {"template_suspect": "N/A (dry run)", "eca_suspect": "N/A (dry run)"}
        else:
            from core.tasks import check_template_hygiene
            d_result = check_template_hygiene()
            ts = d_result["template_suspect"]
            es = d_result["eca_suspect"]
            out(f"  template_suspect={ts}  eca_suspect={es}")
            if ts or es:
                out(self.style.WARNING("  Remaining hits:"))
                for coa in ChartOfAccount.objects.all():
                    if SUSPICIOUS_NAME_REGEX.search(coa.account_name):
                        out(self.style.WARNING(
                            f"    TEMPLATE: {coa.entity_type} | {coa.account_code} | {coa.account_name}"
                        ))
                for eca in EntityChartOfAccount.objects.filter(is_custom=False):
                    if SUSPICIOUS_NAME_REGEX.search(eca.account_name):
                        out(self.style.WARNING(
                            f"    ECA: entity={eca.entity_id} | {eca.account_code} | {eca.account_name}"
                        ))
            else:
                out(self.style.SUCCESS("  Clean — zero suspects."))

        # ------------------------------------------------------------------ #
        # CHIARAVALLE INVESTIGATION (read-only)                               #
        # ------------------------------------------------------------------ #
        out("\n" + "=" * 66)
        out("CHIARAVALLE INVESTIGATION")
        out("-" * 40)

        chiaravalle = Entity.objects.filter(entity_name__icontains='Chiaravalle')
        if not chiaravalle.exists():
            out("  No Chiaravalle entities found.")
        else:
            records = []
            for e in chiaravalle:
                fy_count = FinancialYear.objects.filter(entity=e).count()
                tb_count = TrialBalanceLine.objects.filter(
                    financial_year__entity=e
                ).count()
                jl_count = JournalLine.objects.filter(
                    journal__financial_year__entity=e
                ).count()
                aj_count = AdjustingJournal.objects.filter(
                    financial_year__entity=e
                ).count()
                posting_count = tb_count + jl_count + aj_count
                # Entity has no created_by field; primary_accountant is the closest proxy
                accountant = (
                    e.primary_accountant.username if e.primary_accountant else "N/A"
                )
                rec = {
                    "name": e.entity_name,
                    "pk": str(e.pk),
                    "created_at": str(e.created_at),
                    "abn": e.abn or "N/A",
                    "primary_accountant": accountant,
                    "fy_count": fy_count,
                    "posting_count": posting_count,
                    "tb": tb_count,
                    "jl": jl_count,
                    "aj": aj_count,
                }
                records.append(rec)
                out(f"\n  {e.entity_name}")
                out(f"    pk              = {rec['pk']}")
                out(f"    created_at      = {rec['created_at']}")
                out(f"    abn             = {rec['abn']}")
                out(f"    primary_acct    = {rec['primary_accountant']}")
                out(f"    FinancialYears  = {fy_count}")
                out(f"    Postings        = {posting_count} "
                    f"(TB={tb_count}, JL={jl_count}, AJ={aj_count})")

            # Heuristic verdict
            if len(records) == 2:
                r0, r1 = records
                same_abn = r0["abn"] == r1["abn"] and r0["abn"] != "N/A"
                both_empty = r0["posting_count"] == 0 and r1["posting_count"] == 0
                if same_abn:
                    chiaravalle_verdict = "duplicates"
                    chiaravalle_summary = (
                        f"Same ABN ({r0['abn']}) on both — almost certainly the same trust seeded twice."
                    )
                elif both_empty and r0["fy_count"] == 0 and r1["fy_count"] == 0:
                    chiaravalle_verdict = "duplicates"
                    chiaravalle_summary = (
                        "Both entities are empty (0 FYs, 0 postings) — likely a double-create during reseed."
                    )
                elif r0["abn"] != r1["abn"] and r0["abn"] != "N/A" and r1["abn"] != "N/A":
                    chiaravalle_verdict = "separate"
                    chiaravalle_summary = (
                        f"Different ABNs ({r0['abn']} vs {r1['abn']}) — two distinct legal trusts."
                    )
                else:
                    chiaravalle_verdict = "unclear"
                    chiaravalle_summary = (
                        "ABN data incomplete; manual review of trust deed names required."
                    )
            else:
                chiaravalle_verdict = "unclear"
                chiaravalle_summary = f"Found {len(records)} entities (expected 2); investigate manually."

            out(f"\n  VERDICT: {chiaravalle_verdict} — {chiaravalle_summary}")

        # ------------------------------------------------------------------ #
        # FINAL VERDICT BLOCK                                                 #
        # ------------------------------------------------------------------ #
        ts_val = d_result.get('template_suspect', 'N/A')
        es_val = d_result.get('eca_suspect', 'N/A')
        mopup_status = "blocked (dry-run)" if dry else (
            "complete" if (ts_val == 0 and es_val == 0) else "partial"
        )

        out(f"\n{'=' * 66}")
        out("VERDICT BLOCK")
        out(f"{'=' * 66}")
        out(f"VERDICT: mop-up {mopup_status}")
        out(f"")
        out(f"Task A — Scarton: {a_deleted} rows deleted, {len(a_escalated)} escalated")
        if a_escalated:
            for esc in a_escalated:
                out(f"  ESCALATED: code={esc['code']} name={esc['name']} "
                    f"entity={esc['entity']} reasons={esc['reasons']}")
        out(f"Task B — Rename: {b_liebac} rows renamed (Liebac), {b_cleary} rows renamed (Cleary)")
        out(f"Task C — Template sweep: {c_hits} hits found, "
            f"{c_tpl_renamed} renamed in template, {c_eca_renamed} renamed in ECAs")
        out(f"Task D — Hygiene: template_suspect={ts_val}, eca_suspect={es_val}")
        out(f"")
        if chiaravalle.exists():
            out(f"Chiaravalle: {chiaravalle_verdict} — {chiaravalle_summary}")
        else:
            out("Chiaravalle: no entities found")
