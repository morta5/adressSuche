"""BK-Tree implementation for efficient typo-tolerant search using Levenshtein distance.

A BK-Tree (Burkhard-Keller Tree) is a tree structure optimized for finding
similar strings using a metric distance function (like Levenshtein distance).
It enables efficient fuzzy matching with symmetric error tolerance.

Key features:
- O(log n) average lookup time for typo-tolerant search
- Symmetric error handling (works regardless of which side has the typo)
- Configurable maximum edit distance threshold
- Memory efficient serialization for persistence
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


def levenshtein_distance(s1: str, s2: str, max_dist: int = 10) -> int:
    """Calculate Levenshtein distance with early termination.
    
    Args:
        s1: First string
        s2: Second string
        max_dist: Maximum distance to compute (early termination optimization)
        
    Returns:
        Levenshtein distance, or max_dist + 1 if exceeds threshold
    """
    if s1 == s2:
        return 0
    
    len1, len2 = len(s1), len(s2)
    
    # Quick rejection based on length difference
    if abs(len1 - len2) > max_dist:
        return max_dist + 1
    
    # Ensure s1 is the shorter string
    if len1 > len2:
        s1, s2 = s2, s1
        len1, len2 = len2, len1
    
    # Use two rows instead of full matrix for memory efficiency
    prev_row = list(range(len2 + 1))
    curr_row = [0] * (len2 + 1)
    
    for i in range(1, len1 + 1):
        curr_row[0] = i
        row_min = i
        
        for j in range(1, len2 + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            curr_row[j] = min(
                prev_row[j] + 1,      # deletion
                curr_row[j - 1] + 1,  # insertion
                prev_row[j - 1] + cost  # substitution
            )
            row_min = min(row_min, curr_row[j])
        
        # Early termination if minimum exceeds threshold
        if row_min > max_dist:
            return max_dist + 1
        
        prev_row, curr_row = curr_row, prev_row
    
    return prev_row[len2]


@dataclass
class BKTreeNode:
    """A node in the BK-Tree.
    
    Attributes:
        word: The normalized string stored at this node
        data: Optional metadata associated with this word (e.g., street_id)
        children: Dict mapping edit distances to child nodes
    """
    word: str
    data: Optional[Any] = None
    children: Dict[int, "BKTreeNode"] = field(default_factory=dict)


class BKTree:
    """BK-Tree for efficient typo-tolerant string matching.
    
    This implementation supports:
    - Fast fuzzy lookups using Levenshtein distance
    - Storing associated metadata with each entry
    - Serialization/deserialization for persistence
    - Batch operations for efficient building
    
    Example:
        >>> tree = BKTree()
        >>> tree.insert("bahnhof", {"id": 1})
        >>> tree.insert("hauptbahnhof", {"id": 2})
        >>> results = tree.search("banhof", max_distance=1)
        >>> print(results)  # [("bahnhof", {"id": 1}, 1)]
    """
    
    def __init__(
        self,
        distance_fn: Callable[[str, str, int], int] = levenshtein_distance
    ):
        """Initialize the BK-Tree.
        
        Args:
            distance_fn: Distance function to use. Must be a metric (symmetric,
                        triangle inequality). Defaults to Levenshtein distance.
        """
        self.root: Optional[BKTreeNode] = None
        self.distance_fn = distance_fn
        self._size = 0
    
    def __len__(self) -> int:
        """Return the number of entries in the tree."""
        return self._size
    
    def insert(self, word: str, data: Optional[Any] = None) -> None:
        """Insert a word with optional metadata into the tree.
        
        Args:
            word: The string to insert (should be normalized)
            data: Optional metadata to associate with the word
        """
        if not word:
            return
        
        if self.root is None:
            self.root = BKTreeNode(word=word, data=data)
            self._size = 1
            return
        
        node = self.root
        while True:
            dist = self.distance_fn(word, node.word, max_dist=100)
            
            if dist == 0:
                # Word already exists, update data if provided
                if data is not None:
                    node.data = data
                return
            
            if dist in node.children:
                node = node.children[dist]
            else:
                node.children[dist] = BKTreeNode(word=word, data=data)
                self._size += 1
                return
    
    def search(
        self,
        query: str,
        max_distance: int = 2
    ) -> List[Tuple[str, Optional[Any], int]]:
        """Search for words within the specified edit distance of the query.
        
        This is the core fuzzy search operation. It finds all entries in the
        tree that are within max_distance edits of the query string.
        
        Args:
            query: The search query (should be normalized)
            max_distance: Maximum allowed edit distance
            
        Returns:
            List of tuples (word, data, distance) sorted by distance
        """
        if self.root is None or not query:
            return []
        
        results: List[Tuple[str, Optional[Any], int]] = []
        candidates: List[BKTreeNode] = [self.root]
        
        while candidates:
            node = candidates.pop()
            dist = self.distance_fn(query, node.word, max_distance + 1)
            
            if dist <= max_distance:
                results.append((node.word, node.data, dist))
            
            # BK-Tree property: candidates are in range [dist - max_distance, dist + max_distance]
            # This pruning is what makes BK-Trees efficient
            low = max(0, dist - max_distance)
            high = dist + max_distance
            
            for d in range(low, high + 1):
                child = node.children.get(d)
                if child is not None:
                    candidates.append(child)
        
        # Sort by distance, then alphabetically
        results.sort(key=lambda x: (x[2], x[0]))
        return results
    
    def search_best(
        self,
        query: str,
        max_distance: int = 2,
        limit: int = 10
    ) -> List[Tuple[str, Optional[Any], int]]:
        """Search and return the best matching results.
        
        Similar to search(), but optimized for returning top results.
        
        Args:
            query: The search query (should be normalized)
            max_distance: Maximum allowed edit distance
            limit: Maximum number of results to return
            
        Returns:
            List of tuples (word, data, distance) sorted by distance, limited
        """
        results = self.search(query, max_distance)
        return results[:limit]
    
    def contains(self, word: str) -> bool:
        """Check if the exact word exists in the tree.
        
        Args:
            word: The word to check
            
        Returns:
            True if the word exists, False otherwise
        """
        matches = self.search(word, max_distance=0)
        return len(matches) > 0
    
    def build_from_list(
        self,
        items: List[Tuple[str, Optional[Any]]]
    ) -> None:
        """Bulk insert items into the tree.
        
        More efficient than individual inserts for large datasets.
        
        Args:
            items: List of (word, data) tuples to insert
        """
        for word, data in items:
            if word:
                self.insert(word, data)
    
    def save(self, path: Path | str) -> None:
        """Save the tree to a file.
        
        Args:
            path: File path to save to
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, 'wb') as f:
            pickle.dump(self._serialize(), f, protocol=pickle.HIGHEST_PROTOCOL)
    
    @classmethod
    def load(cls, path: Path | str) -> "BKTree":
        """Load a tree from a file.
        
        Args:
            path: File path to load from
            
        Returns:
            Loaded BKTree instance
        """
        with open(path, 'rb') as f:
            data = pickle.load(f)
        
        tree = cls()
        tree._deserialize(data)
        return tree
    
    def _serialize(self) -> Dict:
        """Serialize tree to a dictionary."""
        return {
            'root': self._serialize_node(self.root) if self.root else None,
            'size': self._size
        }
    
    def _serialize_node(self, node: Optional[BKTreeNode]) -> Optional[Dict]:
        """Serialize a single node."""
        if node is None:
            return None
        
        return {
            'word': node.word,
            'data': node.data,
            'children': {
                dist: self._serialize_node(child)
                for dist, child in node.children.items()
            }
        }
    
    def _deserialize(self, data: Dict) -> None:
        """Deserialize tree from a dictionary."""
        self._size = data.get('size', 0)
        self.root = self._deserialize_node(data.get('root'))
    
    def _deserialize_node(self, data: Optional[Dict]) -> Optional[BKTreeNode]:
        """Deserialize a single node."""
        if data is None:
            return None
        
        node = BKTreeNode(
            word=data['word'],
            data=data.get('data')
        )
        
        for dist, child_data in data.get('children', {}).items():
            child = self._deserialize_node(child_data)
            if child is not None:
                node.children[int(dist)] = child
        
        return node
    
    def stats(self) -> Dict[str, Any]:
        """Get statistics about the tree structure.
        
        Returns:
            Dictionary with tree statistics
        """
        if self.root is None:
            return {'size': 0, 'depth': 0, 'avg_children': 0}
        
        depths: List[int] = []
        child_counts: List[int] = []
        
        def traverse(node: BKTreeNode, depth: int) -> None:
            depths.append(depth)
            child_counts.append(len(node.children))
            for child in node.children.values():
                traverse(child, depth + 1)
        
        traverse(self.root, 0)
        
        return {
            'size': self._size,
            'depth': max(depths) if depths else 0,
            'avg_children': sum(child_counts) / len(child_counts) if child_counts else 0,
            'max_children': max(child_counts) if child_counts else 0
        }


