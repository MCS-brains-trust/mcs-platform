"""Casing for names of people.

XPM stores client names in capitals and is the system of record for reporting,
so it keeps them (core/jt_identity.py only ever reads). This is about the
platform's own copy, which should read as a name.

Applied to person-name fields only. Company and trust names are fenced out
because title case damages the acronyms they carry: "ABC PTY LTD" would become
"Abc Pty Ltd".
"""
import re

# Nobiliary particles that stay lowercase inside a name. A particle leading the
# whole name keeps its lowercase form too -- "de Silva" is how the name is
# written, whether or not it opens the line.
PARTICLES = frozenset({
    "van", "von", "der", "den", "de", "del", "della", "di", "da", "du",
    "la", "le", "les", "of", "bin", "binte", "bint", "ibn", "al", "el",
    "ter", "tot", "op", "aan", "y",
})

# Generational suffixes, kept as written rather than title-cased to "Iii".
ROMAN_NUMERALS = frozenset({"II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"})

# Characters that start a new capitalised part inside a single word.
INTRA_WORD_SEPARATORS = ("'", "’", "-")


def _title_case_word(word):
    """Capitalise a word, and any part following an apostrophe or hyphen."""
    result = []
    capitalise_next = True
    for char in word:
        if capitalise_next and char.isalpha():
            result.append(char.upper())
            capitalise_next = False
        else:
            result.append(char.lower())
        if char in INTRA_WORD_SEPARATORS:
            capitalise_next = True
    return "".join(result)


def _is_machine_cased(value):
    """Is the value entirely upper or entirely lower case?

    Mixed case means a person chose it deliberately, and this rule does not
    second-guess that. A value with no cased characters at all (digits,
    punctuation) is not machine-cased -- there is nothing to normalise.
    """
    if not any(char.isalpha() for char in value):
        return False
    return value == value.upper() or value == value.lower()


def normalise_person_name(value):
    """Return *value* cased as a name, or unchanged if it should be left alone.

    Unchanged when: falsy, blank, mixed case, or already correct. Internal
    whitespace is preserved -- this changes casing and nothing else.
    """
    if not value or not value.strip():
        return value
    if not _is_machine_cased(value):
        return value

    # re.split with a capture group keeps the separators, so spacing survives.
    out = []
    for part in re.split(r"(\s+)", value):
        if not part or part.isspace():
            out.append(part)
            continue
        if part.upper() in ROMAN_NUMERALS:
            out.append(part.upper())
        elif part.lower() in PARTICLES:
            out.append(part.lower())
        else:
            out.append(_title_case_word(part))
    return "".join(out)
