"""Fuzzy search module using BK-Tree for typo-tolerant address search.

This module provides a high-performance, fault-tolerant search system that
combines multiple indexing strategies:

1. Normalized text index (BK-Tree with Levenshtein distance)
2. Phonetic index (German + Cologne phonetic codes)
3. Consonant skeleton index
4. Prefix Trie for fast prefix matching

The system provides symmetric typo tolerance - it works regardless of whether
the typo is in the query or the indexed data.
"""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from bktree import BKTree, MultiIndexBKTree
from phonetic import (
    german_phonetic_phrase,
    cologne_phonetic_phrase,
    phonetic_match_score,
    GermanPhonetic,
)
from utils import (
    normalize_string,
    normalize_compact,
    consonant_key,
    calculate_fuzzy_score_normalized,
)


# Default paths
DEFAULT_INDEX_PATH = Path(os.getenv("FUZZY_INDEX_PATH", "./fuzzy_index"))
DEFAULT_BKTREE_PATH = DEFAULT_INDEX_PATH / "bktree"

# Score weights for final ranking (should sum to 1.0)
SCORE_WEIGHT_BASE = 0.45       # Weight for BK-Tree base score
SCORE_WEIGHT_PHONETIC = 0.35  # Weight for phonetic similarity
SCORE_WEIGHT_FUZZY = 0.20     # Weight for fuzzy string matching
PREFIX_MATCH_BONUS = 0.15     # Bonus for exact prefix matches
PARTIAL_PREFIX_BONUS = 0.05   # Bonus for partial prefix matches


class PrefixTrie:
    """A Trie data structure for efficient prefix matching.
    
    This provides O(k) lookup where k is the prefix length,
    instead of O(n) linear scan through all strings.
    """
    
    def __init__(self):
        self.root: Dict = {}
        self._end_marker = '\x00'  # Special marker for end of word
    
    def insert(self, word: str, data: Any) -> None:
        """Insert a word with associated data into the trie."""
        if not word:
            return
        
        node = self.root
        for char in word.lower():
            if char not in node:
                node[char] = {}
            node = node[char]
        
        # Store data at the end of the word
        if self._end_marker not in node:
            node[self._end_marker] = []
        node[self._end_marker].append(data)
    
    def search_prefix(self, prefix: str, limit: int = 100) -> List[Any]:
        """Find all entries that start with the given prefix."""
        if not prefix:
            return []
        
        # Navigate to prefix node
        node = self.root
        for char in prefix.lower():
            if char not in node:
                return []
            node = node[char]
        
        # Collect all data below this node
        results = []
        self._collect_all(node, results, limit)
        return results
    
    def _collect_all(self, node: Dict, results: List, limit: int) -> None:
        """Recursively collect all data entries under a node."""
        if len(results) >= limit:
            return
        
        if self._end_marker in node:
            for data in node[self._end_marker]:
                if len(results) >= limit:
                    return
                results.append(data)
        
        for char, child in node.items():
            if char != self._end_marker:
                self._collect_all(child, results, limit)
                if len(results) >= limit:
                    return