class MultiIndexBKTree:
    """A collection of BK-Trees for searching multiple string fields.
    
    This class maintains separate BK-Trees for different indexing strategies
    (e.g., normalized name, phonetic code) and combines their results.
    
    Example:
        >>> multi = MultiIndexBKTree()
        >>> multi.add_index('name', normalize_fn=str.lower)
        >>> multi.add_index('phonetic', normalize_fn=phonetic_encode)
        >>> multi.insert({'id': 1, 'name': 'Bahnhof'})
        >>> results = multi.search('banhof', max_distance=1)
    """
    
    def __init__(self):
        """Initialize the multi-index structure."""
        self.indices: Dict[str, Tuple[BKTree, Callable[[str], str]]] = {}
        self.data_store: Dict[str, Any] = {}
        self._id_counter = 0
    
    def add_index(
        self,
        name: str,
        normalize_fn: Callable[[str], str] = lambda x: x.lower()
    ) -> None:
        """Add a new index to the multi-index structure.
        
        Args:
            name: Name of the index
            normalize_fn: Function to normalize values before indexing
        """
        self.indices[name] = (BKTree(), normalize_fn)
    
    def insert(
        self,
        data: Dict[str, Any],
        id_field: str = 'id'
    ) -> str:
        """Insert a record into all indices.
        
        Args:
            data: Dictionary containing the record data
            id_field: Name of the ID field in the data
            
        Returns:
            The record ID (generated if not present)
        """
        # Generate or use existing ID
        if id_field in data:
            record_id = str(data[id_field])
        else:
            self._id_counter += 1
            record_id = str(self._id_counter)
        
        # Store the full data
        self.data_store[record_id] = data
        
        # Index in each BK-Tree
        for index_name, (tree, normalize_fn) in self.indices.items():
            if index_name in data:
                value = data[index_name]
                if value:
                    normalized = normalize_fn(str(value))
                    if normalized:
                        tree.insert(normalized, record_id)
        
        return record_id
    
    def search(
        self,
        query: str,
        max_distance: int = 2,
        index_name: Optional[str] = None,
        limit: int = 10
    ) -> List[Tuple[Dict[str, Any], int]]:
        """Search across indices for matching records.
        
        Args:
            query: The search query
            max_distance: Maximum edit distance
            index_name: Specific index to search (None = search all)
            limit: Maximum results to return
            
        Returns:
            List of (record, best_distance) tuples
        """
        results: Dict[str, int] = {}  # record_id -> best distance
        
        indices_to_search = (
            [(index_name, self.indices[index_name])]
            if index_name and index_name in self.indices
            else list(self.indices.items())
        )
        
        for name, (tree, normalize_fn) in indices_to_search:
            normalized_query = normalize_fn(query)
            if not normalized_query:
                continue
            
            matches = tree.search(normalized_query, max_distance)
            
            for _, record_id, dist in matches:
                if record_id is not None:
                    if record_id not in results or dist < results[record_id]:
                        results[record_id] = dist
        
        # Sort by distance and return records
        sorted_results = sorted(results.items(), key=lambda x: x[1])[:limit]
        
        return [
            (self.data_store[record_id], dist)
            for record_id, dist in sorted_results
            if record_id in self.data_store
        ]
    
    def save(self, path: Path | str) -> None:
        """Save all indices to a directory.
        
        Args:
            path: Directory path to save to
        """
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        
        # Save each index
        for name, (tree, _) in self.indices.items():
            tree.save(path / f"{name}.bktree")
        
        # Save data store and metadata
        metadata = {
            'data_store': self.data_store,
            'id_counter': self._id_counter,
            'index_names': list(self.indices.keys())
        }
        with open(path / "metadata.pkl", 'wb') as f:
            pickle.dump(metadata, f, protocol=pickle.HIGHEST_PROTOCOL)
    
    @classmethod
    def load(
        cls,
        path: Path | str,
        normalize_fns: Dict[str, Callable[[str], str]] = None
    ) -> "MultiIndexBKTree":
        """Load multi-index from a directory.
        
        Args:
            path: Directory path to load from
            normalize_fns: Dict mapping index names to normalization functions
            
        Returns:
            Loaded MultiIndexBKTree instance
        """
        path = Path(path)
        normalize_fns = normalize_fns or {}
        
        # Load metadata
        with open(path / "metadata.pkl", 'rb') as f:
            metadata = pickle.load(f)
        
        instance = cls()
        instance.data_store = metadata.get('data_store', {})
        instance._id_counter = metadata.get('id_counter', 0)
        
        # Load each index
        for name in metadata.get('index_names', []):
            tree = BKTree.load(path / f"{name}.bktree")
            normalize_fn = normalize_fns.get(name, lambda x: x.lower())
            instance.indices[name] = (tree, normalize_fn)
        
        return instance
