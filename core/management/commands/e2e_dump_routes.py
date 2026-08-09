"""
Generate the Tier 1 route manifest for the Playwright crawl.

    python3 manage.py e2e_dump_routes --settings=config.settings_e2e

Walks Django's URL resolver, resolves path parameters against real rows in the E2E
database, and classifies every route as crawlable, excluded or unresolved. The
manifest is committed to the repository so that adding a route to the application
shows up as a manifest diff — that is the mechanism that keeps Tier 1 coverage
honest over time instead of decaying silently.

Nothing here is a "best effort" skip. Every route the crawler will not visit is
recorded with a machine-readable reason, and the summary prints the counts, so the
suite can never imply coverage it does not have.
"""

import json
import re
from pathlib import Path

from django.core.management.base import BaseCommand
from django.urls import URLPattern, URLResolver, get_resolver

from core.e2e_support import assert_e2e_database

# Why a route is not requested by the crawler, in precedence order.
#
# The database is a disposable per-file branch, so a view that writes to it on GET
# is not itself a reason to stay away — the copy is thrown away either way. What
# actually has to be avoided is narrower and falls into three groups:
#
#   external   the effect leaves this machine and cannot be undone by dropping a
#              database: email, e-signature envelopes, provider OAuth handshakes.
#   metered    the call costs money per invocation (Anthropic, Textract). These are
#              exercised deliberately in Tier 2 rather than 200+ times in a crawl.
#   mutating   changes state in a way that would make later assertions in the same
#              file order-dependent. Tier 2 covers these through the UI on purpose.
#
# Method guards are not relied on for safety. The crawler tolerates 405 as a normal
# outcome, which covers anything decorated with require_POST, but a view that is not
# method-guarded would act on a GET regardless — so the categories below are static
# and the manifest is committed, making a newly added unguarded route visible in a
# diff rather than discovered by running it.
#
# Deliberately NOT excluded: create/edit/form/wizard routes. A GET on those renders
# a form, which is exactly the kind of page worth smoke testing, and any stray write
# lands in a throwaway branch.

EXCLUSION_RULES = [
    ("external", [
        r"send", r"email", r"resend", r"fusesign", r"sign_",
        r"_sign$", r"connect", r"disconnect", r"sync", r"oauth",
        r"webhook", r"callback", r"invite",
    ]),
    ("metered", [
        r"^ai_", r"_ai$", r"^eva_", r"_eva$", r"generate", r"draft",
        r"classify", r"analyse", r"analyze", r"commentary", r"ocr", r"extract",
    ]),
    ("mutating", [
        r"delete", r"remove", r"purge", r"archive", r"revoke", r"reset",
        r"post_to", r"submit", r"approve", r"finalise", r"finalize",
        r"reopen", r"lock", r"unlock", r"roll_forward", r"reroll",
        r"import", r"upload", r"assemble", r"bulk_", r"accept_all",
        r"dismiss", r"suppress", r"toggle", r"reallocate", r"logout",
    ]),
]

# Routes excluded for reasons other than destructiveness.
INFRASTRUCTURE_NAME_PATTERNS = [
    r"^admin", r"protected_media", r"^api_", r"_api$", r"_poll$", r"htmx_",
]

# Django admin is excluded by path rather than by name: its generated route names
# are model-derived (accounts_user_change, auth_group_history) and match no useful
# name pattern. The admin is third-party code and not what this suite regression
# tests; the custom TOTP-gated login that fronts it is covered separately.
INFRASTRUCTURE_PATH_PREFIXES = ("/admin/",)

