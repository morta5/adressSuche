"""Advanced Street Autocomplete API v2."""

import asyncio
import logging
import math
import os
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

from fastapi import Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from query_processor import QueryProcessor
from phonetic import phonetic_match_score, phonetic_forms
from bktree import levenshtein_distance

from database import get_async_db, init_db
from models import Street, Address

# Configure logging
logger = logging.getLogger(__name__)
from schemas import StreetAutocompleteResponse, AddressValidationResponse
from utils import (
    haversine_distance,
    normalize_string,
    normalize_compact,
    normalize_city_for_matching,
    generate_city_variations,
    calculate_fuzzy_score_normalized,
    consonant_key,
    point_to_segment_distance,
    find_nearest_house_number,
    parse_house_number,
)

# Constants for search quality thresholds
HIGH_QUALITY_SCORE_THRESHOLD = 0.65  # Score threshold for early exit optimization
FUZZY_TRIGRAM_CANDIDATE_LIMIT = 3000  # Max candidates to fetch from trigram search

# Performance tuning constants for stage limits
TRIGRAM_LIMIT_MULTIPLIER = 15  # Multiplier for trigram search results
TRIGRAM_MIN_LIMIT = 300  # Minimum results for trigram search
PHONETIC_LIMIT_MULTIPLIER = 20  # Multiplier for phonetic search results
PHONETIC_MIN_LIMIT = 100  # Minimum results for phonetic search
PHONETIC_MAX_LIMIT = 250  # Maximum results for phonetic search
RERANK_CANDIDATE_MULTIPLIER = 10  # Multiplier for reranking candidate pool
RERANK_MIN_CANDIDATES = 100  # Minimum candidates for reranking

# Unicode constants for prefix range queries
UNICODE_MAX_CODEPOINT = 0x10FFFF  # Maximum valid Unicode code point
UNICODE_FALLBACK_UPPER = "\uffff"  # Fallback upper bound for prefix range

# Trigram selection thresholds for fuzzy search
# These control how many trigrams are selected based on query length
TRIGRAM_LONG_THRESHOLD = 9  # Queries with >= 9 trigrams use full selection
TRIGRAM_MEDIUM_THRESHOLD = 6  # Queries with >= 6 trigrams use medium selection
TRIGRAM_SHORT_THRESHOLD = 4  # Queries with >= 4 trigrams use short selection

# Async-safe lazy loading using asyncio.Lock


async def _geo_bounds(
    lat: float, lon: float, radius_km: float
) -> Tuple[float, float, float, float]:
    lat_deg = radius_km / 110.574
    lon_deg = radius_km / (111.320 * max(0.0001, math.cos(math.radians(lat))))
    return lat - lat_deg, lat + lat_deg, lon - lon_deg, lon + lon_deg


def _distance_penalized(score: float, geo_distance: Optional[float]) -> float:
    if geo_distance is None:
        return score
    # Penalty based on distance - much stronger to favor nearby results
    if score >= 0.7:
        k = 50.0  # Reduced from 220 - much stronger penalty
    elif score >= 0.5:
        k = 35.0  # Reduced from 150
    else:
        k = 25.0  # Reduced from 100
    penalty = 1.0 / (1.0 + (geo_distance / k))
    return max(0.1, score * penalty)


def _prefix_bonus(qc: str, nc: str) -> float:
    m = 0
    for a, b in zip(qc, nc):
        if a != b:
            break
        m += 1
    return min(m, 6) * 0.02


def _collect_phonetic_codes(candidates: List[str]) -> Tuple[List[str], List[str]]:
    german_seen: set[str] = set()
    cologne_seen: set[str] = set()
    german_codes: List[str] = []
    cologne_codes: List[str] = []

    for text in candidates:
        if not text:
            continue
        german, cologne = phonetic_forms(text)
        if german and german not in german_seen:
            german_seen.add(german)
            german_codes.append(german)
        if cologne and cologne not in cologne_seen:
            cologne_seen.add(cologne)
            cologne_codes.append(cologne)

    # Also add codes per token to handle compound mismatches
    for text in candidates:
        if not text:
            continue
        parts = normalize_string(text).split()
        for part in parts:
            german, cologne = phonetic_forms(part)
            if german and german not in german_seen:
                german_seen.add(german)
                german_codes.append(german)
            if cologne and cologne not in cologne_seen:
                cologne_seen.add(cologne)
                cologne_codes.append(cologne)

    return german_codes, cologne_codes


# Cache for known city names (lazy loaded)
_known_cities: set[str] | None = None
_known_cities_lock: asyncio.Lock | None = None


async def _get_known_cities(db: AsyncSession) -> set[str]:
    """Load known city names from the database (cached, case-insensitive)."""
    global _known_cities, _known_cities_lock

    # Initialize lock lazily to ensure it's created in the event loop
    if _known_cities_lock is None:
        _known_cities_lock = asyncio.Lock()

    if _known_cities is not None:
        return _known_cities

    async with _known_cities_lock:
        if _known_cities is not None:
            return _known_cities

        try:
            # Query distinct cities using SQLAlchemy async session
            stmt = select(Street.city).distinct().where(Street.city.isnot(None))
            result = await db.execute(stmt)
            cities = {row[0].lower() for row in result.fetchall() if row[0]}
            _known_cities = cities
            return _known_cities
        except Exception as e:
            logger.debug(f"Failed to load cities: {e}")
            return set()


async def _extract_city_from_query(
    query: str, 
    known_cities: set[str],
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    db: Optional[AsyncSession] = None
) -> Tuple[str, Optional[str]]:
    """
    Extract city name from the end of query if present.

    For query "jungfernstieg hamburg", returns ("jungfernstieg", "Hamburg").
    For query "hauptstraße berlin mitte", returns ("hauptstraße", "Berlin Mitte").
    For query "bahnhofstraße", returns ("bahnhofstraße", None).
    For query "kampstraße neum" with coordinates near Neumünster, returns ("kampstraße", "Neumünster") - uses geo distance.
    For query "kampstraße neumuenster", returns ("kampstraße", "Neumünster") - normalized match returns actual city.

    Args:
        query: Search query
        known_cities: Set of known city names (lowercase)
        latitude: Optional latitude for geographic disambiguation
        longitude: Optional longitude for geographic disambiguation
        db: Optional database session to query city coordinates

    Returns:
        Tuple of (street_query, detected_city_from_database)
    """
    parts = query.strip().split()
    if len(parts) < 2:
        return query, None

    # Try to match last N words as a city (N from 1 to min(3, len(parts)-1))
    for n in range(min(3, len(parts) - 1), 0, -1):
        potential_city = " ".join(parts[-n:]).lower()
        potential_city_normalized = normalize_compact(potential_city)

        # Strategy 1: Exact match (fastest)
        if potential_city in known_cities:
            street_query = " ".join(parts[:-n])
            # Return the potential_city directly (already lowercase from known_cities set)
            detected_city = potential_city
            return street_query, detected_city
        
        # Strategy 2: Normalized match (handles "neumuenster" vs "neumünster")
        # Check if normalized form matches any known city's normalized form
        for known_city in known_cities:
            known_city_normalized = normalize_compact(known_city)
            if potential_city_normalized == known_city_normalized:
                street_query = " ".join(parts[:-n])
                # Return the actual city from database (with ü, not ue)
                detected_city = known_city
                return street_query, detected_city
        
        # Strategy 3: Partial prefix match (handles "neum" for "neumünster")
        # Only for single-word cities and if at least 3 characters
        if n == 1 and len(potential_city) >= 3:
            # Collect all matching cities
            matching_cities = []
            for known_city in known_cities:
                known_city_normalized = normalize_compact(known_city)
                # Check if the normalized potential city is a prefix of a known city
                if known_city_normalized.startswith(potential_city_normalized):
                    matching_cities.append(known_city)
            
            if matching_cities:
                street_query = " ".join(parts[:-n])
                
                # If we have geographic coordinates and a database session, use them to disambiguate
                if latitude is not None and longitude is not None and db is not None:
                    try:
                        # Get coordinates for each matching city using async SQLAlchemy
                        # Use func.lower() with IN for better performance
                        
                        # Create lowercase versions of matching cities for case-insensitive comparison
                        lowercase_cities = [city.lower() for city in matching_cities]
                        
                        stmt = (
                            select(Street.city, 
                                   func.avg(Street.latitude).label("avg_lat"), 
                                   func.avg(Street.longitude).label("avg_lon"))
                            .where(func.lower(Street.city).in_(lowercase_cities))
                            .group_by(Street.city)
                        )
                        
                        result = await db.execute(stmt)
                        city_coords = {}
                        for row in result.fetchall():
                            city_name = row[0]
                            avg_lat = row[1]
                            avg_lon = row[2]
                            city_coords[city_name] = (avg_lat, avg_lon)
                        
                        # Find the closest city by geographic distance
                        if city_coords:
                            best_city = None
                            best_distance = float('inf')
                            
                            for city_name, (city_lat, city_lon) in city_coords.items():
                                distance = haversine_distance(latitude, longitude, city_lat, city_lon)
                                if distance < best_distance:
                                    best_distance = distance
                                    best_city = city_name
                            
                            # Accept only if the nearest candidate city is reasonably close
                            if best_city and best_distance <= 80.0:
                                detected_city = best_city
                                return street_query, detected_city
                    except Exception as e:
                        # If geo disambiguation fails, fall back to shortest match
                        logger.debug(f"Geographic disambiguation failed: {e}")
                        pass
                
                # Fallback: prefer shorter city names (more specific matches)
                # Sort by length, then alphabetically
                matching_cities.sort(key=lambda c: (len(c), c))
                detected_city = matching_cities[0]
                return street_query, detected_city

    return query, None


