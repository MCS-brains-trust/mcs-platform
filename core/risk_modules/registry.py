"""
Detection Module Registry
=========================

Central registry of all dedicated detection modules.  The risk engine
orchestrator iterates over DETECTION_MODULES and calls module.run() for
each financial year being assessed.

To add a new module:
    1. Create a subclass of BaseDetectionModule in this package.
    2. Import it here and add its dotted path to DETECTION_MODULES.

MODULE_COVERS maps module_id → list of old Tier 2 rule_id prefixes that
the module replaces.  The orchestrator uses this to skip individual rules
that are now handled by a dedicated module, preventing double-coverage.
"""

import importlib
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Registered detection modules (dotted import paths)
# Order matters: modules execute in this sequence.
# ---------------------------------------------------------------------------
DETECTION_MODULES = [
    "core.risk_modules.div7a.Div7ADetectionModule",
    "core.risk_modules.going_concern.GoingConcernModule",
    "core.risk_modules.section100a.Section100AModule",
    "core.risk_modules.cluster_rp.RelatedPartyCluster",
    "core.risk_modules.cluster_sgc.SGCCluster",
    "core.risk_modules.cluster_tpar.TPARCluster",
]


# ---------------------------------------------------------------------------
# MODULE_COVERS: module_id → old rule_id prefixes that the module replaces
# Individual Tier 2 rules with these prefixes are skipped when the module
# is active.  The old rules are NOT deleted (audit trail), just bypassed.
# ---------------------------------------------------------------------------
MODULE_COVERS = {
    "div7a": [
        "D7A-",       # Old D7A-01 through D7A-06 (keyword-based heuristics)
        "T2-D7A-",    # New T2-D7A-01 through T2-D7A-08 (handled by module)
    ],
    "going_concern": [
        "SOL-",       # Old SOL-01 through SOL-04 (solvency: net assets, current ratio, etc.)
    ],
    "section100a": [
        "TRU-",       # Old TRU-01 through TRU-06 (trust distribution rules)
    ],
    "cluster_rp": [
        "RP-",        # Old RP-01 through RP-05 (related party rules)
    ],
    "cluster_sgc": [
        "SG-",        # Old SG-01 through SG-05 (superannuation rules)
    ],
    "cluster_tpar": [
        # TPAR was not a standalone rule before — no old rules to cover.
        # The cluster adds new detection capability.
    ],
}


def get_module_classes():
    """Import and return all registered module classes.

    Silently skips modules that fail to import (e.g. if a dependency
    is not yet implemented).  Logs a warning for each failure.
    """
    classes = []
    for dotted_path in DETECTION_MODULES:
        try:
            module_path, class_name = dotted_path.rsplit(".", 1)
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
            classes.append(cls)
        except (ImportError, AttributeError) as e:
            logger.warning(
                "Could not load detection module %s: %s", dotted_path, e
            )
    return classes


def is_covered_by_module(rule_id):
    """Check if a Tier 2 rule_id is covered by a registered module.

    Returns True if the rule should be skipped because a module handles it.
    """
    for prefixes in MODULE_COVERS.values():
        for prefix in prefixes:
            if rule_id.startswith(prefix):
                return True
    return False


def get_covered_rule_prefixes():
    """Return a flat set of all rule_id prefixes covered by modules."""
    prefixes = set()
    for prefix_list in MODULE_COVERS.values():
        prefixes.update(prefix_list)
    return prefixes
