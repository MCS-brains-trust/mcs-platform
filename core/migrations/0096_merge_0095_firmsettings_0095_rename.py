"""
Migration: 0096_merge_0095_firmsettings_0095_rename

Merge migration to resolve the conflict between:
  - 0095_firmsettings  (added in this branch — FirmSettings model)
  - 0095_rename_core_govdocc_entity__7f0b6f_idx_core_govern_entity__20bbbb_idx_and_more
    (existed on the production server, applied outside of git history)

This merge migration has no operations of its own; it simply declares
both leaf nodes as its dependencies so Django can proceed.

NOTE: The 0095_rename migration only exists on the production server.
      On fresh/local environments it is absent, so we detect it at
      import time and only include it as a dependency when present.
"""
import importlib
from django.db import migrations


def _build_dependencies():
    base = [("core", "0095_firmsettings")]
    rename_module = (
        "core.migrations"
        ".0095_rename_core_govdocc_entity__7f0b6f_idx_core_govern_entity__20bbbb_idx_and_more"
    )
    try:
        importlib.import_module(rename_module)
        base.append((
            "core",
            "0095_rename_core_govdocc_entity__7f0b6f_idx_core_govern_entity__20bbbb_idx_and_more",
        ))
    except (ImportError, ModuleNotFoundError):
        pass  # Not present in local / CI environments — safe to skip
    return base


class Migration(migrations.Migration):
    dependencies = _build_dependencies()
    operations = []
