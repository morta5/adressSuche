"""Utility functions for the autocomplete API."""

import math
import re
from typing import List, Tuple, Optional


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


def point_to_segment_distance(
    point_lat: float, point_lon: float,
    seg_start_lat: float, seg_start_lon: float,
    seg_end_lat: float, seg_end_lon: float
) -> Tuple[float, float, float]:
    """
    Calculate the shortest distance from a point to a line segment.
    
    Returns the distance in km and the coordinates of the closest point on the segment.
    Uses a flat-earth approximation which is accurate enough for short distances.
    
    Args:
        point_lat, point_lon: The query point coordinates
        seg_start_lat, seg_start_lon: Start of the segment
        seg_end_lat, seg_end_lon: End of the segment
    
    Returns:
        Tuple of (distance_km, closest_lat, closest_lon)
    """
    # For the projection calculation, we scale longitude by cos(lat) to account
    # for the convergence of meridians at higher latitudes.
    avg_lat = (point_lat + seg_start_lat + seg_end_lat) / 3
    cos_lat = math.cos(math.radians(avg_lat))
    
    # Scale longitude coordinates by cos(lat) for more accurate flat-earth projection
    px = point_lon * cos_lat
    py = point_lat
    ax = seg_start_lon * cos_lat
    ay = seg_start_lat
    bx = seg_end_lon * cos_lat
    by = seg_end_lat
    
    # Vector from a to b
    abx = bx - ax
    aby = by - ay
    
    # Vector from a to p
    apx = px - ax
    apy = py - ay
    
    # Calculate the projection of ap onto ab
    ab_squared = abx * abx + aby * aby
    
    if ab_squared < 1e-12:  # Segment is essentially a point
        return haversine_distance(point_lat, point_lon, seg_start_lat, seg_start_lon), seg_start_lat, seg_start_lon
    
    # t is the parameter along the line segment [0, 1]
    t = (apx * abx + apy * aby) / ab_squared
    
    # Clamp t to [0, 1] to stay on the segment
    t = max(0.0, min(1.0, t))
    
    # Find the closest point on the segment
    # Note: closest_x is still scaled by cos_lat
    closest_x = ax + t * abx
    closest_lat = ay + t * aby
    
    # Unscale longitude to get back to degrees
    closest_lon = closest_x / cos_lat if abs(cos_lat) > 1e-6 else point_lon
    
    # Calculate distance using haversine for accuracy
    distance = haversine_distance(point_lat, point_lon, closest_lat, closest_lon)
    
    return distance, closest_lat, closest_lon


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


def normalize_city_for_matching(city: str) -> str:
    """
    Normalize city name for flexible matching.
    
    Removes hyphens, spaces, and administrative prefixes to allow matching variations like:
    - "Henstedt Ulzburg" with "Henstedt-Ulzburg"
    - "Kreis Plön" with "Plön"
    - "Stadt Hamburg" with "Hamburg"
    
    Args:
        city: City name to normalize
        
    Returns:
        Normalized city name (lowercase, no spaces/hyphens, umlauts converted, no admin prefixes)
    """
    # Remove common administrative prefixes
    city_clean = city
    admin_prefixes = ['kreis ', 'stadt ', 'gemeinde ', 'hansestadt ', 'landkreis ']
    city_lower = city.lower()
    for prefix in admin_prefixes:
        if city_lower.startswith(prefix):
            city_clean = city[len(prefix):]
            break
    
    return normalize_compact(city_clean)


def generate_city_variations(city: str) -> List[str]:
    """
    Generate city name variations for flexible matching in SQL queries.
    
    Handles cases like:
    - "Henstedt Ulzburg" -> ["Henstedt Ulzburg", "Henstedt-Ulzburg"]
    - "Kreis Plön" -> ["Kreis Plön", "Plön"]
    
    Args:
        city: City name to generate variations for
        
    Returns:
        List of city name variations to try in SQL queries
    """
    variations = [city]
    
    # Add space/hyphen variations
    if ' ' in city:
        variations.append(city.replace(' ', '-'))
    if '-' in city:
        variations.append(city.replace('-', ' '))
    
    # Remove administrative prefixes
    admin_prefixes = ['kreis ', 'stadt ', 'gemeinde ', 'hansestadt ', 'landkreis ']
    city_lower = city.lower()
    for prefix in admin_prefixes:
        if city_lower.startswith(prefix):
            city_without_prefix = city[len(prefix):]
            variations.append(city_without_prefix)
            # Also add space/hyphen variations of the cleaned city
            if ' ' in city_without_prefix:
                variations.append(city_without_prefix.replace(' ', '-'))
            if '-' in city_without_prefix:
                variations.append(city_without_prefix.replace('-', ' '))
            break
    
    # Remove duplicates while preserving order
    seen = set()
    unique_variations = []
    for var in variations:
        if var.lower() not in seen:
            seen.add(var.lower())
            unique_variations.append(var)
    
    return unique_variations


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


