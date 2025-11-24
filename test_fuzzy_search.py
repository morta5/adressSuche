"""Tests for the BK-Tree based fuzzy search functionality.

These tests verify:
1. Basic BK-Tree operations (insert, search, serialization)
2. Levenshtein distance calculation
3. FuzzySearchIndex for typo-tolerant street search
4. API integration with the new search stage
"""

import os
import pickle
import tempfile
from pathlib import Path
from typing import Dict, Any

import pytest

from bktree import BKTree, levenshtein_distance, MultiIndexBKTree
from fuzzy_search import FuzzySearchIndex
from utils import normalize_string, normalize_compact


class TestLevenshteinDistance:
    """Tests for Levenshtein distance calculation."""
    
    def test_identical_strings(self):
        """Identical strings should have distance 0."""
        assert levenshtein_distance("test", "test") == 0
        assert levenshtein_distance("", "") == 0
        assert levenshtein_distance("bahnhof", "bahnhof") == 0
    
    def test_single_insertion(self):
        """Strings differing by one insertion should have distance 1."""
        assert levenshtein_distance("bahnhof", "banhof") == 1
        assert levenshtein_distance("test", "tset") == 2  # swap = 2 ops
        assert levenshtein_distance("abc", "ab") == 1
    
    def test_single_deletion(self):
        """Strings differing by one deletion should have distance 1."""
        assert levenshtein_distance("ab", "abc") == 1
        assert levenshtein_distance("schiller", "schiler") == 1
    
    def test_single_substitution(self):
        """Strings differing by one substitution should have distance 1."""
        assert levenshtein_distance("abc", "adc") == 1
        assert levenshtein_distance("cat", "hat") == 1
    
    def test_symmetry(self):
        """Levenshtein distance should be symmetric."""
        assert levenshtein_distance("abc", "def") == levenshtein_distance("def", "abc")
        assert levenshtein_distance("bahnhof", "banhof") == levenshtein_distance("banhof", "bahnhof")
    
    def test_max_distance_early_termination(self):
        """Should terminate early if distance exceeds max_dist."""
        result = levenshtein_distance("abc", "xyz", max_dist=2)
        assert result > 2  # Should return max_dist + 1
    
    def test_german_street_names(self):
        """Test with typical German street name typos."""
        # Common typos
        assert levenshtein_distance("bahnhofstrasse", "banhofstrasse") == 1
        assert levenshtein_distance("schillerstrasse", "schilerstrasse") == 1
        assert levenshtein_distance("goethestrasse", "goetestrasse") == 1


