"""Person names arriving in capitals are normalised to ordinary case.

XPM holds "ELLIOTT JAQUES" in capitals and is the system of record for
reporting, so it keeps them -- core/jt_identity.py only ever reads. The
platform's own copy should read as a name.

The rule acts only when a value is entirely upper or entirely lower case. Mixed
case means a person made a deliberate choice about it, and guessing over the
top of that does more harm than the caps do.

Mc/Mac are deliberately NOT special-cased: capitalising after them gets
McDonald right but turns Mackay into MacKay and Macey into MacEy. Plain title
case is wrong in a predictable, correctable way rather than confidently wrong.
"""
from django.test import SimpleTestCase

from core.name_case import normalise_person_name


class NormalisePersonNameTests(SimpleTestCase):
    def test_all_caps_becomes_ordinary_case(self):
        cases = {
            "ELLIOTT JAQUES": "Elliott Jaques",
            "JANE SMITH": "Jane Smith",
            "RONEN DAVIDOV": "Ronen Davidov",
        }
        for given, expected in cases.items():
            with self.subTest(given=given):
                self.assertEqual(normalise_person_name(given), expected)

    def test_all_lowercase_becomes_ordinary_case(self):
        self.assertEqual(normalise_person_name("elliott jaques"), "Elliott Jaques")

    def test_mixed_case_is_left_exactly_as_entered(self):
        """A deliberate choice is not second-guessed."""
        for given in ("Elliott Jaques", "de Silva", "van der Berg",
                      "McDonald", "MacKay", "O'Brien", "DeAngelo", "eBay Guy"):
            with self.subTest(given=given):
                self.assertEqual(normalise_person_name(given), given)

    def test_apostrophes_capitalise_the_following_letter(self):
        self.assertEqual(normalise_person_name("O'BRIEN"), "O'Brien")
        self.assertEqual(normalise_person_name("D'ANGELO"), "D'Angelo")

    def test_hyphens_capitalise_the_following_letter(self):
        self.assertEqual(normalise_person_name("SMITH-JONES"), "Smith-Jones")
        self.assertEqual(
            normalise_person_name("ANNE-MARIE SMITH-JONES"), "Anne-Marie Smith-Jones"
        )

    def test_particles_are_lowercased(self):
        cases = {
            "VAN DER BERG": "van der Berg",
            "JAN VAN DER BERG": "Jan van der Berg",
            "DE SILVA": "de Silva",
            "MARIA DEL TORO": "Maria del Toro",
        }
        for given, expected in cases.items():
            with self.subTest(given=given):
                self.assertEqual(normalise_person_name(given), expected)

    def test_a_leading_particle_still_starts_with_a_capital(self):
        """Nothing should ever render with a lowercase first letter."""
        self.assertEqual(normalise_person_name("VAN DER BERG")[0], "v")
        self.assertEqual(normalise_person_name("DE SILVA"), "de Silva")

    def test_mc_and_mac_get_plain_title_case(self):
        self.assertEqual(normalise_person_name("MCDONALD"), "Mcdonald")
        self.assertEqual(normalise_person_name("MACKAY"), "Mackay")
        self.assertEqual(normalise_person_name("MACEY"), "Macey")

    def test_roman_numeral_suffixes_stay_uppercase(self):
        self.assertEqual(normalise_person_name("JOHN SMITH III"), "John Smith III")
        self.assertEqual(normalise_person_name("HENRY FORD II"), "Henry Ford II")

    def test_initials_survive(self):
        self.assertEqual(normalise_person_name("J R SMITH"), "J R Smith")

    def test_blank_and_none_are_returned_unchanged(self):
        for given in ("", "   ", None):
            with self.subTest(given=given):
                self.assertEqual(normalise_person_name(given), given)

    def test_internal_spacing_is_preserved(self):
        """Not a whitespace cleaner -- only casing changes."""
        self.assertEqual(normalise_person_name("JANE  SMITH"), "Jane  Smith")
