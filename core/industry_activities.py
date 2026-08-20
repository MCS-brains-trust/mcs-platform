"""The ATO BIC activity index: 2,817 business activities -> 582 codes.

`industry_codes` holds the 582 official codes and their classification labels.
Those labels are what the ATO calls the industry; they are not what an operator
types. Nobody looks up "Meat Processing" -- they look up "abattoir". This module
holds the other half of the official document: every activity description the
ATO maps onto a code, which is exactly what makes its own Business Industry Code
tool findable.

The index is a committed fixture, regenerated once a year from the published PDF
by `manage.py build_industry_activities`. Nothing here touches the network.
"""
import json
import logging
import os
import re

logger = logging.getLogger(__name__)

# The live document carries ~2,817 activity/code pairs. The floor exists so a
# layout change that stops the regex matching fails loudly: a half-empty index
# looks like a working search that simply cannot find anything.
MIN_EXPECTED_PAIRS = 2000

# "<activity text> <5-digit code>" at end of line. The code is the anchor: the
# activity is whatever precedes it on that line.
_PAIR = re.compile(r"^(?P<activity>.*?)\s+(?P<code>\d{5})\s*$", re.MULTILINE)

# Column headings and page furniture that survive text extraction. These end in
# the word "code" with no code on the line, or are the document's own title.
_FURNITURE = {"code", "activity", "activity code", "business industry codes", ""}


class ActivityParseError(Exception):
    """The extracted text did not look like the BIC activity list."""


def parse_bic_text(text):
    """{code: [activity, ...]} from the PDF's extracted text.

    Activities are de-duplicated and sorted so a regenerated fixture diffs
    against the previous one line by line rather than reshuffling.
    """
    grouped = {}
    for match in _PAIR.finditer(text):
        activity = match.group("activity").strip()
        if activity.lower() in _FURNITURE:
            continue
        # A trailing "... code" is a heading fragment, not an activity.
        if activity.lower().endswith(" code"):
            continue
        grouped.setdefault(match.group("code"), set()).add(activity)
    if not grouped:
        raise ActivityParseError(
            "no activity/code pairs found -- the document layout has changed "
            "or the text extraction produced nothing usable"
        )
    return {code: sorted(acts) for code, acts in sorted(grouped.items())}


def validate_against_codes(index, code_map, min_pairs=MIN_EXPECTED_PAIRS):
    """Refuse an index that invents codes or collapsed to a fraction of the list.

    Every code MUST be one of the official 582: a code the fixture does not know
    would render as a choice the form cannot validate.
    """
    unknown = sorted(set(index) - set(code_map))
    if unknown:
        raise ActivityParseError(
            f"{len(unknown)} code(s) are not official ATO BICs: "
            f"{', '.join(unknown[:10])}"
        )
    pairs = sum(len(acts) for acts in index.values())
    if pairs < min_pairs:
        raise ActivityParseError(
            f"only {pairs} activity/code pairs, expected at least {min_pairs} -- "
            "refusing to write a truncated index"
        )
    return pairs


def diff_index(new, previous):
    """What regenerating the index would change, as data rather than prose.

    A removed code is the case worth seeing: it means the ATO retired a code
    that entities may still be pointing at.
    """
    return {
        "codes_added": sorted(set(new) - set(previous)),
        "codes_removed": sorted(set(previous) - set(new)),
        "codes_changed": sorted(
            code for code in set(new) & set(previous) if new[code] != previous[code]
        ),
    }


_FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "ato_industry_activities.json"
)


def load_activities():
    """The committed index, or {} if it has not been generated yet.

    Missing is not fatal: the industry dropdown falls back to searching the 582
    official labels, which is how it behaved before this index existed.
    """
    try:
        with open(_FIXTURE_PATH) as handle:
            return json.load(handle)
    except FileNotFoundError:
        logger.warning(
            "ato_industry_activities.json is missing -- industry search falls "
            "back to official labels only. Run build_industry_activities."
        )
        return {}


ACTIVITY_INDEX = load_activities()