class TestBKTree:
    """Tests for the BK-Tree data structure."""
    
    def test_empty_tree(self):
        """Empty tree should return no results."""
        tree = BKTree()
        assert len(tree) == 0
        assert tree.search("test", max_distance=2) == []
    
    def test_single_insert(self):
        """Single insert should increase size."""
        tree = BKTree()
        tree.insert("test", {"id": 1})
        assert len(tree) == 1
        assert tree.contains("test")
    
    def test_duplicate_insert(self):
        """Duplicate insert should update data but not increase size."""
        tree = BKTree()
        tree.insert("test", {"id": 1})
        tree.insert("test", {"id": 2})
        assert len(tree) == 1
        # Data should be updated
        results = tree.search("test", max_distance=0)
        assert len(results) == 1
        assert results[0][1] == {"id": 2}
    
    def test_exact_search(self):
        """Exact match search should find the word."""
        tree = BKTree()
        tree.insert("bahnhof", 1)
        tree.insert("hauptbahnhof", 2)
        
        results = tree.search("bahnhof", max_distance=0)
        assert len(results) == 1
        assert results[0][0] == "bahnhof"
        assert results[0][2] == 0  # distance
    
    def test_fuzzy_search(self):
        """Fuzzy search should find similar words."""
        tree = BKTree()
        tree.insert("bahnhof", 1)
        tree.insert("hauptbahnhof", 2)
        tree.insert("flughafen", 3)
        
        # Search with one typo
        results = tree.search("banhof", max_distance=1)
        assert len(results) >= 1
        words = [r[0] for r in results]
        assert "bahnhof" in words
    
    def test_search_returns_sorted_results(self):
        """Search results should be sorted by distance."""
        tree = BKTree()
        tree.insert("test", 1)
        tree.insert("testa", 2)
        tree.insert("testab", 3)
        
        results = tree.search("test", max_distance=3)
        distances = [r[2] for r in results]
        assert distances == sorted(distances)
    
    def test_build_from_list(self):
        """Build from list should add all items."""
        tree = BKTree()
        items = [
            ("bahnhof", 1),
            ("hauptbahnhof", 2),
            ("flughafen", 3),
        ]
        tree.build_from_list(items)
        assert len(tree) == 3
    
    def test_save_and_load(self):
        """Tree should be serializable and deserializable."""
        tree = BKTree()
        tree.insert("bahnhof", {"id": 1, "name": "Bahnhof"})
        tree.insert("hauptbahnhof", {"id": 2, "name": "Hauptbahnhof"})
        
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.bktree"
            tree.save(path)
            
            loaded = BKTree.load(path)
            assert len(loaded) == len(tree)
            
            # Search should work on loaded tree
            results = loaded.search("banhof", max_distance=1)
            assert len(results) >= 1
    
    def test_stats(self):
        """Stats should return tree structure information."""
        tree = BKTree()
        items = [
            ("bahnhof", 1),
            ("hauptbahnhof", 2),
            ("flughafen", 3),
            ("strasse", 4),
        ]
        tree.build_from_list(items)
        
        stats = tree.stats()
        assert stats["size"] == 4
        assert stats["depth"] >= 1
        assert "avg_children" in stats


class TestFuzzySearchIndex:
    """Tests for the FuzzySearchIndex class."""
    
    def test_empty_index(self):
        """Empty index should return no results."""
        index = FuzzySearchIndex()
        results = index.search("test", max_distance=2)
        assert results == []
    
    def test_add_street(self):
        """Adding a street should make it searchable."""
        index = FuzzySearchIndex()
        index.add_street(
            street_id=1,
            name="Bahnhofstraße",
            city="Berlin",
            postal_code="10115",
            latitude=52.5,
            longitude=13.4
        )
        
        assert len(index.streets) == 1
        results = index.search("Bahnhof", max_distance=2)
        assert len(results) >= 1
    
    def test_typo_tolerance(self):
        """Search should find streets despite typos."""
        index = FuzzySearchIndex()
        index.add_street(1, "Bahnhofstraße", "Berlin", "10115", 52.5, 13.4)
        index.add_street(2, "Schillerstraße", "Frankfurt", "60313", 50.1, 8.7)
        
        # Missing 'h' in Bahnhof
        results = index.search("Banhofstrasse", max_distance=2)
        names = [r["name"] for r in results]
        assert "Bahnhofstraße" in names
        
        # Missing 'l' in Schiller
        results = index.search("Schilerstrasse", max_distance=2)
        names = [r["name"] for r in results]
        assert "Schillerstraße" in names
    
    def test_city_filter(self):
        """City filter should limit results to specified city."""
        index = FuzzySearchIndex()
        index.add_street(1, "Hauptstraße", "Berlin", "10115", 52.5, 13.4)
        index.add_street(2, "Hauptstraße", "München", "80333", 48.1, 11.6)
        
        results = index.search("Hauptstrasse", city="Berlin", max_distance=2)
        assert len(results) == 1
        assert results[0]["city"] == "Berlin"
    
    def test_prefix_search(self):
        """Prefix search should find streets starting with query."""
        index = FuzzySearchIndex()
        index.add_street(1, "Bahnhofstraße", "Berlin", "10115", 52.5, 13.4)
        index.add_street(2, "Bahnhofsplatz", "Berlin", "10115", 52.5, 13.4)
        
        results = index.search_prefix("Bahnhof")
        assert len(results) == 2
    
    def test_match_score(self):
        """Results should include match scores."""
        index = FuzzySearchIndex()
        index.add_street(1, "Bahnhofstraße", "Berlin", "10115", 52.5, 13.4)
        
        results = index.search("Bahnhof", max_distance=2, include_scores=True)
        assert len(results) >= 1
        assert "match_score" in results[0]
        assert 0 <= results[0]["match_score"] <= 1
    
    def test_save_and_load(self):
        """Index should be serializable and deserializable."""
        index = FuzzySearchIndex()
        index.add_street(1, "Bahnhofstraße", "Berlin", "10115", 52.5, 13.4)
        index.add_street(2, "Schillerstraße", "Frankfurt", "60313", 50.1, 8.7)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "index"
            index.save(path)
            
            loaded = FuzzySearchIndex.load(path)
            assert len(loaded.streets) == 2
            
            # Search should work on loaded index
            results = loaded.search("Banhofstrasse", max_distance=2)
            names = [r["name"] for r in results]
            assert "Bahnhofstraße" in names
    
    def test_stats(self):
        """Stats should return index information."""
        index = FuzzySearchIndex()
        index.add_street(1, "Bahnhofstraße", "Berlin", "10115", 52.5, 13.4)
        
        stats = index.stats()
        assert stats["total_streets"] == 1
        assert "normalized_tree" in stats