def _select_fuzzy_trigrams(all_trigrams: list[str]) -> list[str]:
    """Select trigrams for fuzzy matching, avoiding common suffix trigrams.

    German street names often end in "straße/strasse" which produces very common
    trigrams (str, tra, ras, ass, sse) that match most of the database. We focus
    on trigrams from the unique part of the name (start and middle).

    Args:
        all_trigrams: List of all trigrams from the query

    Returns:
        Selected trigrams for OR matching
    """
    # Common suffix trigrams from "strasse", "weg", "platz", etc.
    # These match too many entries and should be avoided
    COMMON_SUFFIX_TRIGRAMS = {
        '"str"',
        '"tra"',
        '"ras"',
        '"ass"',
        '"sse"',  # strasse
        '"weg"',
        '"pla"',
        '"lat"',
        '"atz"',  # weg, platz
        '"rin"',
        '"ing"',  # ring
        '"lle"',
        '"lee"',  # allee
    }

    # Filter out common suffix trigrams
    filtered = [t for t in all_trigrams if t.lower() not in COMMON_SUFFIX_TRIGRAMS]

    # If too many were filtered out, use more from the original
    if len(filtered) < 3 and len(all_trigrams) >= 3:
        # Use first half which is less likely to be common suffixes
        filtered = all_trigrams[: len(all_trigrams) // 2 + 2]

    if not filtered:
        filtered = all_trigrams

    # Select trigrams focusing on start and middle (where unique name content is)
    if len(filtered) >= TRIGRAM_LONG_THRESHOLD:
        # Long strings: pick from start (idx 0-2) and middle
        start_tris = filtered[0:3]
        mid_idx = len(filtered) // 2
        mid_tris = filtered[mid_idx - 1 : mid_idx + 2]
        return list(dict.fromkeys(start_tris + mid_tris))  # Remove dupes
    elif len(filtered) >= TRIGRAM_MEDIUM_THRESHOLD:
        # Medium strings: use a wider net (start + middle + end) to better
        # tolerate transposition typos while keeping the pattern selective.
        start_tris = filtered[0:3]
        mid_idx = len(filtered) // 2
        mid_tris = filtered[mid_idx : mid_idx + 2]
        end_tris = filtered[-2:]
        return list(dict.fromkeys(start_tris + mid_tris + end_tris))
    elif len(filtered) >= TRIGRAM_SHORT_THRESHOLD:
        # Short strings: pick first few
        return filtered[0:3]
    else:
        return filtered


app = FastAPI(
    title="Street Autocomplete API v2",
    description="Advanced search with query understanding, phonetics and multi-stage fuzzy matching",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Check if frontend should be served (controlled by environment variable)
SERVE_FRONTEND = os.getenv("SERVE_FRONTEND", "false").lower() == "true"

# Mount static files from frontend directory
frontend_path = Path(__file__).parent / "frontend"
if SERVE_FRONTEND and frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")


@app.on_event("startup")
async def _on_startup():
    init_db()


@app.get("/")
async def root():
    """Serve the homepage."""
    if not SERVE_FRONTEND:
        return {"message": "Street Autocomplete API v2", "docs": "/docs"}
    
    index_path = frontend_path / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text(), status_code=200)
    return {"message": "Street Autocomplete API v2", "docs": "/docs"}


@app.get("/de")
async def root_de():
    """Serve the German homepage."""
    if not SERVE_FRONTEND:
        return {"message": "Street Autocomplete API v2", "docs": "/docs"}
    
    de_path = frontend_path / "de.html"
    if de_path.exists():
        return HTMLResponse(content=de_path.read_text(), status_code=200)
    return {"message": "Street Autocomplete API v2 (DE)", "docs": "/docs"}


@app.get("/autocomplete", response_model=List[StreetAutocompleteResponse])
async def autocomplete(
    query: str = Query(..., min_length=1),
    city: Optional[str] = Query(None),
    latitude: Optional[float] = Query(None),
    longitude: Optional[float] = Query(None),
    limit: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_async_db),
):
    raw_query = query  # Preserve the original query for potential fallback

    # Placeholders for query-dependent state populated by _prepare_query_state
    qc = qn = qc_lower = query_titlecase = qc_titlecase = ""
    qg_phonetic = qc_phonetic = ""
    q_consonant = ""
    expanded_queries: List[str] = []
    expanded_norm: List[str] = []

    def _prepare_query_state(q: str) -> None:
        nonlocal query, qc, qn, qc_lower, query_titlecase, qc_titlecase
        nonlocal qg_phonetic, qc_phonetic, q_consonant, expanded_queries, expanded_norm

        query = q
        qc = normalize_compact(query)
        qn = normalize_string(query)
        qc_lower = qc.lower() if qc else ""

        # Convert query to Title Case for optimal database matching
        # Preserve hyphens in the title-cased result for better matching with database
        # E.g., "albert-schweitzer-straße" -> "Albert-Schweitzer-Straße" (not "Albert Schweitzer Straße")
        if '-' in query:
            parts = query.split('-')
            titlecased_parts = [part.capitalize() for part in parts]
            query_titlecase_local = '-'.join(titlecased_parts)
        else:
            query_titlecase_local = ' '.join(word.capitalize() for word in query.split())
        query_titlecase = query_titlecase_local
        qc_titlecase = normalize_compact(query_titlecase)

        # Cache phonetic forms for reuse across stages
        qg_phonetic, qc_phonetic = phonetic_forms(query)
        q_consonant = consonant_key(query)

        # Query expansion (abbr/suffix/hyphen)
        expanded_queries = QueryProcessor.expand_query(query)
        expanded_norm = list(dict.fromkeys([normalize_string(q) for q in expanded_queries]))
    # Extract city from query if not provided explicitly
    # E.g., "jungfernstieg hamburg" -> query="jungfernstieg", city="hamburg"
    if city is None:
        known_cities = await _get_known_cities(db)
        query, detected_city = await _extract_city_from_query(
            query, known_cities, latitude, longitude, db
        )
        if detected_city:
            city = detected_city
            logger.debug(f"Extracted city '{city}' from query, street query: '{query}'")

    _prepare_query_state(query)

    processed_ids = set()
    candidates: list[tuple[StreetAutocompleteResponse, float]] = []

    # Stage A: Exact prefix on name and normalized_name (OPTIMIZED - uses range queries for index)
    async def exact_prefix() -> list[tuple[StreetAutocompleteResponse, float, int]]:
        local = []
        added = set()

        # Helper to get the end of prefix range (increment last character)
        def prefix_end(prefix: str) -> str:
            if not prefix:
                return ""
            # Find the last character that can be incremented
            chars = list(prefix)
            for i in range(len(chars) - 1, -1, -1):
                if ord(chars[i]) < UNICODE_MAX_CODEPOINT:  # Can increment
                    chars[i] = chr(ord(chars[i]) + 1)
                    return "".join(chars[: i + 1])
            return prefix + UNICODE_FALLBACK_UPPER  # Fallback for maximum codepoint

        # Use UNION of two indexed range queries instead of OR (which causes full scan)
        # Adaptive limit based on query characteristics and geo coordinates:
        # When using ORDER BY distance, we need a small limit for fast performance
        if latitude is not None and longitude is not None:
            # With distance ordering, ORDER BY is expensive - keep limit reasonable
            # but big enough to ensure good recall
            is_multiword = ' ' in query.strip()
            
            # Detect very common street names that need larger pools
            common_names = {'haupt', 'bahnhof', 'kirch', 'schul', 'mark', 'berg'}
            is_common = any(query.lower().startswith(name) for name in common_names)
            
            if is_multiword:
                stage_a_limit = 50  # Multi-word with coords - keep small for performance
            elif is_common:
                stage_a_limit = 300  # Common street names need large pool
            elif len(query) <= 6:
                stage_a_limit = 60  # Very short prefixes
            elif len(query) >= 12:
                stage_a_limit = 40  # Longer queries are usually more specific - smaller pool
            else:
                stage_a_limit = 50  # Medium-length queries
        else:
            stage_a_limit = limit * 4
        
        params: Dict[str, Any] = {
            "q1_start": query_titlecase,  # Use title-cased query for DB matching
            "q1_end": prefix_end(query_titlecase),
            "limit": stage_a_limit,
        }

        # Build the SQL with UNION to use index on both queries
        if qc_titlecase and qc_titlecase != query_titlecase:
            # Both original name and normalized name
            params["q2_start"] = qc_titlecase
            params["q2_end"] = prefix_end(qc_titlecase)
            sql = """
                SELECT id, name, city, postal_code, latitude, longitude FROM (
                    SELECT id, name, city, postal_code, latitude, longitude
                    FROM streets
                    WHERE name >= :q1_start AND name < :q1_end
                    UNION
                    SELECT id, name, city, postal_code, latitude, longitude
                    FROM streets
                    WHERE normalized_name >= :q2_start AND normalized_name < :q2_end
                ) sub
            """
        else:
            # Only original name
            sql = """
                SELECT id, name, city, postal_code, latitude, longitude
                FROM streets
                WHERE name >= :q1_start AND name < :q1_end
            """

        geo_filter = ""
        if (
            latitude is not None
            and longitude is not None
            and city is None
            and original_city is None
        ):
            # Skip geo filtering for very specific queries (long, unique names)
            # These can be found quickly via index without geo constraints
            if len(query) >= 11 and ' ' not in query:
                # Long single-word queries are specific enough - no geo filtering needed
                geo_filter = ""
            else:
                # Narrow to a local bounding box to ensure nearby matches for very common names (e.g., Hauptstraße)
                # Detect very common street names that need larger radius
                common_names = {'haupt', 'bahnhof', 'kirch', 'schul', 'mark', 'berg'}
                is_common = any(query.lower().startswith(name) for name in common_names)
                
                if is_common:
                    geo_radius = 45.0  # Common names need wider radius
                elif len(qc) <= 6:
                    geo_radius = 35.0  # Short queries - moderate radius
                elif " " in query:
                    geo_radius = 32.0  # Multi-word queries - tighter radius
                else:
                    geo_radius = 35.0  # Default moderate radius
                
                min_lat, max_lat, min_lon, max_lon = await _geo_bounds(latitude, longitude, geo_radius)
                params.update(
                    {
                        "min_lat": min_lat,
                        "max_lat": max_lat,
                        "min_lon": min_lon,
                        "max_lon": max_lon,
                    }
                )
                geo_filter = "latitude BETWEEN :min_lat AND :max_lat AND longitude BETWEEN :min_lon AND :max_lon"

        if city:
            # Wrap with city filter - explicitly list columns instead of SELECT *
            # Use LOWER for case-insensitive matching (handles "neum" matching "Neumünster")
            params["city"] = f"{city.lower()}%"
            filters = ["LOWER(city) LIKE :city"]
            if geo_filter:
                filters.append(geo_filter)
            sql = f"SELECT id, name, city, postal_code, latitude, longitude FROM ({sql}) WHERE " + " AND ".join(filters)
        elif geo_filter:
            sql = f"SELECT id, name, city, postal_code, latitude, longitude FROM ({sql}) WHERE {geo_filter}"

        # NOTE: Do NOT order by distance in SQL - it's too slow!
        # Fetch results and sort in Python instead
        sql += " LIMIT :limit"

        try:
            res = await db.execute(text(sql), params)
            rows = res.fetchall()
            
            # Sort by distance in Python if geo-coordinates provided
            if latitude is not None and longitude is not None:
                rows = sorted(
                    rows,
                    key=lambda r: (
                        (r._mapping["latitude"] - latitude) ** 2 +
                        (r._mapping["longitude"] - longitude) ** 2
                    )
                )
            
            for r in rows:
                sid = r._mapping["id"]
                if sid in added:
                    continue
                added.add(sid)
                name = r._mapping["name"]
                # Score based on whether it's an exact prefix match
                name_lower = name.lower()
                query_lower = query.lower()
                if name_lower.startswith(query_lower):
                    sc = 1.0
                else:
                    sc = 0.97
                if "straße" in name_lower or "strasse" in name_lower:
                    sc += 0.02  # prefer canonical street suffix
                resp = StreetAutocompleteResponse(
                    street_id=sid,
                    name=name,
                    city=r._mapping["city"],
                    postal_code=r._mapping.get("postal_code"),
                    latitude=r._mapping["latitude"],
                    longitude=r._mapping["longitude"],
                    match_score=sc,
                )
                if latitude is not None and longitude is not None:
                    d = haversine_distance(
                        latitude, longitude, resp.latitude, resp.longitude
                    )
                    resp.distance_km = round(d, 2)
                    # NOTE: Do NOT apply distance penalty here!
                    # Distance is only used for final reranking boost
                local.append((resp, sc, sid))
        except Exception:
            pass
        return local

    # Stage B: Trigram FTS search on normalized_name
    async def trigram_search() -> list[tuple[StreetAutocompleteResponse, float, int]]:
        if len(qc) < 2:
            return []
        # Use performance tuning constants
        trigram_limit = max(limit * 10, 150)
        params: Dict[str, Any] = {"pattern": f"{qc}*", "limit": trigram_limit}
        sql = [
            "SELECT s.id AS street_id, s.name, s.city, s.postal_code, s.latitude, s.longitude,",
            "       bm25(street_trigram) AS rnk, s.normalized_name AS nn",
            "FROM street_trigram JOIN streets s ON s.id = street_trigram.rowid",
            "WHERE street_trigram MATCH :pattern",
        ]
        if city:
            sql.append("AND LOWER(s.city) LIKE :city")
            params["city"] = f"{city.lower()}%"
        sql.append("ORDER BY rnk ASC, s.name ASC LIMIT :limit")
        try:
            rows = (await db.execute(text("\n".join(sql)), params)).fetchall()
        except Exception:
            return []
        local = []
        for r in rows:
            nn = r._mapping.get("nn") or normalize_compact(
                r._mapping["name"]
            )  # fallback
            base = 1.0 / (1.0 + (float(r._mapping["rnk"]) / 6.0))
            base += _prefix_bonus(qc, nn)
            if "straße" in nn.lower() or "strasse" in nn.lower():
                base += 0.02  # prefer canonical street suffix
            resp = StreetAutocompleteResponse(
                street_id=r._mapping["street_id"],
                name=r._mapping["name"],
                city=r._mapping["city"],
                postal_code=r._mapping.get("postal_code"),
                latitude=r._mapping["latitude"],
                longitude=r._mapping["longitude"],
                match_score=base,
            )
            if latitude is not None and longitude is not None:
                d = haversine_distance(
                    latitude, longitude, resp.latitude, resp.longitude
                )
                resp.distance_km = round(d, 2)
                # NOTE: Do NOT apply distance penalty here!
                # Distance is only used for final reranking boost
            local.append((resp, base, resp.street_id))
        return local

    # Stage C: SQL fuzzy LIKE on normalized_search using generated patterns (OPTIMIZED)
    async def sql_typos() -> list[tuple[StreetAutocompleteResponse, float, int]]:
        if len(qn) < 3:
            return []

        # Generate fewer, more targeted patterns
        patterns = []
        # Single-wildcard patterns - only at likely typo positions (first 4 chars)
        for i in range(min(4, len(qn))):
            patterns.append(qn[:i] + "_" + qn[i + 1 :])

        # Combine all patterns into single OR query for performance
        local = []
        added = set()

        # Run all patterns in a single query using OR
        or_clauses = " OR ".join(
            [f"normalized_search LIKE :p{i}" for i in range(len(patterns))]
        )
        params = {f"p{i}": f"%{p}%" for i, p in enumerate(patterns)}
        params["limit"] = max(80, limit * 10)

        sql = f"""
            SELECT id, name, city, postal_code, latitude, longitude, normalized_name
            FROM streets
            WHERE ({or_clauses})
        """
        if city:
            sql += " AND LOWER(city) LIKE :city"
            params["city"] = f"{city.lower()}%"
        sql += " LIMIT :limit"

        try:
            res = await db.execute(text(sql), params)
            for r in res.fetchall():
                sid = r._mapping["id"]
                if sid in added:
                    continue
                added.add(sid)
                cand_norm = normalize_compact(r._mapping["name"])
                sc = 0.62 + _prefix_bonus(qc, cand_norm)
                resp = StreetAutocompleteResponse(
                    street_id=sid,
                    name=r._mapping["name"],
                    city=r._mapping["city"],
                    postal_code=r._mapping.get("postal_code"),
                    latitude=r._mapping["latitude"],
                    longitude=r._mapping["longitude"],
                    match_score=sc,
                )
                if latitude is not None and longitude is not None:
                    d = haversine_distance(
                        latitude, longitude, resp.latitude, resp.longitude
                    )
                    resp.distance_km = round(d, 2)
                    # NOTE: Do NOT apply distance penalty here!
                    # Distance is only used for final reranking boost
                local.append((resp, sc, sid))
        except Exception:
            pass
        return local

    # Stage D: Phonetic search using precomputed phonetic codes (SIMPLIFIED for speed)
    async def phonetic_stage() -> list[tuple[StreetAutocompleteResponse, float, int]]:
        # Use cached phonetic forms
        qg = qg_phonetic
        qc_ph = qc_phonetic
        q_cons = q_consonant

        if not qg and not qc_ph and not q_cons:
            return []

        # Use performance tuning constants
        params: Dict[str, Any] = {
            "prelimit": max(PHONETIC_MIN_LIMIT, min(PHONETIC_MAX_LIMIT, limit * PHONETIC_LIMIT_MULTIPLIER)),
        }

        where_clauses = []
        if qg and len(qg) >= 2:
            where_clauses.append("phonetic_german LIKE :pg")
            params["pg"] = f"{qg[:3]}%"
        if qc_ph and len(qc_ph) >= 2:
            where_clauses.append("phonetic_cologne LIKE :pc")
            params["pc"] = f"{qc_ph[:3]}%"
        if q_cons and len(q_cons) >= 2:
            where_clauses.append("consonant_key LIKE :qcons")
            params["qcons"] = f"{q_cons[:3]}%"

        if not where_clauses:
            return []

        where_str = " OR ".join(where_clauses)
        sql = f"""
            SELECT id, name, city, postal_code, latitude, longitude, phonetic_german, phonetic_cologne
            FROM streets
            WHERE ({where_str})
        """
        if city:
            sql += " AND LOWER(city) LIKE :city"
            params["city"] = f"{city.lower()}%"
        sql += " LIMIT :prelimit"

        try:
            rows = (await db.execute(text(sql), params)).fetchall()
        except Exception:
            return []

        local: list[tuple[StreetAutocompleteResponse, float, int]] = []
        for r in rows:
            name = r._mapping["name"]
            # Quick phonetic scoring
            ph_score = 0.0
            if qg:
                pg = r._mapping.get("phonetic_german") or ""
                if pg.startswith(qg[:2]):
                    ph_score = 0.8 if pg == qg else 0.6
            if qc_ph and ph_score < 0.7:
                pc = r._mapping.get("phonetic_cologne") or ""
                if pc.startswith(qc_ph[:2]):
                    ph_score = max(ph_score, 0.8 if pc == qc_ph else 0.6)

            base = 0.5 + ph_score * 0.3
            base += _prefix_bonus(qc, normalize_compact(name))

            resp = StreetAutocompleteResponse(
                street_id=r._mapping["id"],
                name=name,
                city=r._mapping["city"],
                postal_code=r._mapping.get("postal_code"),
                latitude=r._mapping["latitude"],
                longitude=r._mapping["longitude"],
                match_score=base,
            )
            if latitude is not None and longitude is not None:
                d = haversine_distance(
                    latitude, longitude, resp.latitude, resp.longitude
                )
                resp.distance_km = round(d, 2)
                # NOTE: Do NOT apply distance penalty here!
                # Distance is only used for final reranking boost
            local.append((resp, base, resp.street_id))
        return local

    # Stage E: Broad prefix fallback + rerank (guarantee recall)
    async def broad_prefix_fallback() -> list[
        tuple[StreetAutocompleteResponse, float, int]
    ]:
        qn_norm = normalize_string(query)
        if not qn_norm:
            return []
        first = qn_norm[0]
        prefixes = {first}
        if len(qn_norm) >= 2:
            prefixes.add(qn_norm[:2])
        # Handle common vowel confusion at 2nd char (a/e/i)
        if len(qn_norm) >= 2:
            pfx1 = qn_norm[0]
            for v in ["a", "e", "i", "o"]:
                prefixes.add(pfx1 + v)

        local = []
        seen = set()
        prelimit = max(400, min(1500, limit * 120))
        for p in list(prefixes)[:6]:
            stmt = (
                select(Street)
                .where(Street.normalized_search.like(f"{p}%"))
                .limit(prelimit)
            )
            if city:
                stmt = stmt.where(Street.city.ilike(f"{city}%"))
            res = await db.execute(stmt)
            for s in res.scalars().all():
                if s.id in seen:
                    continue
                seen.add(s.id)
                resp = _to_response(s, latitude, longitude)
                # Compute strong combined score
                ph = phonetic_match_score(query, resp.name)
                _, fuzz = calculate_fuzzy_score_normalized(
                    normalize_string(query), normalize_string(resp.name)
                )
                base = 0.4 + _prefix_bonus(qc, normalize_compact(resp.name))
                score = min(1.0, 0.4 * base + 0.4 * ph + 0.2 * fuzz)
                if (
                    latitude is not None
                    and longitude is not None
                    and resp.distance_km is not None
                ):
                    score = _distance_penalized(score, resp.distance_km)
                resp.match_score = score
                local.append((resp, score, resp.street_id))
        # Return already reranked subset
        local.sort(key=lambda t: (-t[1], t[0].name, t[0].city))
        return local[: max(limit * 5, 50)]

    # Stage G: Fuzzy trigram search with OR matching for typo tolerance
    async def fuzzy_trigram_search() -> list[
        tuple[StreetAutocompleteResponse, float, int]
    ]:
        """Use trigram OR matching to find candidates with typos.

        Strategy: Use OR instead of AND with multiple trigrams from different
        parts of the string. This allows matching even when some trigrams are
        affected by typos. The Levenshtein distance filter then removes false positives.
        """
        # Use cached normalized form
        if len(qc_lower) < 3:
            return []

        # Generate ALL trigrams from the query
        all_trigrams = []
        for i in range(len(qc_lower) - 2):
            trigram = qc_lower[i : i + 3]
            # Escape quotes for FTS5
            trigram = trigram.replace('"', '""')
            all_trigrams.append(f'"{trigram}"')

        if not all_trigrams:
            return []

        local: list[tuple[StreetAutocompleteResponse, float, int]] = []
        added = set()

        # Select trigrams from START, MIDDLE, and END regions
        # This handles typos at any position by ensuring we match on multiple regions
        selected_trigrams = _select_fuzzy_trigrams(all_trigrams)
        
        # Also include a few suffix trigrams to improve overlap with typo variations
        # This helps catch cases like "klsoter" vs "kloster" where suffix overlap matters
        suffix_trigrams = all_trigrams[-3:] if len(all_trigrams) > 6 else []
        or_trigrams = list(dict.fromkeys(selected_trigrams + suffix_trigrams))

        # Use OR matching - will match if ANY trigram matches
        or_pattern = " OR ".join(or_trigrams)

        params: Dict[str, Any] = {
            "pattern": or_pattern,
            "limit": 150  # Reduced limit for better performance with suffix trigrams
            if city is None
            else min(120, FUZZY_TRIGRAM_CANDIDATE_LIMIT),
        }

        # If geo coords are provided, constrain the search to a local bounding box
        # to avoid distant high-frequency matches dominating the candidate set.
        geo_clause = ""
        if (
            latitude is not None
            and longitude is not None
            and city is None
            and original_city is None
        ):
            # Use a generous radius to keep distant-but-correct matches (e.g., rare names)
            min_lat, max_lat, min_lon, max_lon = await _geo_bounds(
                latitude, longitude, 250.0
            )
            params.update(
                {
                    "min_lat": min_lat,
                    "max_lat": max_lat,
                    "min_lon": min_lon,
                    "max_lon": max_lon,
                }
            )
            geo_clause = (
                " AND s.latitude BETWEEN :min_lat AND :max_lat"
                " AND s.longitude BETWEEN :min_lon AND :max_lon"
            )

        sql = [
            "SELECT s.id AS street_id, s.name, s.city, s.postal_code, s.latitude, s.longitude,",
            "       s.normalized_name AS nn",
            "FROM street_trigram JOIN streets s ON s.id = street_trigram.rowid",
            "WHERE street_trigram MATCH :pattern",
        ]
        if city:
            sql.append("AND LOWER(s.city) LIKE :city")
            params["city"] = f"{city.lower()}%"
        if geo_clause:
            sql.append(geo_clause)
        # ORDER BY bm25 to get best matches first (not rowid order)
        sql.append("ORDER BY bm25(street_trigram) ASC LIMIT :limit")

        try:
            rows = (await db.execute(text("\n".join(sql)), params)).fetchall()
        except Exception:
            return []

        for r in rows:
            sid = r._mapping["street_id"]
            # Check for duplicates BEFORE expensive Levenshtein calculation
            if sid in added:
                continue
            added.add(sid)

            nn = r._mapping.get("nn") or normalize_compact(r._mapping["name"])
            nn_lower = nn.lower() if nn else ""

            # Calculate Levenshtein distance for scoring
            dist = levenshtein_distance(qc_lower, nn_lower, max_dist=4)

            # Only include candidates with reasonable edit distance
            if dist > 3:
                continue

            # Score based on edit distance (closer = higher score)
            if dist == 0:
                base = 1.0
            elif dist == 1:
                base = 0.9
            elif dist == 2:
                base = 0.75
            else:
                base = 0.6

            # Add prefix bonus
            base += _prefix_bonus(qc, nn)

            resp = StreetAutocompleteResponse(
                street_id=r._mapping["street_id"],
                name=r._mapping["name"],
                city=r._mapping["city"],
                postal_code=r._mapping.get("postal_code"),
                latitude=r._mapping["latitude"],
                longitude=r._mapping["longitude"],
                match_score=base,
            )
            if latitude is not None and longitude is not None:
                d = haversine_distance(
                    latitude, longitude, resp.latitude, resp.longitude
                )
                resp.distance_km = round(d, 2)
                # NOTE: Do NOT apply distance penalty here!
                # Distance is only used for final reranking boost
            local.append((resp, base, resp.street_id))

        # Sort by score
        local.sort(key=lambda t: (-t[1], t[0].name))
        return local[: max(limit * 2, 20)]

    # Run retrieval stages with early exit optimization
    # Fast stages first, skip slow stages if we have enough results

    # Store the original city filter for potential fallback
    original_city = city

    # Stage A: Exact prefix (fast, uses index)
    stage_a = await exact_prefix()

    # Check if Stage A found any exact prefix matches (score ~= 1.0 or 0.97)
    # If so, skip expensive fuzzy stages as we likely have good results
    stage_a_has_matches = len(stage_a) > 0
    stage_a_has_good_match = any(
        sc >= HIGH_QUALITY_SCORE_THRESHOLD for _, sc, _ in stage_a
    )

    city_in_raw_query = bool(city) and city.lower() in raw_query.lower()
    if original_city and not city_in_raw_query and not stage_a_has_matches:
        # Low-confidence city extraction (city text not in raw query) with no exact hits – drop city early
        city = None
        original_city = None
        _prepare_query_state(raw_query)
        stage_a = await exact_prefix()
        stage_a_has_matches = len(stage_a) > 0
        stage_a_has_good_match = any(
            sc >= HIGH_QUALITY_SCORE_THRESHOLD for _, sc, _ in stage_a
        )

    # Stage B: Trigram prefix search (fast, uses FTS5 index)
    # Only run if Stage A didn't find any high-quality results
    # Skip expensive fuzzy search if we already have exact prefix matches
    stage_b = []
    stage_g = []  # initialize for later use (may stay empty)
    if not stage_a_has_matches:  # Changed: skip if we have ANY matches
        stage_b = await trigram_search()

    # Early exit check after fast stages
    fast_results = [*stage_a, *stage_b]
    
    # Check if we have nearby high-quality results when geo coords are provided
    has_nearby_results = False
    if latitude is not None and longitude is not None and fast_results:
        # Check if any result is within reasonable distance (< 20km) with high quality score
        for resp, score, _ in fast_results:
            if resp.latitude and resp.longitude:
                dist_km = haversine_distance(latitude, longitude, resp.latitude, resp.longitude)
                if dist_km < 20.0 and score >= HIGH_QUALITY_SCORE_THRESHOLD:
                    has_nearby_results = True
                    break
    
    has_good_fast_results = (
        len(fast_results) >= limit and (has_nearby_results or latitude is None)
    ) or (stage_a_has_matches and stage_a_has_good_match)

    # Stage G: Fuzzy trigram OR (moderate speed, good for typos)
    # ONLY run if we don't have good results from exact matching
    # This is expensive for queries with common suffixes like "straße"
    stage_g = []
    if not has_good_fast_results:
        stage_g = await fuzzy_trigram_search()

    # Combined results
    all_fast_results = [*fast_results, *stage_g]

    # If city filter yields zero candidates, retry without city but keep city for reranking
    if original_city and not all_fast_results:
        logger.debug(
            "City filter '%s' yielded no candidates, retrying without city filter",
            original_city,
        )
        restored_query = raw_query
        raw_lower = raw_query.lower()
        target_lower = original_city.lower()
        if raw_lower.endswith(target_lower):
            restored_query = raw_query[: -len(original_city)].strip()
        city = None
        original_city = None
        _prepare_query_state(restored_query or raw_query)

        stage_a = await exact_prefix()
        stage_a_has_matches = len(stage_a) > 0
        stage_a_has_good_match = any(
            sc >= HIGH_QUALITY_SCORE_THRESHOLD for _, sc, _ in stage_a
        )

        stage_b = []
        if not stage_a_has_good_match:
            stage_b = await trigram_search()

        fast_results = [*stage_a, *stage_b]
        has_good_fast_results = len(fast_results) >= limit or (
            stage_a_has_matches and stage_a_has_good_match
        )

        stage_g = []
        if not has_good_fast_results:
            stage_g = await fuzzy_trigram_search()

        all_fast_results = [*fast_results, *stage_g]

    high_score_count = sum(
        1 for _, sc, _ in all_fast_results if sc >= HIGH_QUALITY_SCORE_THRESHOLD
    )

    # Stage F: (removed - BK-Tree fuzzy search no longer used)
    stage_f = []

    # Only run expensive stages if we don't have enough good results
    stage_c = []
    stage_d = []
    stage_e = []

    if high_score_count < limit and not has_good_fast_results:
        # Stage D: Phonetic (uses index, moderate speed)
        stage_d = await phonetic_stage()

    # Skip Stage C (sql_typos) as it's very slow and Stage G handles typos better
    # Only run if we have very few results
    if not all_fast_results and not stage_d and not stage_f:
        stage_c = await sql_typos()
        stage_e = await broad_prefix_fallback()

    flat: list[tuple[StreetAutocompleteResponse, float, int]] = [
        *stage_a,
        *stage_b,
        *stage_g,
        *stage_d,
        *stage_f,
        *stage_c,
        *stage_e,
    ]


    # Dedup + keep best score
    by_id: Dict[int, tuple[StreetAutocompleteResponse, float]] = {}
    for resp, sc, sid in flat:
        if sid in by_id:
            if sc > by_id[sid][1]:
                by_id[sid] = (resp, sc)
        else:
            by_id[sid] = (resp, sc)

    # Optional phonetic + normalized fuzzy reranking on top K candidates
    # Use performance tuning constants for candidate pool size
    # Skip expensive reranking if we have high-quality exact matches
    skip_expensive_rerank = (
        stage_a_has_good_match
        and len(stage_a) >= limit
        and not stage_b
        and not stage_g
    )
    
    rerank_cap = max(limit * RERANK_CANDIDATE_MULTIPLIER, RERANK_MIN_CANDIDATES)
    if len(qc) <= 6:
        rerank_cap = max(limit * 2, 20)
    elif " " in query:
        rerank_cap = max(limit * 3, 30)
    top_candidates = sorted(by_id.values(), key=lambda t: t[1], reverse=True)[
        : rerank_cap
    ]
    reranked: list[tuple[StreetAutocompleteResponse, float]] = []
    qn_norm = normalize_string(query)
    for resp, base in top_candidates:
        if skip_expensive_rerank:
            # Fast path: skip phonetic/fuzzy for exact matches
            combined = base
        else:
            ph = phonetic_match_score(query, resp.name)
            _, fuzz = calculate_fuzzy_score_normalized(qn_norm, normalize_string(resp.name))
            combined = min(1.0, 0.55 * base + 0.30 * ph + 0.15 * fuzz)

        # Local proximity boost when coordinates are provided (no city filter)
        if (
            latitude is not None
            and longitude is not None
            and resp.distance_km is not None
        ):
            if resp.distance_km <= 30:
                combined = min(1.0, combined * 1.2)
            elif resp.distance_km <= 60:
                combined = min(1.0, combined * 1.05)

        # Strongly favor results in the requested/detected city if provided
        if original_city:
            resp_city_l = (resp.city or "").lower()
            target_city_l = original_city.lower()
            if resp_city_l.startswith(target_city_l):
                combined = min(1.0, combined * 1.1)
            else:
                combined = combined * 0.6
        # Apply distance penalty ONLY when no city filter is present.
        # If the user specified (or we detected) a city, ranking should rely on text/phonetic match,
        # not geo distance from the provided coordinates.
        if (
            latitude is not None
            and longitude is not None
            and resp.distance_km is not None
            and city is None
            and original_city is None
            and combined < 0.85  # Do not penalize strong textual matches
        ):
            # Strong distance penalty to favor nearby results when no city is given
            if combined >= 0.7:
                k = 30.0
            elif combined >= 0.5:
                k = 20.0
            else:
                k = 15.0
            distance_penalty = 1.0 / (1.0 + (resp.distance_km / k))
            combined = max(0.1, combined * distance_penalty)
        # Prefer canonical street suffix slightly in final score to break ties for short prefixes
        if "straße" in (resp.name or "").lower() or "strasse" in (resp.name or "").lower():
            combined = min(1.1, combined + 0.02)
        resp.match_score = combined
        reranked.append((resp, combined))

    reranked.sort(
        key=lambda t: (
            -(t[1]),
            t[0].distance_km if t[0].distance_km is not None else float("inf"),
            t[0].name,
            t[0].city,
        )
    )
    return [r[0] for r in reranked[:limit]]