def parse_house_number(house_number: str) -> Optional[int]:
    """
    Extract the numeric part from a house number.
    
    Examples:
        "38" -> 38
        "38a" -> 38
        "38-40" -> 38
        "38 a" -> 38
    
    Args:
        house_number: The house number string
        
    Returns:
        The numeric part as integer, or None if no number found
    """
    if not house_number:
        return None
    
    # Extract first number from the string
    match = re.match(r'^\s*(\d+)', house_number.strip())
    if match:
        return int(match.group(1))
    return None


def find_nearest_house_number(target: str, available: List[str]) -> Optional[str]:
    """
    Find the nearest house number from a list of available house numbers.
    
    The matching priority is:
    1. Exact match (e.g., "38a" -> "38a")
    2. Same number, different suffix (e.g., "38d" -> "38a" if 38a exists)
    3. Nearest number by numeric value (e.g., "37" -> "36")
    
    Args:
        target: The target house number (e.g., "38a", "37")
        available: List of available house numbers
        
    Returns:
        The nearest house number from the available list, or None if list is empty
    """
    if not available:
        return None
    
    if not target:
        return None
    
    # First, check for exact match
    target_stripped = target.strip()
    for hn in available:
        if hn.strip() == target_stripped:
            return hn
    
    # Parse target number
    target_num = parse_house_number(target)
    if target_num is None:
        # If we can't parse the target, return first available
        return available[0] if available else None
    
    # Look for matches with the same base number (priority 2)
    same_number_candidates = []
    for hn in available:
        hn_num = parse_house_number(hn)
        if hn_num == target_num:
            same_number_candidates.append(hn)
    
    # If we found house numbers with the same base number, prefer those
    if same_number_candidates:
        # Extract suffix from target (everything after the number)
        target_match = re.match(r'^\s*\d+(.*)$', target_stripped)
        target_suffix = target_match.group(1).strip() if target_match else ""
        
        # Find the candidate with the closest suffix
        best_candidate = None
        best_suffix_distance = float('inf')
        
        for candidate in same_number_candidates:
            candidate_match = re.match(r'^\s*\d+(.*)$', candidate.strip())
            candidate_suffix = candidate_match.group(1).strip() if candidate_match else ""
            
            # Calculate "distance" between suffixes
            if target_suffix == candidate_suffix:
                # Exact suffix match (shouldn't happen as we checked exact match earlier)
                return candidate
            elif not target_suffix or not candidate_suffix:
                # One has no suffix - prefer the one without suffix
                suffix_distance = 0 if not candidate_suffix else 1
            else:
                # Both have suffixes - calculate alphabetic distance
                # For simple suffixes like "a", "b", "c", use character distance
                if len(target_suffix) == 1 and len(candidate_suffix) == 1 and target_suffix.isalpha() and candidate_suffix.isalpha():
                    suffix_distance = abs(ord(target_suffix.lower()) - ord(candidate_suffix.lower()))
                else:
                    # For complex suffixes, use string edit distance (simple approach)
                    suffix_distance = abs(len(target_suffix) - len(candidate_suffix)) + (0 if target_suffix == candidate_suffix else 1)
            
            if suffix_distance < best_suffix_distance:
                best_candidate = candidate
                best_suffix_distance = suffix_distance
            elif suffix_distance == best_suffix_distance and best_candidate:
                # If same distance, prefer alphabetically lower
                if candidate < best_candidate:
                    best_candidate = candidate
        
        return best_candidate if best_candidate else same_number_candidates[0]
    
    # Fall back to finding the closest match by numeric value (priority 3)
    best_match = None
    best_distance = float('inf')
    
    for hn in available:
        hn_num = parse_house_number(hn)
        if hn_num is None:
            continue
        
        distance = abs(target_num - hn_num)
        
        if distance < best_distance:
            best_match = hn
            best_distance = distance
        elif distance == best_distance and best_match:
            # If same distance, prefer lower alphabetically (for consistency)
            if hn < best_match:
                best_match = hn
    
    # If no numeric match found, return first available
    return best_match if best_match else (available[0] if available else None)