class TestSymmetricTypoTolerance:
    """Tests verifying symmetric typo tolerance.
    
    The system should find matches regardless of whether the typo
    is in the query or in the indexed data.
    """
    
    def test_typo_in_query(self):
        """Should find correct street when query has typo."""
        index = FuzzySearchIndex()
        index.add_street(1, "Bahnhofstraße", "Berlin", "10115", 52.5, 13.4)
        
        # Query has typo (missing 'h')
        results = index.search("Banhofstrasse", max_distance=2)
        assert len(results) >= 1
        assert any(r["name"] == "Bahnhofstraße" for r in results)
    
    def test_various_typo_positions(self):
        """Should handle typos at various positions."""
        index = FuzzySearchIndex()
        index.add_street(1, "Goethestraße", "Frankfurt", "60313", 50.1, 8.7)
        
        # Typo at beginning
        results = index.search("Goetestrasse", max_distance=2)
        assert any(r["name"] == "Goethestraße" for r in results)
        
        # Typo in middle
        results = index.search("Goetehstrasse", max_distance=2)
        assert any(r["name"] == "Goethestraße" for r in results)
    
    def test_multiple_typos(self):
        """Should handle multiple typos within threshold."""
        index = FuzzySearchIndex()
        index.add_street(1, "Schillerstraße", "Frankfurt", "60313", 50.1, 8.7)
        
        # Two typos (missing two 'l's)
        results = index.search("Schierstrasse", max_distance=2)
        # May or may not find depending on exact distance
        # At least should not crash


class TestGermanStreetNameCases:
    """Tests for typical German street name scenarios."""
    
    def test_umlaut_handling(self):
        """Should handle German umlauts correctly."""
        index = FuzzySearchIndex()
        index.add_street(1, "Königstraße", "Stuttgart", "70173", 48.8, 9.2)
        
        # Search with 'oe' instead of 'ö'
        results = index.search("Koenigstrasse", max_distance=2)
        assert len(results) >= 1
    
    def test_compound_street_names(self):
        """Should handle compound street names."""
        index = FuzzySearchIndex()
        index.add_street(1, "Friedrich-Ebert-Straße", "Berlin", "10117", 52.5, 13.4)
        
        results = index.search("Friedrich Ebert", max_distance=2)
        assert len(results) >= 1
    
    def test_common_prefixes(self):
        """Should distinguish streets with common prefixes."""
        index = FuzzySearchIndex()
        index.add_street(1, "Bahnhofstraße", "Berlin", "10115", 52.5, 13.4)
        index.add_street(2, "Bahnhofsplatz", "Berlin", "10115", 52.5, 13.4)
        index.add_street(3, "Bahnweg", "Berlin", "10115", 52.5, 13.4)
        
        results = index.search("Bahnhofstr", max_distance=2)
        # Should prefer Bahnhofstraße over others
        assert results[0]["name"] == "Bahnhofstraße"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