class FuzzySearchIndex:
    """High-performance fuzzy search index for street names.
    
    This class manages multiple BK-Tree indices optimized for different
    search strategies:
    
    1. `normalized`: For Levenshtein distance matching on normalized text
    2. `phonetic_german`: For phonetic similarity (German encoding)
    3. `phonetic_cologne`: For phonetic similarity (Cologne encoding)
    4. `consonant`: For consonant skeleton matching
    5. `prefix_trie`: For fast prefix matching (O(k) instead of O(n))
    
    Example:
        >>> index = FuzzySearchIndex()
        >>> index.add_street(1, "Bahnhofstraße", "Berlin")
        >>> results = index.search("Banhofstrasse", max_distance=2)
        >>> # Results include street_id=1 despite the typo
    """
    
    def __init__(self, path: Optional[Path] = None):
        """Initialize the fuzzy search index.
        
        Args:
            path: Optional path to load existing index from
        """
        self.path = path or DEFAULT_BKTREE_PATH
        
        # Initialize separate BK-Trees for different indexing strategies
        self.normalized_tree = BKTree()
        self.phonetic_german_tree = BKTree()
        self.phonetic_cologne_tree = BKTree()
        self.consonant_tree = BKTree()
        
        # Prefix trie for fast prefix matching
        self.prefix_trie = PrefixTrie()
        
        # Pre-computed normalized names for fast lookup
        self._normalized_cache: Dict[int, str] = {}
        
        # Data store for street metadata
        self.streets: Dict[int, Dict[str, Any]] = {}
        
        # Track index state
        self._is_loaded = False
        self._is_modified = False
    
    def add_street(
        self,
        street_id: int,
        name: str,
        city: str,
        postal_code: Optional[str] = None,
        latitude: float = 0.0,
        longitude: float = 0.0,
        **extra_fields
    ) -> None:
        """Add a street to the index.
        
        Args:
            street_id: Unique identifier for the street
            name: Street name
            city: City name
            postal_code: Optional postal code
            latitude: Street latitude
            longitude: Street longitude
            **extra_fields: Additional fields to store
        """
        if not name:
            return
        
        # Store street data
        self.streets[street_id] = {
            'id': street_id,
            'name': name,
            'city': city,
            'postal_code': postal_code,
            'latitude': latitude,
            'longitude': longitude,
            **extra_fields
        }
        
        # Generate and store index keys
        normalized = normalize_compact(name)
        normalized_search = normalize_string(name)
        phonetic_german = german_phonetic_phrase(name)
        phonetic_cologne = cologne_phonetic_phrase(name)
        cons_key = consonant_key(name)
        
        # Cache normalized name for fast lookup
        self._normalized_cache[street_id] = normalized.lower() if normalized else ""
        
        # Insert into BK-Trees
        if normalized:
            self.normalized_tree.insert(normalized, street_id)
        
        if phonetic_german:
            self.phonetic_german_tree.insert(phonetic_german, street_id)
        
        if phonetic_cologne:
            self.phonetic_cologne_tree.insert(phonetic_cologne, street_id)
        
        if cons_key:
            self.consonant_tree.insert(cons_key, street_id)
        
        # Insert into prefix trie for fast prefix matching
        if normalized:
            self.prefix_trie.insert(normalized, street_id)
        
        self._is_modified = True
    
    def search(
        self,
        query: str,
        max_distance: int = 2,
        city: Optional[str] = None,
        limit: int = 10,
        include_scores: bool = True
    ) -> List[Dict[str, Any]]:
        """Search for streets matching the query with typo tolerance.
        
        This method searches across all indices and combines results
        using a scoring system that considers:
        - Edit distance from normalized query
        - Phonetic similarity
        - Consonant skeleton match
        - Prefix matching (using Trie for O(k) lookup)
        
        Args:
            query: Search query (can contain typos)
            max_distance: Maximum Levenshtein distance to consider
            city: Optional city filter
            limit: Maximum number of results
            include_scores: Whether to include match scores in results
            
        Returns:
            List of street dictionaries with optional match_score
        """
        if not query:
            return []
        
        # Normalize the query once
        query_normalized = normalize_compact(query)
        query_search = normalize_string(query)
        query_german = german_phonetic_phrase(query)
        query_cologne = cologne_phonetic_phrase(query)
        query_cons = consonant_key(query)
        query_lower = query_normalized.lower() if query_normalized else ""
        city_lower = city.lower() if city else None
        
        # Collect candidates from all indices
        candidates: Dict[int, Dict[str, Any]] = {}
        
        # Use Trie for fast prefix matching (O(k) instead of O(n))
        if query_lower:
            prefix_matches = self.prefix_trie.search_prefix(query_lower, limit=limit * 10)
            for street_id in prefix_matches:
                if street_id not in self.streets:
                    continue
                
                # Check city filter
                if city_lower:
                    street_city = self.streets[street_id].get('city', '').lower()
                    if not street_city.startswith(city_lower):
                        continue
                
                self._add_candidate(
                    candidates, street_id,
                    score=1.0,
                    source='prefix',
                    distance=0
                )
        
        # Search normalized tree (BK-Tree fuzzy matching)
        if query_normalized:
            norm_results = self.normalized_tree.search(query_normalized, max_distance)
            for _, street_id, dist in norm_results:
                if street_id is not None:
                    # Check city filter
                    if city_lower and street_id in self.streets:
                        street_city = self.streets[street_id].get('city', '').lower()
                        if not street_city.startswith(city_lower):
                            continue
                    
                    self._add_candidate(
                        candidates, street_id, 
                        score=1.0 - (dist / (max_distance + 1)),
                        source='normalized',
                        distance=dist
                    )
        
        # Search phonetic trees (broader matching)
        phonetic_distance = max_distance + 1  # Phonetic codes are shorter
        
        if query_german:
            german_results = self.phonetic_german_tree.search(query_german, phonetic_distance)
            for _, street_id, dist in german_results:
                if street_id is not None:
                    # Check city filter
                    if city_lower and street_id in self.streets:
                        street_city = self.streets[street_id].get('city', '').lower()
                        if not street_city.startswith(city_lower):
                            continue
                    
                    self._add_candidate(
                        candidates, street_id,
                        score=0.9 - (dist / (phonetic_distance + 1)) * 0.3,
                        source='phonetic_german',
                        distance=dist
                    )
        
        if query_cologne:
            cologne_results = self.phonetic_cologne_tree.search(query_cologne, phonetic_distance)
            for _, street_id, dist in cologne_results:
                if street_id is not None:
                    # Check city filter
                    if city_lower and street_id in self.streets:
                        street_city = self.streets[street_id].get('city', '').lower()
                        if not street_city.startswith(city_lower):
                            continue
                    
                    self._add_candidate(
                        candidates, street_id,
                        score=0.9 - (dist / (phonetic_distance + 1)) * 0.3,
                        source='phonetic_cologne',
                        distance=dist
                    )
        
        # Search consonant tree (fallback for difficult cases)
        if query_cons:
            cons_results = self.consonant_tree.search(query_cons, max_distance)
            for _, street_id, dist in cons_results:
                if street_id is not None:
                    # Check city filter
                    if city_lower and street_id in self.streets:
                        street_city = self.streets[street_id].get('city', '').lower()
                        if not street_city.startswith(city_lower):
                            continue
                    
                    self._add_candidate(
                        candidates, street_id,
                        score=0.8 - (dist / (max_distance + 1)) * 0.3,
                        source='consonant',
                        distance=dist
                    )
        
        # Compute final scores and rank results
        # Limit candidates to avoid processing too many
        max_candidates = limit * 5  # Process at most 5x the limit
        
        results = []
        candidate_list = list(candidates.items())
        
        # Sort by initial score first to prioritize best matches
        candidate_list.sort(key=lambda x: -x[1].get('score', 0))
        
        for street_id, match_info in candidate_list[:max_candidates]:
            street = self.streets.get(street_id)
            if not street:
                continue
            
            # For high-confidence matches (prefix/exact), skip expensive recomputation
            base_score = match_info.get('score', 0.5)
            source = match_info.get('source', '')
            
            if source == 'prefix' and base_score >= 0.95:
                # High confidence prefix match - use base score directly
                final_score = min(1.0, base_score + PREFIX_MATCH_BONUS)
            else:
                # Recompute score with additional phonetic matching
                final_score = self._compute_final_score(
                    query, query_search, street, match_info
                )
            
            result = {**street, 'match_score': final_score} if include_scores else {**street}
            results.append((final_score, result))
        
        # Sort by score (descending) and return top results
        results.sort(key=lambda x: (-x[0], x[1].get('name', '')))
        return [r[1] for r in results[:limit]]
    
    def search_prefix(
        self,
        query: str,
        city: Optional[str] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Search for streets starting with the query prefix.
        
        This uses the Trie for O(k) lookup where k is the prefix length.
        Useful for autocomplete as the user types.
        
        Args:
            query: Search query prefix
            city: Optional city filter
            limit: Maximum number of results
            
        Returns:
            List of matching street dictionaries
        """
        if not query:
            return []
        
        query_normalized = normalize_compact(query).lower()
        city_lower = city.lower() if city else None
        
        # Use Trie for fast prefix lookup
        prefix_matches = self.prefix_trie.search_prefix(query_normalized, limit=limit * 5)
        
        results = []
        for street_id in prefix_matches:
            street = self.streets.get(street_id)
            if not street:
                continue
            
            # Apply city filter
            if city_lower:
                if not street.get('city', '').lower().startswith(city_lower):
                    continue
            
            # Use cached normalized name
            name_normalized = self._normalized_cache.get(street_id, '')
            score = 1.0 if name_normalized == query_normalized else 0.95
            results.append((score, {**street, 'match_score': score}))
        
        results.sort(key=lambda x: (-x[0], x[1].get('name', '')))
        return [r[1] for r in results[:limit]]
    
    def _add_candidate(
        self,
        candidates: Dict[int, Dict[str, Any]],
        street_id: int,
        score: float,
        source: str,
        distance: int
    ) -> None:
        """Add or update a candidate in the results."""
        if street_id in candidates:
            # Keep the best score
            if score > candidates[street_id].get('score', 0):
                candidates[street_id] = {
                    'score': score,
                    'source': source,
                    'distance': distance
                }
        else:
            candidates[street_id] = {
                'score': score,
                'source': source,
                'distance': distance
            }
    
    def _compute_final_score(
        self,
        query: str,
        query_search: str,
        street: Dict[str, Any],
        match_info: Dict[str, Any]
    ) -> float:
        """Compute final match score combining all signals.
        
        This is optimized to avoid redundant computations.
        """
        base_score = match_info.get('score', 0.5)
        
        # Use cached normalized name if available
        street_id = street.get('id')
        street_name = street.get('name', '')
        
        # For high base scores, use simplified scoring
        if base_score >= 0.9:
            # Quick check for prefix match bonus
            street_compact = self._normalized_cache.get(street_id, '')
            if not street_compact:
                street_compact = normalize_compact(street_name).lower()
            
            query_compact = normalize_compact(query).lower()
            if street_compact.startswith(query_compact):
                return min(1.0, base_score + PREFIX_MATCH_BONUS)
            return base_score
        
        # Full scoring for lower confidence matches
        phonetic_score = phonetic_match_score(query, street_name)
        
        # Add fuzzy score on normalized names
        street_search = normalize_string(street_name)
        _, fuzzy_score = calculate_fuzzy_score_normalized(query_search, street_search)
        
        # Weighted combination using configured weights
        final_score = (
            SCORE_WEIGHT_BASE * base_score +
            SCORE_WEIGHT_PHONETIC * phonetic_score +
            SCORE_WEIGHT_FUZZY * fuzzy_score
        )
        
        # Boost for exact prefix matches
        street_compact = self._normalized_cache.get(street_id, '')
        if not street_compact:
            street_compact = normalize_compact(street_name).lower()
        
        query_compact = normalize_compact(query).lower()
        
        if street_compact.startswith(query_compact):
            final_score = min(1.0, final_score + PREFIX_MATCH_BONUS)
        elif query_compact.startswith(street_compact[:len(query_compact)]):
            final_score = min(1.0, final_score + PARTIAL_PREFIX_BONUS)
        
        return min(1.0, max(0.0, final_score))
    
    def save(self, path: Optional[Path] = None) -> None:
        """Save the index to disk.
        
        Args:
            path: Optional path to save to (uses default if not specified)
        """
        save_path = Path(path) if path else self.path
        save_path.mkdir(parents=True, exist_ok=True)
        
        # Save each BK-Tree
        self.normalized_tree.save(save_path / "normalized.bktree")
        self.phonetic_german_tree.save(save_path / "phonetic_german.bktree")
        self.phonetic_cologne_tree.save(save_path / "phonetic_cologne.bktree")
        self.consonant_tree.save(save_path / "consonant.bktree")
        
        # Save street data
        import pickle
        with open(save_path / "streets.pkl", 'wb') as f:
            pickle.dump(self.streets, f, protocol=pickle.HIGHEST_PROTOCOL)
        
        self._is_modified = False
    
    @classmethod
    def load(cls, path: Optional[Path] = None) -> "FuzzySearchIndex":
        """Load an index from disk.
        
        Args:
            path: Optional path to load from (uses default if not specified)
            
        Returns:
            Loaded FuzzySearchIndex instance
        """
        load_path = Path(path) if path else DEFAULT_BKTREE_PATH
        
        if not load_path.exists():
            # Return empty index if path doesn't exist
            return cls(path=load_path)
        
        instance = cls(path=load_path)
        
        # Load BK-Trees if they exist
        if (load_path / "normalized.bktree").exists():
            instance.normalized_tree = BKTree.load(load_path / "normalized.bktree")
        
        if (load_path / "phonetic_german.bktree").exists():
            instance.phonetic_german_tree = BKTree.load(load_path / "phonetic_german.bktree")
        
        if (load_path / "phonetic_cologne.bktree").exists():
            instance.phonetic_cologne_tree = BKTree.load(load_path / "phonetic_cologne.bktree")
        
        if (load_path / "consonant.bktree").exists():
            instance.consonant_tree = BKTree.load(load_path / "consonant.bktree")
        
        # Load street data
        import pickle
        streets_path = load_path / "streets.pkl"
        if streets_path.exists():
            with open(streets_path, 'rb') as f:
                instance.streets = pickle.load(f)
        
        # Rebuild prefix trie and normalized cache from loaded streets
        for street_id, street in instance.streets.items():
            name = street.get('name', '')
            if name:
                normalized = normalize_compact(name)
                if normalized:
                    instance.prefix_trie.insert(normalized, street_id)
                    instance._normalized_cache[street_id] = normalized.lower()
        
        instance._is_loaded = True
        instance._is_modified = False
        
        return instance
    
    def stats(self) -> Dict[str, Any]:
        """Get statistics about the index.
        
        Returns:
            Dictionary with index statistics
        """
        return {
            'total_streets': len(self.streets),
            'normalized_tree': self.normalized_tree.stats(),
            'phonetic_german_tree': self.phonetic_german_tree.stats(),
            'phonetic_cologne_tree': self.phonetic_cologne_tree.stats(),
            'consonant_tree': self.consonant_tree.stats(),
            'is_loaded': self._is_loaded,
            'is_modified': self._is_modified
        }
    
    def clear(self) -> None:
        """Clear all data from the index."""
        self.normalized_tree = BKTree()
        self.phonetic_german_tree = BKTree()
        self.phonetic_cologne_tree = BKTree()
        self.consonant_tree = BKTree()
        self.prefix_trie = PrefixTrie()
        self._normalized_cache.clear()
        self.streets.clear()
        self._is_modified = True


# Global singleton for the fuzzy search index
_global_index: Optional[FuzzySearchIndex] = None


def get_fuzzy_index() -> FuzzySearchIndex:
    """Get the global fuzzy search index (lazy loaded).
    
    Returns:
        The global FuzzySearchIndex instance
    """
    global _global_index
    
    if _global_index is None:
        if DEFAULT_BKTREE_PATH.exists():
            _global_index = FuzzySearchIndex.load()
        else:
            _global_index = FuzzySearchIndex()
    
    return _global_index


def fuzzy_search_streets(
    query: str,
    max_distance: int = 2,
    city: Optional[str] = None,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """Convenience function for fuzzy street search.
    
    Args:
        query: Search query (can contain typos)
        max_distance: Maximum edit distance
        city: Optional city filter
        limit: Maximum results
        
    Returns:
        List of matching street dictionaries
    """
    index = get_fuzzy_index()
    return index.search(query, max_distance, city, limit)
