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