@app.get("/validate", response_model=AddressValidationResponse)
async def validate_address(
    street_name: str = Query(..., description="Street name"),
    house_number: str = Query(..., description="House number (use '0' for streets without house numbers)"),
    city: Optional[str] = Query(None, description="City name"),
    latitude: Optional[float] = Query(
        None, description="Latitude for distance calculation"
    ),
    longitude: Optional[float] = Query(
        None, description="Longitude for distance calculation"
    ),
    db: AsyncSession = Depends(get_async_db),
):
    target_norm = normalize_string(street_name)
    
    # Normalize city for flexible matching (handles "Henstedt Ulzburg" vs "Henstedt-Ulzburg" and "Kreis Plön" vs "Plön")
    city_norm = normalize_city_for_matching(city) if city else None
    city_variations = generate_city_variations(city) if city else []
    
    # Check if house_number is "0" - treat as request for street without house number
    is_no_house_number_request = house_number.strip() == "0"

    # Try strict normalized match first
    stmt = select(Street).where(Street.normalized_search == target_norm)
    if city_norm and city_variations:
        # Use flexible city matching with all variations
        city_conditions = [Street.city.ilike(f"{var}%") for var in city_variations]
        stmt = stmt.where(or_(*city_conditions))
    
    # If lat/lng provided, try nearby streets first (50km radius), then fallback to all
    streets = []
    if latitude is not None and longitude is not None:
        # First try: search within 50km radius
        min_lat, max_lat, min_lon, max_lon = await _geo_bounds(latitude, longitude, 50.0)
        stmt_nearby = stmt.where(
            Street.latitude.between(min_lat, max_lat),
            Street.longitude.between(min_lon, max_lon)
        )
        res = await db.execute(stmt_nearby.limit(100))
        streets = res.scalars().all()
        
        # If no nearby results, search without geo-bounds (for far away addresses)
        if not streets:
            res = await db.execute(stmt.limit(100))
            streets = res.scalars().all()
    else:
        # Without lat/lng, limit to first 30 results
        res = await db.execute(stmt.limit(30))
        streets = res.scalars().all()

    # If no strict match, try name ilike and phonetic code match as fallback
    if not streets:
        qg, qc_ph = phonetic_forms(street_name)
        conditions = [
            Street.name.ilike(f"{street_name}%"),
            Street.normalized_name.ilike(f"{normalize_compact(street_name)}%"),
        ]
        if qg:
            conditions.append(
                Street.phonetic_german.like(f"{qg[: max(1, len(qg) - 1)]}%")
            )
        if qc_ph:
            conditions.append(
                Street.phonetic_cologne.like(f"{qc_ph[: max(1, len(qc_ph) - 1)]}%")
            )
        stmt2 = select(Street).where(or_(*conditions))
        if city_norm and city_variations:
            # Use flexible city matching here too
            city_conditions = [Street.city.ilike(f"{var}%") for var in city_variations]
            stmt2 = stmt2.where(or_(*city_conditions))
        
        # Apply same geo-filtering strategy: nearby first, then fallback
        if latitude is not None and longitude is not None:
            # First try: search within 50km radius
            min_lat, max_lat, min_lon, max_lon = await _geo_bounds(latitude, longitude, 50.0)
            stmt2_nearby = stmt2.where(
                Street.latitude.between(min_lat, max_lat),
                Street.longitude.between(min_lon, max_lon)
            )
            res2 = await db.execute(stmt2_nearby.limit(100))
            streets = res2.scalars().all()
            
            # If no nearby results, search without geo-bounds
            if not streets:
                res2 = await db.execute(stmt2.limit(100))
                streets = res2.scalars().all()
        else:
            res2 = await db.execute(stmt2.limit(60))
            streets = res2.scalars().all()
    
    # If city filter was provided, filter streets by normalized city name for better matching
    if city_norm and streets:
        filtered_streets = []
        for st in streets:
            st_city_norm = normalize_city_for_matching(str(getattr(st, "city")))
            # Check if normalized cities match or start with the same prefix
            if st_city_norm.startswith(city_norm) or city_norm.startswith(st_city_norm):
                filtered_streets.append(st)
        # Only use filtered results if we found matches, otherwise keep all
        if filtered_streets:
            streets = filtered_streets
    
    # If lat/lng provided, sort streets by distance to prefer nearby matches
    if latitude is not None and longitude is not None and streets:
        streets = sorted(
            streets,
            key=lambda st: haversine_distance(
                latitude, longitude,
                float(getattr(st, "latitude")),
                float(getattr(st, "longitude"))
            )
        )

    # Handle house_number "0" request - return street without house number
    if is_no_house_number_request:
        if streets:
            # streets is already sorted by street centroid distance. Pick the
            # nearest street, but use the nearest address on that street (not
            # the centroid) so the returned lat/lon is more precise.
            st = streets[0]
            result_lat = float(getattr(st, "latitude"))
            result_lon = float(getattr(st, "longitude"))
            if latitude is not None and longitude is not None:
                # Try to find the nearest actual address on this street
                all_addr_res = await db.execute(select(Address).where(Address.street_id == st.id))
                all_addrs = all_addr_res.scalars().all()
                if all_addrs:
                    nearest_addr = min(
                        all_addrs,
                        key=lambda a: haversine_distance(
                            latitude, longitude,
                            float(getattr(a, "latitude")),
                            float(getattr(a, "longitude")),
                        ),
                    )
                    result_lat = float(getattr(nearest_addr, "latitude"))
                    result_lon = float(getattr(nearest_addr, "longitude"))
            resp = AddressValidationResponse(
                exists=True,
                address_id=None,
                street_name=str(getattr(st, "name")),
                city=str(getattr(st, "city")),
                postal_code=str(getattr(st, "postal_code"))
                if getattr(st, "postal_code") is not None
                else None,
                house_number="0",
                latitude=result_lat,
                longitude=result_lon,
            )
            if latitude is not None and longitude is not None:
                resp.distance_km = round(
                    haversine_distance(float(latitude), float(longitude), result_lat, result_lon),
                    2,
                )
            return resp
        return AddressValidationResponse(exists=False)
    
    # First, collect all exact matches across all candidate streets
    exact_matches = []
    for st in streets:
        a_stmt = (
            select(Address)
            .where(Address.street_id == st.id, Address.house_number == house_number)
        )
        ares = await db.execute(a_stmt)
        addrs = ares.scalars().all()
        for addr in addrs:
            exact_matches.append((st, addr))
    
    # If we have exact matches and lat/lng, pick the closest one
    if exact_matches:
        if latitude is not None and longitude is not None:
            exact_matches = sorted(
                exact_matches,
                key=lambda x: haversine_distance(
                    latitude, longitude,
                    float(getattr(x[1], "latitude")),
                    float(getattr(x[1], "longitude"))
                )
            )
        st, addr = exact_matches[0]
        resp = AddressValidationResponse(
            exists=True,
            address_id=int(getattr(addr, "id")),
            street_name=str(getattr(st, "name")),
            city=str(getattr(st, "city")),
            postal_code=str(getattr(st, "postal_code"))
            if getattr(st, "postal_code") is not None
            else None,
            house_number=str(getattr(addr, "house_number")),
            latitude=float(getattr(addr, "latitude")),
            longitude=float(getattr(addr, "longitude")),
        )
        if latitude is not None and longitude is not None:
            resp.distance_km = round(
                haversine_distance(
                    float(latitude),
                    float(longitude),
                    float(getattr(addr, "latitude")),
                    float(getattr(addr, "longitude")),
                ),
                2,
            )
        return resp
    
    # No exact match found - check if street has no addresses (return house_number="0")
    # Check if any street has addresses
    street_has_no_addresses = True
    if streets:
        for st in streets:
            res = await db.execute(select(Address).where(Address.street_id == st.id).limit(1))
            if res.scalars().first():
                street_has_no_addresses = False
                break
    
    if street_has_no_addresses and streets:
        st = streets[0]
        resp = AddressValidationResponse(
            exists=True,
            address_id=None,
            street_name=str(getattr(st, "name")),
            city=str(getattr(st, "city")),
            postal_code=str(getattr(st, "postal_code"))
            if getattr(st, "postal_code") is not None
            else None,
            house_number="0",
            latitude=float(getattr(st, "latitude")),
            longitude=float(getattr(st, "longitude")),
        )
        if latitude is not None and longitude is not None:
            resp.distance_km = round(
                haversine_distance(
                    float(latitude),
                    float(longitude),
                    float(getattr(st, "latitude")),
                    float(getattr(st, "longitude")),
                ),
                2,
            )
        return resp
    
    # Try soft validation with nearest house number
    # Collect all possible soft matches from all candidate streets
    soft_matches = []
    for st in streets:
        # Get all house numbers for this street
        all_hn_stmt = select(Address).where(Address.street_id == st.id)
        all_hn_res = await db.execute(all_hn_stmt)
        all_addresses = all_hn_res.scalars().all()
        
        if all_addresses:
            # Extract house numbers
            available_house_numbers = [str(getattr(a, "house_number")) for a in all_addresses]
            
            # Find nearest house number
            nearest_hn = find_nearest_house_number(house_number, available_house_numbers)
            
            if nearest_hn:
                # Find all addresses with this house number (could be multiple on same street)
                matching_addresses = [addr for addr in all_addresses if str(getattr(addr, "house_number")) == nearest_hn]
                for addr in matching_addresses:
                    soft_matches.append((st, addr))
    
    # If we have soft matches, pick the closest one if lat/lng provided
    if soft_matches:
        target_num = parse_house_number(house_number)

        def _hn_distance(x: tuple) -> int:
            if target_num is None:
                return 0
            hn_num = parse_house_number(str(getattr(x[1], "house_number")))
            return abs(target_num - hn_num) if hn_num is not None else 10_000_000

        if latitude is not None and longitude is not None:
            # Geo distance is always the primary key — a nearby street with a
            # slightly wrong house number beats a far-away street with the exact one.
            soft_matches = sorted(
                soft_matches,
                key=lambda x: (
                    haversine_distance(
                        latitude, longitude,
                        float(getattr(x[1], "latitude")),
                        float(getattr(x[1], "longitude"))
                    ),
                    _hn_distance(x),
                ),
            )
        else:
            soft_matches = sorted(soft_matches, key=_hn_distance)

        st, addr = soft_matches[0]
        resp = AddressValidationResponse(
            exists=True,
            address_id=int(getattr(addr, "id")),
            street_name=str(getattr(st, "name")),
            city=str(getattr(st, "city")),
            postal_code=str(getattr(st, "postal_code"))
            if getattr(st, "postal_code") is not None
            else None,
            house_number=str(getattr(addr, "house_number")),
            latitude=float(getattr(addr, "latitude")),
            longitude=float(getattr(addr, "longitude")),
        )
        if latitude is not None and longitude is not None:
            resp.distance_km = round(
                haversine_distance(
                    float(latitude),
                    float(longitude),
                    float(getattr(addr, "latitude")),
                    float(getattr(addr, "longitude")),
                ),
                2,
            )
        return resp

    return AddressValidationResponse(exists=False)