# Path segment immediately preceding a parameter to the model that parameter
# identifies. This is checked before the URL-name heuristic because it is a far
# stronger signal: "years/<uuid:pk>/statements/" says FinancialYear unambiguously,
# whereas the name "financial_statements_view" does not.
PATH_SEGMENT_MODEL_MAP = {
    "years": ("core", "FinancialYear"),
    "financial-years": ("core", "FinancialYear"),
    "financial-year": ("core", "FinancialYear"),
    "entities": ("core", "Entity"),
    "entity": ("core", "Entity"),
    "clients": ("core", "Client"),
    "client": ("core", "Client"),
    "transaction": ("review", "PendingTransaction"),
    "review": ("review", "ReviewJob"),
    "eva-findings": ("core", "EvaFinding"),
    "eva-reviews": ("core", "EvaReview"),
    "risk-flags": ("core", "RiskFlag"),
    "rules": ("core", "RiskRule"),
    "fs": ("core", "FinancialStatementTemplate"),
    "governing-docs": ("core", "GoverningDocument"),
    "associates": ("core", "ClientAssociate"),
    "chart-of-accounts": ("core", "ChartOfAccount"),
    "correspondence": ("core", "Correspondence"),
    "compliance": ("core", "Div7ACompliance"),
    "engagement-letters": ("core", "EngagementLetter"),
    "fte": ("core", "FamilyTrustElectionDocument"),
    "documents": ("core", "LegalDocument"),
    "tb-line": ("core", "TrialBalanceLine"),
    "software": ("core", "AccountingSoftware"),
    "journals": ("core", "AdjustingJournal"),
    "officers": ("core", "EntityOfficer"),
    "assets": ("core", "DepreciationAsset"),
    "templates": ("core", "DocumentTemplate"),
    "notes": ("core", "MeetingNote"),
}

# Explicit parameter name to model mapping. Applied before the URL-name heuristic.
PARAM_MODEL_MAP = {
    "entity_pk": ("core", "Entity"),
    "entity_id": ("core", "Entity"),
    "fy_pk": ("core", "FinancialYear"),
    "officer_pk": ("core", "EntityOfficer"),
    "activity_pk": ("core", "ActivityLog"),
    "asset_pk": ("core", "DepreciationAsset"),
    "scenario_pk": ("core", "TaxPlanningScenario"),
    "letter_pk": ("core", "EngagementLetter"),
    "template_pk": ("core", "DocumentTemplate"),
    "txn_pk": ("review", "PendingTransaction"),
    "flag_pk": ("core", "RiskFlag"),
}

# Longest-prefix-first mapping from URL name to the model a bare <pk> refers to.
# 288 routes use a bare "pk", so the name is the only available signal.
NAME_PREFIX_MODEL_MAP = [
    ("entity_coa", ("core", "EntityChartOfAccount")),
    ("entity_officer", ("core", "EntityOfficer")),
    ("entity", ("core", "Entity")),
    ("financial_year", ("core", "FinancialYear")),
    ("client_summary", ("core", "EvaClientSummary")),
    ("client", ("core", "Client")),
    ("journal", ("core", "AdjustingJournal")),
    ("bulk_journal", ("core", "BulkJournalUpload")),
    ("trial_balance", ("core", "FinancialYear")),
    ("legal_template", ("core", "LegalDocumentTemplate")),
    ("legal_doc", ("core", "LegalDocument")),
    ("engagement_letter", ("core", "EngagementLetter")),
    ("meeting_note", ("core", "MeetingNote")),
    ("dividend", ("core", "DividendEvent")),
    ("depreciation", ("core", "DepreciationAsset")),
    ("workpaper_template", ("core", "WorkPaperTemplate")),
    ("template", ("core", "DocumentTemplate")),
    ("tax_planning_scenario", ("core", "TaxPlanningScenario")),
    ("tax_planning", ("core", "TaxPlanningWorksheet")),
    ("bas_commentary", ("core", "BASPeriodCommentary")),
    ("bas", ("core", "BASPeriod")),
    ("stock", ("core", "StockItem")),
    ("general_pool", ("core", "GeneralPool")),
    ("review", ("review", "ReviewJob")),
    ("software", ("core", "SoftwareConfig")),
    ("package", ("core", "FinancialYear")),
    ("user", ("accounts", "User")),
    ("invitation", ("accounts", "Invitation")),
]

