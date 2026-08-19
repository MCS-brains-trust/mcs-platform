# core/management/commands/link_entities_to_xpm.py
"""Propose, then apply, the XPM link on StatementHub's existing entities.

Production holds 15 entities and none of them carry an xpm_client_id: the column
existed and was never populated. This walks them, asks Job Tracker for a client
with the same ABN (digits only, since ABNs are typed with spaces on one side and
stored bare on the other), and writes a CSV of proposals.

Nothing is written to the database without --apply AND a reviewed file. A wrong
client link does not stay contained: it reaches signing and filing later. Fifteen
rows do not justify an unreviewed automated match.
"""
import csv

from django.core.management.base import BaseCommand, CommandError

from core.jt_identity import search_clients
from core.models import Entity

DEFAULT_OUT = "/opt/statementhub/xpm_link_proposals.csv"
FIELDNAMES = ["entity_id", "entity_name", "entity_abn",
              "xpm_client_id", "jt_display_name", "match"]


def normalise_abn(value):
    """Digits only. '11 222 333 444', '11-222-333-444' and '11222333444' are one ABN."""
    if not value:
        return ""
    return "".join(ch for ch in str(value) if ch.isdigit())


class Command(BaseCommand):
    help = "Propose (default) or apply XPM client links for StatementHub entities."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Write the links. Requires --from-file.")
        parser.add_argument("--from-file", dest="from_file", default=None,
                            help="The reviewed proposals CSV to apply.")
        parser.add_argument("--out", default=DEFAULT_OUT,
                            help=f"Where to write proposals in a dry run (default {DEFAULT_OUT}).")

    def handle(self, *args, **options):
        if options["apply"]:
            return self._apply(options["from_file"])
        return self._propose(options["out"])

    def _propose(self, out_path):
        self.stdout.write("DRY RUN — nothing will be written to the database.")
        rows = []
        for entity in Entity.objects.filter(is_archived=False).order_by("entity_name"):
            if entity.xpm_client_id:
                self.stdout.write(f"  linked    {entity.entity_name} -> {entity.xpm_client_id}")
                continue

            abn = normalise_abn(entity.abn)
            if not abn:
                self.stdout.write(f"  no_abn    {entity.entity_name} — link by hand")
                rows.append(self._row(entity, "", "", "no_abn"))
                continue

            result = search_clients(abn, limit=10)
            if result.failed:
                self.stdout.write(f"  UNAVAILABLE {entity.entity_name} — Job Tracker unreachable")
                rows.append(self._row(entity, "", "", "unavailable"))
                continue

            matches = [c for c in result.clients if normalise_abn(c.get("abn")) == abn]
            if len(matches) == 1:
                match = matches[0]
                self.stdout.write(
                    f"  single    {entity.entity_name} -> {match.get('xpmId')} "
                    f"({match.get('displayName')})"
                )
                rows.append(self._row(entity, match.get("xpmId", ""),
                                      match.get("displayName", ""), "single"))
            elif matches:
                self.stdout.write(
                    f"  multiple  {entity.entity_name} — {len(matches)} JT clients share this ABN; "
                    "link by hand"
                )
                rows.append(self._row(entity, "", "", "multiple"))
            else:
                self.stdout.write(f"  none      {entity.entity_name} — no JT client with this ABN")
                rows.append(self._row(entity, "", "", "none"))

        with open(out_path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)

        self.stdout.write(f"\nProposals written to {out_path}")
        self.stdout.write(
            "Review every row — delete what is wrong, fill in what is blank — then:\n"
            f"  manage.py link_entities_to_xpm --apply --from-file {out_path}"
        )

    def _row(self, entity, xpm_client_id, jt_display_name, match):
        return {
            "entity_id": str(entity.pk),
            "entity_name": entity.entity_name,
            "entity_abn": normalise_abn(entity.abn),
            "xpm_client_id": xpm_client_id,
            "jt_display_name": jt_display_name,
            "match": match,
        }

    def _apply(self, from_file):
        if not from_file:
            raise CommandError(
                "--apply requires --from-file: the reviewed proposals CSV. Run without "
                "--apply first, read every row, then apply that file."
            )
        try:
            with open(from_file, newline="") as fh:
                rows = list(csv.DictReader(fh))
        except OSError as exc:
            raise CommandError(f"could not read {from_file}: {exc}")

        written = skipped = ignored = 0
        for row in rows:
            xpm_client_id = (row.get("xpm_client_id") or "").strip()
            if not xpm_client_id:
                ignored += 1
                continue
            try:
                entity = Entity.objects.get(pk=row["entity_id"])
            except (Entity.DoesNotExist, KeyError, ValueError):
                self.stdout.write(f"  ignored   unknown entity_id {row.get('entity_id')!r}")
                ignored += 1
                continue
            if entity.xpm_client_id:
                self.stdout.write(
                    f"  skipped   {entity.entity_name} already linked to {entity.xpm_client_id}"
                )
                skipped += 1
                continue
            entity.xpm_client_id = xpm_client_id
            entity.save(update_fields=["xpm_client_id"])
            self.stdout.write(f"  LINKED    {entity.entity_name} -> {xpm_client_id}")
            written += 1

        self.stdout.write(f"\nlinked {written}, skipped {skipped}, ignored {ignored}")
