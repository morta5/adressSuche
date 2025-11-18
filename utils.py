"""Utility functions for the autocomplete API."""

import math
import re
from typing import List, Tuple


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great circle distance between two points on the earth (specified in decimal degrees).

    Args:
        lat1: Latitude of point 1
        lon1: Longitude of point 1
        lat2: Latitude of point 2
        lon2: Longitude of point 2

    Returns:
        Distance in kilometers
    """
    # Convert decimal degrees to radians
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)

    # Haversine formula
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(a))

    # Radius of earth in kilometers
    r = 6371

    return c * r


def levenshtein_distance(s1: str, s2: str, max_distance: int = 3) -> int:
    """
    Calculate Levenshtein distance between two strings with early termination.
    Optimized for performance - stops calculation if distance exceeds max_distance.

    Args:
        s1: First string
        s2: Second string
        max_distance: Maximum distance to calculate (early termination)

    Returns:
        Levenshtein distance or max_distance + 1 if exceeds threshold
    """
    if abs(len(s1) - len(s2)) > max_distance:
        return max_distance + 1

    if len(s1) > len(s2):
        s1, s2 = s2, s1

    # Use only two rows for memory efficiency
    prev_row = list(range(len(s2) + 1))
    curr_row = [0] * (len(s2) + 1)

    for i in range(1, len(s1) + 1):
        curr_row[0] = i
        min_val = i

        for j in range(1, len(s2) + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            curr_row[j] = min(
                prev_row[j] + 1,  # deletion
                curr_row[j - 1] + 1,  # insertion
                prev_row[j - 1] + cost,  # substitution
            )
            min_val = min(min_val, curr_row[j])

        # Early termination if minimum in current row exceeds threshold
        if min_val > max_distance:
            return max_distance + 1

        prev_row, curr_row = curr_row, prev_row

    return prev_row[len(s2)]


def normalize_string(s: str) -> str:
    """
    Normalize string for fuzzy matching - remove special chars, convert to lowercase.

    Args:
        s: Input string

    Returns:
        Normalized string
    """
    # Convert to lowercase and remove common German characters
    s = s.lower()
    s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    # Remove special characters but keep spaces and hyphens
    s = re.sub(r"[^\w\s\-]", "", s)
    # Normalize multiple spaces/hyphens to single space
    s = re.sub(r"[\s\-]+", " ", s)
    return s.strip()


def normalize_compact(s: str) -> str:
    """Return normalized string without spaces or hyphens for indexing/comparison."""

    normalized = normalize_string(s)
    return normalized.replace(" ", "").replace("-", "")


def consonant_key(s: str) -> str:
    """Return a compact consonant skeleton for robust matching.

    - Lowercases and normalizes umlauts.
    - Removes spaces/hyphens and most vowels (a,e,i,o,u,y) and mute 'h'.
    - Keeps the first character of each token and collapses duplicates.
    """

    n = normalize_string(s)
    if not n:
        return ""
    parts = n.split()

    def _token_key(tok: str) -> str:
        if not tok:
            return ""
        first = tok[0]
        rest = tok[1:]
        rest = re.sub(r"[aeiouyh]", "", rest)
        key = first + rest
        # collapse consecutive duplicates
        out = []
        prev = ""
        for ch in key:
            if ch != prev:
                out.append(ch)
                prev = ch
        return "".join(out)

    keys = [_token_key(p) for p in parts if p]
    return "".join(keys)


def generate_fuzzy_variants(query: str) -> List[str]:
    """
    Generate common typo variants for a query string.

    Args:
        query: Original query string

    Returns:
        List of potential typo variants
    """
    normalized = normalize_string(query)
    variants = [normalized]

    # Generate variants for common typos
    if len(normalized) >= 3:
        # Character swaps (transpose adjacent characters)
        for i in range(len(normalized) - 1):
            variant = list(normalized)
            variant[i], variant[i + 1] = variant[i + 1], variant[i]
            variants.append("".join(variant))

        # Single character deletions
        for i in range(len(normalized)):
            variant = normalized[:i] + normalized[i + 1 :]
            if len(variant) >= 2:
                variants.append(variant)

    # Remove duplicates while preserving order
    seen = set()
    unique_variants = []
    for variant in variants:
        if variant not in seen:
            seen.add(variant)
            unique_variants.append(variant)

    return unique_variants


def _calculate_fuzzy_score_core(
    query_norm: str, street_norm: str, max_distance: int = 2
) -> Tuple[int, float]:
    # Exact match gets best score
    if query_norm == street_norm:
        return 0, 1.0

    # Prefix match gets high score
    if street_norm.startswith(query_norm):
        return 0, 0.95

    # Word-based matching for multi-word streets
    query_words = query_norm.split()
    street_words = street_norm.split()

    if len(query_words) > 1 and len(street_words) > 1:
        # Check if all query words have close matches in street words
        total_distance = 0
        matched_words = 0

        for query_word in query_words:
            best_word_distance = max_distance + 1
            for street_word in street_words:
                if street_word.startswith(query_word):
                    best_word_distance = 0
                    break
                else:
                    distance = levenshtein_distance(
                        query_word, street_word, max_distance
                    )
                    best_word_distance = min(best_word_distance, distance)

            if best_word_distance <= max_distance:
                total_distance += best_word_distance
                matched_words += 1

        if matched_words == len(query_words):
            avg_distance = total_distance / len(query_words)
            similarity = max(0, 1.0 - (avg_distance / max_distance)) * 0.9
            return int(avg_distance), similarity

    # Single string comparison
    distance = levenshtein_distance(query_norm, street_norm, max_distance)
    if distance <= max_distance:
        # Calculate similarity score (0.0 to 0.8 for fuzzy matches)
        max_len = max(len(query_norm), len(street_norm))
        similarity = max(0, 1.0 - (distance / max_len)) * 0.8
        return distance, similarity

    return max_distance + 1, 0.0


def calculate_fuzzy_score(
    query: str, street_name: str, max_distance: int = 2
) -> Tuple[int, float]:
    """
    Calculate fuzzy matching score between query and street name.

    Args:
        query: Search query
        street_name: Street name to match against
        max_distance: Maximum allowed Levenshtein distance

    Returns:
        Tuple of (distance, similarity_score) where lower distance and higher score is better
    """
    query_norm = normalize_string(query)
    street_norm = normalize_string(street_name)
    return _calculate_fuzzy_score_core(query_norm, street_norm, max_distance)


def calculate_fuzzy_score_normalized(
    query_norm: str, street_norm: str, max_distance: int = 2
) -> Tuple[int, float]:
    """Calculate fuzzy score when both arguments are already normalized."""

    return _calculate_fuzzy_score_core(query_norm, street_norm, max_distance)


def generate_hyphen_variants(query: str) -> List[str]:
    """
    Generate street name variants by converting between spaces and hyphens.

    This helps find streets that are written with different hyphenation styles:
    - "albert schweitzer straße" -> "albert-schweitzer-straße"
    - "albert-schweitzer-straße" -> "albert schweitzer straße"

    Args:
        query: Original search query

    Returns:
        List of query variants with different hyphenation
    """
    query_lower = query.lower().strip()
    if len(query_lower) < 3:
        return [query]

    variants = [query]

    # Convert spaces to hyphens
    if " " in query_lower:
        hyphenated = query_lower.replace(" ", "-")
        if hyphenated != query_lower:
            variants.append(hyphenated)

    # Convert hyphens to spaces
    if "-" in query_lower:
        spaced = query_lower.replace("-", " ")
        if spaced != query_lower:
            variants.append(spaced)

    return variants


def generate_street_suffix_variants(query: str) -> List[str]:
    """
    Generate street name variants by expanding common German street suffixes.

    Args:
        query: Original search query

    Returns:
        List of query variants with expanded suffixes
    """
    query_lower = query.lower().strip()
    if len(query_lower) < 2:
        return [query]

    variants = [query]

    # Define common German street suffixes and their expansions
    suffix_expansions = {
        # Straße variants
        "s": ["straße"],
        "st": ["straße"],
        "str": ["straße"],
        "stra": ["straße"],
        "straß": ["straße"],
        "strass": ["straße"],
        "strasse": ["straße"],
        # Weg variants
        "w": ["weg"],
        "we": ["weg"],
        # Allee variants
        "al": ["allee"],
        "all": ["allee"],
        "alle": ["allee"],
        # Platz variants
        "pl": ["platz"],
        "pla": ["platz"],
        "plat": ["platz"],
        # Gasse variants
        "g": ["gasse"],
        "ga": ["gasse"],
        "gas": ["gasse"],
        "gass": ["gasse"],
        # Ring variants
        "r": ["ring"],
        "ri": ["ring"],
        "rin": ["ring"],
        # Damm variants
        "d": ["damm"],
        "da": ["damm"],
        "dam": ["damm"],
        # Hof variants
        "ho": ["hof"],
        # Park variants
        "par": ["park"],
        # Berg variants
        "ber": ["berg"],
        # Additional common endings
        "brück": ["brücke"],
        "brucke": ["brücke"],
        "kirch": ["kirche"],
        "markt": ["markt"],
        "tor": ["tor"],
        "bad": ["bad"],
        "feld": ["feld"],
        "grund": ["grund"],
        "hang": ["hang"],
        "tal": ["tal"],
        "wall": ["wall"],
        "steig": ["steig"],
        "pfad": ["pfad"],
        "winkel": ["winkel"],
    }

    # Check for suffix matches and generate variants
    for suffix, expansions in suffix_expansions.items():
        if query_lower.endswith(suffix):
            # Remove the suffix and add each expansion
            base = query_lower[: -len(suffix)]
            if len(base) >= 2:  # Ensure meaningful base
                for expansion in expansions:
                    variant = base + expansion
                    if variant != query_lower:  # Don't add identical variants
                        variants.append(variant)

    # Also check for common word boundaries in multi-word streets
    words = query_lower.split()
    if len(words) > 1:
        last_word = words[-1]
        if last_word in suffix_expansions:
            for expansion in suffix_expansions[last_word]:
                new_words = words[:-1] + [expansion]
                variant = " ".join(new_words)
                if variant != query_lower:
                    variants.append(variant)

    # Remove duplicates while preserving order
    seen = set()
    unique_variants = []
    for variant in variants:
        variant_normalized = normalize_string(variant)
        if variant_normalized not in seen:
            seen.add(variant_normalized)
            unique_variants.append(variant)

    return unique_variants


def should_expand_suffix(query: str) -> bool:
    """
    Determine if a query should be expanded with street suffixes.

    Args:
        query: Search query

    Returns:
        True if the query looks like it could benefit from suffix expansion
    """
    query_lower = query.lower().strip()

    # Don't expand very short queries
    if len(query_lower) < 3:
        return False

    # Don't expand if it already looks like a complete street name
    complete_suffixes = [
        "straße",
        "strasse",
        "weg",
        "allee",
        "platz",
        "gasse",
        "ring",
        "damm",
        "hof",
        "park",
        "berg",
        "brücke",
        "brucke",
        "kirche",
        "markt",
        "tor",
        "bad",
        "feld",
        "grund",
        "hang",
        "tal",
        "wall",
        "steig",
        "pfad",
        "winkel",
    ]

    if any(query_lower.endswith(suffix) for suffix in complete_suffixes):
        return False

    # Expand if it ends with a common partial suffix
    partial_suffixes = [
        "s",
        "st",
        "str",
        "stra",
        "straß",
        "strass",
        "w",
        "we",
        "al",
        "all",
        "alle",
        "pl",
        "pla",
        "plat",
        "g",
        "ga",
        "gas",
        "gass",
        "r",
        "ri",
        "rin",
        "d",
        "da",
        "dam",
        "ho",
        "par",
        "ber",
        "brück",
        "kirch",
    ]

    return any(query_lower.endswith(suffix) for suffix in partial_suffixes)