# ── /validate_voice helpers ───────────────────────────────────────────────────

import re as _re

_VOICE_SUFFIX_RE = _re.compile(
    r"\s*(straße|strasse|weg|platz|gasse|allee|ring|damm|hof|park|berg|"
    r"steig|pfad|ufer|graben|zeile|chaussee|promenade|brücke|brucke|"
    r"kirche|markt|tor|bad|feld|grund|hang|tal|wall|anger|plan|stieg|"
    r"winkel|siedlung|anger|stieg)\s*$",
    _re.IGNORECASE,
)

# Leading preposition+article combos that STT/LLM may prepend to the root name.
# "In der Rohnstraße" → root is "Rohn", not "In der Rohn".
_VOICE_PREFIX_RE = _re.compile(
    r"^(in\s+der|in\s+den|in\s+dem|an\s+der|an\s+den|an\s+dem|am|"
    r"auf\s+der|auf\s+dem|auf\s+den|beim|im|zum|zur|"
    r"hinter\s+der|hinter\s+dem|unter\s+der|unter\s+dem|"
    r"vor\s+der|vor\s+dem|neben\s+der|neben\s+dem)\s+",
    _re.IGNORECASE,
)

_VOICE_GEO_SCAN_RADIUS_KM = 20.0
_VOICE_GEO_SCAN_MIN_SCORE = 0.65
_VOICE_GEO_SCAN_LIMIT = 2000


