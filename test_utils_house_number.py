"""
Unit tests for house number parsing and nearest selection.
"""

import pytest
from utils import parse_house_number, find_nearest_house_number

class TestHouseNumberParsing:
    def test_parse_simple(self):
        assert parse_house_number("38") == 38
        assert parse_house_number("  7 ") == 7

    def test_parse_with_suffix(self):
        assert parse_house_number("38a") == 38
        assert parse_house_number("38 a") == 38
        assert parse_house_number("38-40") == 38

    def test_parse_invalid(self):
        assert parse_house_number("") is None
        assert parse_house_number("a38") is None
        assert parse_house_number("no-number") is None

class TestNearestHouseNumber:
    def test_exact_match(self):
        assert find_nearest_house_number("21", ["20", "21", "22"]) == "21"
        assert find_nearest_house_number("21a", ["21a", "21b"]) == "21a"

    def test_same_number_different_suffix(self):
        assert find_nearest_house_number("21c", ["21a", "21b"]) in {"21a", "21b"}
        assert find_nearest_house_number("10b", ["10", "10a"]) in {"10", "10a"}

    def test_nearest_numeric(self):
        assert find_nearest_house_number("21", ["22", "130"]) == "22"
        assert find_nearest_house_number("50", ["48", "52"]) in {"48", "52"}

    def test_prefers_close_over_far(self):
        # Ensure nearby numbers beat far-out values like 110
        assert find_nearest_house_number("17", ["110", "16", "18"]) in {"16", "18"}

    def test_empty_available(self):
        assert find_nearest_house_number("10", []) is None
        assert find_nearest_house_number("", ["1", "2"]) is None

if __name__ == "__main__":
    pytest.main([__file__, "-v"])

class TestSchreibweise:
    """Hausnummern mit Buchstaben: Gross-/Kleinschreibung und Leerzeichen duerfen keinen Unterschied machen."""

    @pytest.mark.parametrize("eingabe, erwartet", [("6a", "6a"), ("6A", "6a"), ("6 a", "6a"), (" 6 A ", "6a"), ("12-14", "12-14"), ("6/1", "6/1"), ("", "")])
    def test_normalisieren(self, eingabe, erwartet):
        from utils import normalize_house_number
        assert normalize_house_number(eingabe) == erwartet

    def test_grossbuchstabe_findet_die_kleingeschriebene_nummer(self):
        assert find_nearest_house_number("11A", ["10", "11", "11a", "12"]) == "11a"

    def test_kleinbuchstabe_findet_die_grossgeschriebene_nummer(self):
        assert find_nearest_house_number("12a", ["12", "12A", "13"]) == "12A"

    def test_leerzeichen_in_den_daten_stoert_nicht(self):
        assert find_nearest_house_number("12a", ["12 a", "13"]) == "12 a"

    def test_ohne_gleiche_nummer_bleibt_die_naehe(self):
        assert find_nearest_house_number("50", ["48", "52"]) in {"48", "52"}