# Non-UUID parameters that cannot be resolved from a table.
STATIC_PARAM_VALUES = {
    "doc_type": "engagement_letter",
    "fmt": "pdf",
    "period_number": "1",
    "stage_num": "1",
    "provider_name": "xero",
}


class Command(BaseCommand):
    help = "Generate the Tier 1 route manifest (E2E databases only)."

    def add_arguments(self, parser):
        parser.add_argument("--output", default="/opt/statementhub/e2e/tier1/routes.manifest.json")

    def handle(self, *args, **options):
        assert_e2e_database()

        routes = []
        for pattern, prefix in self._walk(get_resolver()):
            routes.append(self._classify(pattern, prefix))

        routes.sort(key=lambda r: (r["name"] or "", r["path"]))

        crawlable = [r for r in routes if r["status"] == "crawlable"]
        excluded = [r for r in routes if r["status"] == "excluded"]
        unresolved = [r for r in routes if r["status"] == "unresolved"]

        # Django admin is reported separately. Counting 432 third-party admin routes
        # alongside the application's own would inflate the apparent surface and make
        # the coverage fraction look better than it is.
        admin_routes = [r for r in routes if r["path"].startswith("/admin/")]
        own = [r for r in routes if not r["path"].startswith("/admin/")]
        own_crawlable = [r for r in own if r["status"] == "crawlable"]
        own_excluded = [r for r in own if r["status"] == "excluded"]
        own_unresolved = [r for r in own if r["status"] == "unresolved"]

        by_category = {}
        for r in own_excluded:
            by_category[r.get("category") or "infrastructure"] = (
                by_category.get(r.get("category") or "infrastructure", 0) + 1
            )

        manifest = {
            "_comment": (
                "Generated by manage.py e2e_dump_routes. Committed deliberately: a new "
                "application route shows up here as a diff, which is what keeps Tier 1 "
                "coverage honest. Do not hand-edit — regenerate instead."
            ),
            "summary": {
                "routes_total": len(routes),
                "django_admin_excluded": len(admin_routes),
                "application_routes": len(own),
                "crawlable": len(own_crawlable),
                "excluded": len(own_excluded),
                "excluded_by_category": by_category,
                "unresolved": len(own_unresolved),
                "_note": (
                    "'excluded' is not coverage. Those routes mutate state, cost money per "
                    "call, or have effects that leave this machine; they are covered "
                    "deliberately by Tier 2 flows instead of by the crawl."
                ),
            },
            "routes": routes,
        }

        out = Path(options["output"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(manifest, indent=2) + "\n")

        self.stdout.write(self.style.SUCCESS(
            f"{len(own)} application routes "
            f"({len(admin_routes)} Django admin routes reported separately)"
        ))
        self.stdout.write(f"  crawlable  {len(own_crawlable)}")
        self.stdout.write(f"  excluded   {len(own_excluded)}  " + ", ".join(
            f"{k}={v}" for k, v in sorted(by_category.items())
        ))
        self.stdout.write(f"  unresolved {len(own_unresolved)}")
        self.stdout.write(f"manifest → {out}")

        if unresolved:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                f"{len(unresolved)} routes are NOT covered because their parameters could not "
                "be resolved. These are recorded in the manifest and are real coverage gaps:"
            ))
            reasons = {}
            for r in unresolved:
                reasons.setdefault(r["reason"], []).append(r["name"] or r["path"])
            for reason, names in sorted(reasons.items()):
                self.stdout.write(f"  {reason}: {len(names)}")
                for n in sorted(names)[:6]:
                    self.stdout.write(f"    - {n}")
                if len(names) > 6:
                    self.stdout.write(f"    ... and {len(names) - 6} more")

    # --- resolver walk ------------------------------------------------------

    def _walk(self, resolver, prefix=""):
        for entry in resolver.url_patterns:
            current = prefix + str(entry.pattern)
            if isinstance(entry, URLResolver):
                yield from self._walk(entry, current)
            elif isinstance(entry, URLPattern):
                yield entry, prefix

    # --- classification -----------------------------------------------------

    def _classify(self, pattern, prefix):
        raw_path = prefix + str(pattern.pattern)
        name = pattern.name
        namespace = getattr(pattern, "namespace", None)
        full_name = name

        record = {
            "name": full_name,
            "path": "/" + raw_path.lstrip("/"),
            "namespace": namespace,
            "status": "crawlable",
            "category": None,
            "reason": None,
            "url": None,
            "params": {},
        }

        request_path = "/" + raw_path.lstrip("/")
        for p in INFRASTRUCTURE_PATH_PREFIXES:
            if request_path.startswith(p):
                record.update(status="excluded", reason=f"infrastructure path ({p})")
                return record

        if not name:
            record.update(status="excluded", reason="unnamed route")
            return record

        for p in INFRASTRUCTURE_NAME_PATTERNS:
            if re.search(p, name):
                record.update(status="excluded", reason=f"infrastructure route (matched /{p}/)")
                return record

        for category, patterns in EXCLUSION_RULES:
            for p in patterns:
                if re.search(p, name):
                    record.update(
                        status="excluded",
                        category=category,
                        reason=f"{category} (matched /{p}/) — not requested on GET",
                    )
                    return record

        params = re.findall(r"<(?:[a-z]+:)?([a-z_]+)>", raw_path)
        resolved = {}
        for param in params:
            value, err = self._resolve_param(param, name, raw_path)
            if err:
                record.update(status="unresolved", reason=err)
                return record
            resolved[param] = value

        url = "/" + raw_path.lstrip("/")
        for param, value in resolved.items():
            url = re.sub(r"<(?:[a-z]+:)?" + re.escape(param) + r">", str(value), url)

        if "<" in url:
            record.update(status="unresolved", reason=f"unsubstituted parameter in {url}")
            return record

        record["url"] = url
        record["params"] = {k: str(v) for k, v in resolved.items()}
        return record

    @staticmethod
    def _preceding_segment(raw_path, param):
        """Return the path segment immediately before the given parameter.

        For "years/<uuid:pk>/statements/" and param "pk" this returns "years",
        which identifies the model far more reliably than the URL name does.
        """
        parts = ("/" + raw_path.lstrip("/")).strip("/").split("/")
        for index, part in enumerate(parts):
            if re.fullmatch(r"<(?:[a-z]+:)?" + re.escape(param) + r">", part):
                return parts[index - 1] if index > 0 else None
        return None

    def _resolve_param(self, param, url_name, raw_path):
        from django.apps import apps

        if param in STATIC_PARAM_VALUES:
            return STATIC_PARAM_VALUES[param], None

        # Explicit parameter names are unambiguous, so they win.
        target = PARAM_MODEL_MAP.get(param)

        # Then the path segment, which beats the name heuristic in practice.
        if target is None:
            segment = self._preceding_segment(raw_path, param)
            if segment:
                target = PATH_SEGMENT_MODEL_MAP.get(segment)

        # Name prefix last, as a fallback for paths whose segments are uninformative.
        if target is None:
            for prefix, candidate in sorted(NAME_PREFIX_MODEL_MAP, key=lambda x: -len(x[0])):
                if url_name.startswith(prefix):
                    target = candidate
                    break

        if target is None:
            segment = self._preceding_segment(raw_path, param)
            return None, (
                f"no model mapping for parameter '{param}'"
                + (f" under path segment '{segment}'" if segment else "")
            )

        app_label, model_name = target
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            return None, f"model {app_label}.{model_name} does not exist"

        obj = model.objects.values_list("pk", flat=True).first()
        if obj is None:
            return None, f"no rows in {app_label}.{model_name} to supply '{param}'"

        return obj, None