def _strip_voice_suffix(name: str) -> str:
    """Remove trailing German street-type word for root-only phonetic comparison."""
    return _VOICE_SUFFIX_RE.sub("", name).strip()


def _strip_voice_prefix(name: str) -> str:
    """Remove leading German preposition+article so 'In der Rohnstraße' → 'Rohnstraße'."""
    return _VOICE_PREFIX_RE.sub("", name).strip()


async def _phonetic_geo_scan(
    street_name: str,
    lat: float,
    lon: float,
    city_norm: Optional[str],
    city_variations: List[str],
    db: AsyncSession,
) -> List[Tuple[float, float, Any]]:
    """Return (score, distance_km, Street) tuples for the best phonetic matches
    within VOICE_GEO_SCAN_RADIUS_KM of the given coordinates.

    Fetches geo-bounded streets whose name starts with the same letter as the
    query (pre-filter: cuts candidates ~80%), then scores with the combined
    German+Cologne phonetic metric in a thread so the async event loop is not
    blocked.  Only candidates scoring >= _VOICE_GEO_SCAN_MIN_SCORE are returned,
    sorted by score desc then distance asc.
    """
    min_lat, max_lat, min_lon, max_lon = await _geo_bounds(lat, lon, _VOICE_GEO_SCAN_RADIUS_KM)

    # Strip leading prepositions before root extraction so "In der Rohnstraße"
    # compares as "Rohn" against "Roon", not as "In der Rohn" against "Roon".
    street_name_stripped = _strip_voice_prefix(street_name)
    query_root = _strip_voice_suffix(street_name_stripped)
    # Pre-filter: first-letter LIKE to reduce candidates ~80%.
    # When a preposition was stripped we need BOTH the stripped first letter
    # (e.g. "R" for "Rohnstraße") AND the raw first letter (e.g. "I" for
    # "In der …") so we don't miss real streets like "In der Heide" whose DB
    # name actually starts with the preposition.
    raw_first = street_name[:1].upper() if street_name else ""
    stripped_first = query_root[:1].upper() if query_root else ""

    stmt = select(Street).where(
        Street.latitude.between(min_lat, max_lat),
        Street.longitude.between(min_lon, max_lon),
    )
    if stripped_first and stripped_first != raw_first:
        # Preposition was stripped — include both first letters.
        stmt = stmt.where(or_(
            Street.name.like(f"{stripped_first}%"),
            Street.name.like(f"{raw_first}%"),
        ))
    elif stripped_first:
        stmt = stmt.where(Street.name.like(f"{stripped_first}%"))
    if city_norm and city_variations:
        city_conditions = [Street.city.ilike(f"{v}%") for v in city_variations]
        stmt = stmt.where(or_(*city_conditions))

    res = await db.execute(stmt.limit(_VOICE_GEO_SCAN_LIMIT))
    candidates = res.scalars().all()

    if not candidates:
        return []

    # Scoring is CPU-bound; run in a thread to avoid blocking the event loop.
    def _score_candidates():
        results: List[Tuple[float, float, Any]] = []
        for st in candidates:
            candidate_root = _strip_voice_suffix(_strip_voice_prefix(str(getattr(st, "name"))))
            score = phonetic_match_score(query_root, candidate_root)
            if score >= _VOICE_GEO_SCAN_MIN_SCORE:
                dist = haversine_distance(
                    lat, lon,
                    float(getattr(st, "latitude")),
                    float(getattr(st, "longitude")),
                )
                results.append((score, dist, st))
        results.sort(key=lambda x: (-x[0], x[1]))
        return results

    scored = await asyncio.to_thread(_score_candidates)

    if scored:
        logger.info(
            "validate_voice: phonetic geo-scan found %d candidate(s) ≥%.2f "
            "(from %d pre-filtered), best=%r score=%.3f dist=%.1fkm",
            len(scored),
            _VOICE_GEO_SCAN_MIN_SCORE,
            len(candidates),
            getattr(scored[0][2], "name"),
            scored[0][0],
            scored[0][1],
        )
    return scored


@app.get("/validate_voice", response_model=AddressValidationResponse)
async def validate_address_voice(
    street_name: str = Query(..., description="Street name (may be a voice-transcription approximation)"),
    house_number: str = Query(..., description="House number (use '0' for streets without house numbers)"),
    city: Optional[str] = Query(None, description="City name"),
    latitude: Optional[float] = Query(None, description="Latitude for geo-bias (strongly recommended)"),
    longitude: Optional[float] = Query(None, description="Longitude for geo-bias (strongly recommended)"),
    db: AsyncSession = Depends(get_async_db),
):
    """Like /validate but adds a phonetic geo-scan fallback for voice-recognition errors.

    When the standard exact/prefix/phonetic-prefix stages all fail to find a
    street, this endpoint fetches every street within ~20 km and scores each one
    with a combined German + Cologne phonetic similarity metric.  The best
    candidate above the 0.65 threshold is returned with match_type="phonetic_voice"
    and a confidence score so callers can decide whether to confirm with the user.

    Example: "Buschlederstraße 12" → finds "Boostedter Straße 12" with confidence ≈ 0.73
    """
    # ── Stage 1 & 2: delegate to the exact same logic as /validate ──────────
    # We re-use the inner query logic (not a redirect) so we can inspect the result.
    target_norm = normalize_string(street_name)
    city_norm = normalize_city_for_matching(city) if city else None
    city_variations = generate_city_variations(city) if city else []
    is_no_house_number_request = house_number.strip() == "0"

    stmt = select(Street).where(Street.normalized_search == target_norm)
    if city_norm and city_variations:
        city_conditions = [Street.city.ilike(f"{var}%") for var in city_variations]
        stmt = stmt.where(or_(*city_conditions))

    streets = []
    if latitude is not None and longitude is not None:
        # Voice endpoint: geo-bounds are strict — no fallback to global search.
        # A miss here means the street is not nearby; Stage 3 will handle it.
        min_lat, max_lat, min_lon, max_lon = await _geo_bounds(latitude, longitude, 50.0)
        stmt_nearby = stmt.where(
            Street.latitude.between(min_lat, max_lat),
            Street.longitude.between(min_lon, max_lon),
        )
        res = await db.execute(stmt_nearby.limit(100))
        streets = res.scalars().all()
    else:
        res = await db.execute(stmt.limit(30))
        streets = res.scalars().all()

    if not streets:
        qg, qc_ph = phonetic_forms(street_name)
        conditions = [
            Street.name.ilike(f"{street_name}%"),
            Street.normalized_name.ilike(f"{normalize_compact(street_name)}%"),
        ]
        if qg:
            conditions.append(Street.phonetic_german.like(f"{qg[:max(1, len(qg) - 1)]}%"))
        if qc_ph:
            conditions.append(Street.phonetic_cologne.like(f"{qc_ph[:max(1, len(qc_ph) - 1)]}%"))
        stmt2 = select(Street).where(or_(*conditions))
        if city_norm and city_variations:
            city_conditions = [Street.city.ilike(f"{var}%") for var in city_variations]
            stmt2 = stmt2.where(or_(*city_conditions))
        if latitude is not None and longitude is not None:
            # Again: strict geo-bounds, no global fallback.
            min_lat, max_lat, min_lon, max_lon = await _geo_bounds(latitude, longitude, 50.0)
            stmt2_nearby = stmt2.where(
                Street.latitude.between(min_lat, max_lat),
                Street.longitude.between(min_lon, max_lon),
            )
            res2 = await db.execute(stmt2_nearby.limit(100))
            streets = res2.scalars().all()
        else:
            res2 = await db.execute(stmt2.limit(60))
            streets = res2.scalars().all()

    # City filter + distance sort (same as /validate)
    if city_norm and streets:
        filtered = [
            st for st in streets
            if normalize_city_for_matching(str(getattr(st, "city"))).startswith(city_norm)
            or city_norm.startswith(normalize_city_for_matching(str(getattr(st, "city"))))
        ]
        if filtered:
            streets = filtered

    phonetic_voice_match = False
    phonetic_score: Optional[float] = None

    # ── Stage 3: phonetic geo-scan (voice fallback) ──────────────────────────
    if not streets and latitude is not None and longitude is not None:
        scan_results = await _phonetic_geo_scan(
            street_name, latitude, longitude, city_norm, city_variations, db
        )
        if scan_results:
            phonetic_voice_match = True
            phonetic_score = scan_results[0][0]
            streets = [x[2] for x in scan_results[:10]]

    if latitude is not None and longitude is not None and streets:
        streets = sorted(
            streets,
            key=lambda st: haversine_distance(
                latitude, longitude,
                float(getattr(st, "latitude")),
                float(getattr(st, "longitude")),
            ),
        )

    match_type = "phonetic_voice" if phonetic_voice_match else "exact"

    # ── House number resolution (identical to /validate) ─────────────────────
    if is_no_house_number_request:
        if streets:
            st = streets[0]
            resp = AddressValidationResponse(
                exists=True,
                address_id=None,
                street_name=str(getattr(st, "name")),
                city=str(getattr(st, "city")),
                postal_code=str(getattr(st, "postal_code")) if getattr(st, "postal_code") is not None else None,
                house_number="0",
                latitude=float(getattr(st, "latitude")),
                longitude=float(getattr(st, "longitude")),
                match_type=match_type,
                confidence=phonetic_score,
            )
            if latitude is not None and longitude is not None:
                resp.distance_km = round(haversine_distance(latitude, longitude, float(getattr(st, "latitude")), float(getattr(st, "longitude"))), 2)
            return resp
        return AddressValidationResponse(exists=False)

    exact_matches = []
    for st in streets:
        a_stmt = select(Address).where(Address.street_id == st.id, Address.house_number == house_number)
        ares = await db.execute(a_stmt)
        for addr in ares.scalars().all():
            exact_matches.append((st, addr))

    if exact_matches:
        if latitude is not None and longitude is not None:
            exact_matches = sorted(
                exact_matches,
                key=lambda x: haversine_distance(latitude, longitude, float(getattr(x[1], "latitude")), float(getattr(x[1], "longitude"))),
            )
        st, addr = exact_matches[0]
        resp = AddressValidationResponse(
            exists=True,
            address_id=int(getattr(addr, "id")),
            street_name=str(getattr(st, "name")),
            city=str(getattr(st, "city")),
            postal_code=str(getattr(st, "postal_code")) if getattr(st, "postal_code") is not None else None,
            house_number=str(getattr(addr, "house_number")),
            latitude=float(getattr(addr, "latitude")),
            longitude=float(getattr(addr, "longitude")),
            match_type=match_type,
            confidence=phonetic_score,
        )
        if latitude is not None and longitude is not None:
            resp.distance_km = round(haversine_distance(latitude, longitude, float(getattr(addr, "latitude")), float(getattr(addr, "longitude"))), 2)
        return resp

    street_has_no_addresses = True
    if streets:
        for st in streets:
            res = await db.execute(select(Address).where(Address.street_id == st.id).limit(1))
            if res.scalars().first():
                street_has_no_addresses = False
                break

    if street_has_no_addresses and streets:
        st = streets[0]
        resp = AddressValidationResponse(
            exists=True,
            address_id=None,
            street_name=str(getattr(st, "name")),
            city=str(getattr(st, "city")),
            postal_code=str(getattr(st, "postal_code")) if getattr(st, "postal_code") is not None else None,
            house_number="0",
            latitude=float(getattr(st, "latitude")),
            longitude=float(getattr(st, "longitude")),
            match_type=match_type,
            confidence=phonetic_score,
        )
        if latitude is not None and longitude is not None:
            resp.distance_km = round(haversine_distance(latitude, longitude, float(getattr(st, "latitude")), float(getattr(st, "longitude"))), 2)
        return resp

    soft_matches = []
    for st in streets:
        all_hn_res = await db.execute(select(Address).where(Address.street_id == st.id))
        all_addresses = all_hn_res.scalars().all()
        if all_addresses:
            available = [str(getattr(a, "house_number")) for a in all_addresses]
            nearest_hn = find_nearest_house_number(house_number, available)
            if nearest_hn:
                for addr in all_addresses:
                    if str(getattr(addr, "house_number")) == nearest_hn:
                        soft_matches.append((st, addr))

    if soft_matches:
        target_num = parse_house_number(house_number)
        if target_num is not None:
            def _hn_dist(x):
                n = parse_house_number(str(getattr(x[1], "house_number")))
                return abs(target_num - n) if n is not None else 10_000_000
            if latitude is not None and longitude is not None:
                soft_matches = sorted(soft_matches, key=lambda x: (_hn_dist(x), haversine_distance(latitude, longitude, float(getattr(x[1], "latitude")), float(getattr(x[1], "longitude")))))
            else:
                soft_matches = sorted(soft_matches, key=_hn_dist)
        elif latitude is not None and longitude is not None:
            soft_matches = sorted(soft_matches, key=lambda x: haversine_distance(latitude, longitude, float(getattr(x[1], "latitude")), float(getattr(x[1], "longitude"))))

        st, addr = soft_matches[0]
        resp = AddressValidationResponse(
            exists=True,
            address_id=int(getattr(addr, "id")),
            street_name=str(getattr(st, "name")),
            city=str(getattr(st, "city")),
            postal_code=str(getattr(st, "postal_code")) if getattr(st, "postal_code") is not None else None,
            house_number=str(getattr(addr, "house_number")),
            latitude=float(getattr(addr, "latitude")),
            longitude=float(getattr(addr, "longitude")),
            match_type=match_type,
            confidence=phonetic_score,
        )
        if latitude is not None and longitude is not None:
            resp.distance_km = round(haversine_distance(latitude, longitude, float(getattr(addr, "latitude")), float(getattr(addr, "longitude"))), 2)
        return resp

    return AddressValidationResponse(exists=False)


# Maximum distance in kilometers for reverse geocoding to return a result
REVERSE_GEOCODE_MAX_DISTANCE_KM = 0.1  # 100 meters

# Optimization: Assume no single street segment spans more than this many degrees of latitude.
# This allows us to bound the min_lat search, significantly speeding up queries.
# 0.1 degrees is approx 11km, which is safe for most street segments.
MAX_SEGMENT_LAT_SPAN = 0.1


@app.get("/reverse", response_model=AddressValidationResponse)
async def reverse_geocode(
    latitude: float = Query(..., description="Latitude coordinate"),
    longitude: float = Query(..., description="Longitude coordinate"),
    max_distance_km: Optional[float] = Query(
        None, description="Maximum distance in km (default 0.1 km = 100m)"
    ),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Reverse geocode coordinates to find the nearest address or street.

    First tries to find the nearest house number within the specified distance.
    If no address is found, falls back to finding the nearest street segment.
    Returns the coordinates of the closest point on the street.

    If max_distance_km is not provided, it uses an exponential backoff strategy,
    searching increasingly larger areas (up to 5km) until a match is found.

    Args:
        latitude: Latitude coordinate to search near
        longitude: Longitude coordinate to search near
        max_distance_km: Maximum distance in kilometers (default 0.1 km = 100m)

    Returns:
        AddressValidationResponse with the nearest address or street if found
    """
    # Determine search steps
    if max_distance_km is not None:
        search_distances = [max_distance_km]
    else:
        # Exponential backoff steps optimized to minimize queries:
        # 1. Very close (100m) - covers 90% of urban use cases
        # 2. Medium (1km) - covers most suburban/rural roads
        # 3. Far (5km) - fallback for remote highways
        search_distances = [0.1, 1.0, 5.0]

    for dist in search_distances:
        # Calculate bounding box for filtering
        # Use a slightly larger radius to account for edge cases
        search_radius_km = dist * 1.5
        lat_min, lat_max, lon_min, lon_max = await _geo_bounds(
            latitude, longitude, search_radius_km
        )

        params = {
            "lat_min": lat_min,
            "lat_max": lat_max,
            "lon_min": lon_min,
            "lon_max": lon_max,
        }

        # First, try to find the nearest address (house number)
        # Only if dist is small enough to make sense for house numbers (e.g. < 200m)
        # If we are searching 2km away, we probably just want the street/highway.
        if dist <= 0.2:
            address_result = await _find_nearest_address(db, latitude, longitude, params, dist)
            if address_result:
                return address_result

        # No address found (or skipped), fall back to finding the nearest street segment
        street_result = await _find_nearest_street_segment(db, latitude, longitude, params, dist)
        if street_result:
            return street_result

    return AddressValidationResponse(exists=False)


async def _find_nearest_address(
    db: AsyncSession,
    latitude: float,
    longitude: float,
    params: dict,
    max_dist: float
) -> Optional[AddressValidationResponse]:
    """Find the nearest address within the bounding box."""
    sql = """
        SELECT
            a.id as address_id,
            a.street_id,
            a.house_number,
            a.latitude as addr_lat,
            a.longitude as addr_lon,
            s.name as street_name,
            s.city,
            s.postal_code
        FROM addresses a
        JOIN streets s ON s.id = a.street_id
        WHERE a.latitude BETWEEN :lat_min AND :lat_max
          AND a.longitude BETWEEN :lon_min AND :lon_max
        LIMIT 1000
    """

    try:
        res = await db.execute(text(sql), params)
        rows = res.fetchall()
    except Exception:
        return None

    if not rows:
        return None

    # Calculate actual haversine distance for each candidate and find the nearest
    nearest_row = None
    nearest_distance = float("inf")

    for row in rows:
        addr_lat = row._mapping["addr_lat"]
        addr_lon = row._mapping["addr_lon"]
        distance = haversine_distance(latitude, longitude, addr_lat, addr_lon)

        if distance < nearest_distance:
            nearest_distance = distance
            nearest_row = row

    # Check if the nearest address is within the maximum distance threshold
    if nearest_row is None or nearest_distance > max_dist:
        return None

    # Build and return the response
    return AddressValidationResponse(
        exists=True,
        address_id=int(nearest_row._mapping["address_id"]),
        street_name=str(nearest_row._mapping["street_name"]),
        city=str(nearest_row._mapping["city"]),
        postal_code=str(nearest_row._mapping["postal_code"])
        if nearest_row._mapping["postal_code"] is not None
        else None,
        house_number=str(nearest_row._mapping["house_number"]),
        latitude=float(nearest_row._mapping["addr_lat"]),
        longitude=float(nearest_row._mapping["addr_lon"]),
        distance_km=round(nearest_distance, 2),
    )


async def _find_nearest_street_segment(
    db: AsyncSession,
    latitude: float,
    longitude: float,
    params: dict,
    max_dist: float
) -> Optional[AddressValidationResponse]:
    """Find the nearest street segment within the bounding box."""
    # Query street segments that might be nearby
    # We check segments whose bounding box overlaps with our search area
    # Use a spatial index hint if possible, but standard B-Tree on min/max columns works well
    
    # Optimization: Add lower bound for min_lat to use the index effectively
    # This prevents scanning the entire table for segments starting south of the search area
    params["min_lat_lower"] = params["lat_min"] - MAX_SEGMENT_LAT_SPAN

    sql = """
        SELECT
            seg.id as segment_id,
            seg.street_id,
            seg.start_lat,
            seg.start_lon,
            seg.end_lat,
            seg.end_lon,
            s.name as street_name,
            s.city,
            s.postal_code
        FROM street_segments seg
        JOIN streets s ON s.id = seg.street_id
        WHERE seg.max_lat >= :lat_min AND seg.min_lat <= :lat_max
          AND seg.min_lat >= :min_lat_lower
          AND seg.max_lon >= :lon_min AND seg.min_lon <= :lon_max
    """

    try:
        res = await db.execute(text(sql), params)
        rows = res.fetchall()
    except Exception:
        return None

    if not rows:
        return None

    # Calculate distance from point to each segment and find the nearest
    nearest_row = None
    nearest_distance = float("inf")
    nearest_point_lat = 0.0
    nearest_point_lon = 0.0

    for row in rows:
        start_lat = row._mapping["start_lat"]
        start_lon = row._mapping["start_lon"]
        end_lat = row._mapping["end_lat"]
        end_lon = row._mapping["end_lon"]

        distance, closest_lat, closest_lon = point_to_segment_distance(
            latitude, longitude, start_lat, start_lon, end_lat, end_lon
        )

        if distance < nearest_distance:
            nearest_distance = distance
            nearest_row = row
            nearest_point_lat = closest_lat
            nearest_point_lon = closest_lon

    # Check if the nearest segment is within the maximum distance threshold
    if nearest_row is None or nearest_distance > max_dist:
        return None

    # Build and return the response (without house_number since this is just a street)
    return AddressValidationResponse(
        exists=True,
        address_id=None,  # No address, just a street
        street_name=str(nearest_row._mapping["street_name"]),
        city=str(nearest_row._mapping["city"]),
        postal_code=str(nearest_row._mapping["postal_code"])
        if nearest_row._mapping["postal_code"] is not None
        else None,
        house_number=None,  # No house number for street-only match
        latitude=round(nearest_point_lat, 6),  # Closest point on the street
        longitude=round(nearest_point_lon, 6),
        distance_km=round(nearest_distance, 2),
    )


def _to_response(
    s: Street, lat: Optional[float], lon: Optional[float]
) -> StreetAutocompleteResponse:
    sid = getattr(s, "id")
    name = getattr(s, "name")
    city = getattr(s, "city")
    postal_code = getattr(s, "postal_code")
    latitude = getattr(s, "latitude")
    longitude = getattr(s, "longitude")

    resp = StreetAutocompleteResponse(
        street_id=int(sid),
        name=str(name),
        city=str(city),
        postal_code=str(postal_code) if postal_code is not None else None,
        latitude=float(latitude),
        longitude=float(longitude),
        match_score=1.0,
    )
    if lat is not None and lon is not None:
        d = haversine_distance(lat, lon, float(latitude), float(longitude))
        resp.distance_km = round(d, 2)
    return resp


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8001, workers=1)
