"""Advanced Street Autocomplete API v2."""

import asyncio
import logging
import math
import sqlite3
from typing import List, Optional, Tuple, Dict, Any

from fastapi import Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text, or_
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
    calculate_fuzzy_score_normalized,
    consonant_key,
)

# Constants for search quality thresholds
HIGH_QUALITY_SCORE_THRESHOLD = 0.7  # Score threshold for early exit optimization
FUZZY_TRIGRAM_CANDIDATE_LIMIT = 3000  # Max candidates to fetch from trigram search

# Unicode constants for prefix range queries
UNICODE_MAX_CODEPOINT = 0x10FFFF  # Maximum valid Unicode code point
UNICODE_FALLBACK_UPPER = "\uffff"  # Fallback upper bound for prefix range

# Thread-safe lazy loading using threading.Lock
import threading


async def _geo_bounds(
    lat: float, lon: float, radius_km: float
) -> Tuple[float, float, float, float]:
    lat_deg = radius_km / 110.574
    lon_deg = radius_km / (111.320 * max(0.0001, math.cos(math.radians(lat))))
    return lat - lat_deg, lat + lat_deg, lon - lon_deg, lon + lon_deg


def _distance_penalized(score: float, geo_distance: Optional[float]) -> float:
    if geo_distance is None:
        return score
    # Gentle penalty to keep relevant far results
    if score >= 0.7:
        k = 220.0
    elif score >= 0.5:
        k = 150.0
    else:
        k = 100.0
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
_known_cities_lock = threading.Lock()


def _get_known_cities(db_path: str = "./autocomplete.db") -> set[str]:
    """Load known city names from the database (cached, case-insensitive)."""
    global _known_cities

    if _known_cities is not None:
        return _known_cities

    with _known_cities_lock:
        if _known_cities is not None:
            return _known_cities

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT city FROM streets WHERE city IS NOT NULL")
            cities = {row[0].lower() for row in cursor.fetchall() if row[0]}
            conn.close()
            _known_cities = cities
            return _known_cities
        except Exception as e:
            logger.debug(f"Failed to load cities: {e}")
            return set()


def _extract_city_from_query(
    query: str, known_cities: set[str]
) -> Tuple[str, Optional[str]]:
    """
    Extract city name from the end of query if present.

    For query "jungfernstieg hamburg", returns ("jungfernstieg", "Hamburg").
    For query "hauptstraße berlin mitte", returns ("hauptstraße", "Berlin Mitte").
    For query "bahnhofstraße", returns ("bahnhofstraße", None).

    Returns:
        Tuple of (street_query, detected_city)
    """
    parts = query.strip().split()
    if len(parts) < 2:
        return query, None

    # Try to match last N words as a city (N from 1 to min(3, len(parts)-1))
    for n in range(min(3, len(parts) - 1), 0, -1):
        potential_city = " ".join(parts[-n:]).lower()

        # Check if this is a known city
        if potential_city in known_cities:
            street_query = " ".join(parts[:-n])
            # Return city with original casing from query
            detected_city = " ".join(parts[-n:])
            return street_query, detected_city

    return query, None


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





@app.on_event("startup")
async def _on_startup():
    init_db()


@app.get("/")
async def root():
    return {"message": "Street Autocomplete API v2", "endpoint": "/autocomplete"}


@app.get("/autocomplete", response_model=List[StreetAutocompleteResponse])
async def autocomplete(
    query: str = Query(..., min_length=1),
    city: Optional[str] = Query(None),
    latitude: Optional[float] = Query(None),
    longitude: Optional[float] = Query(None),
    limit: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_async_db),
):
    # Extract city from query if not provided explicitly
    # E.g., "jungfernstieg hamburg" -> query="jungfernstieg", city="hamburg"
    if city is None:
        known_cities = _get_known_cities()
        query, detected_city = _extract_city_from_query(query, known_cities)
        if detected_city:
            city = detected_city
            logger.debug(f"Extracted city '{city}' from query, street query: '{query}'")

    qc = normalize_compact(query)
    qn = normalize_string(query)

    # Query expansion (abbr/suffix/hyphen)
    expanded_queries = QueryProcessor.expand_query(query)
    expanded_norm = list(dict.fromkeys([normalize_string(q) for q in expanded_queries]))

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
        params: Dict[str, Any] = {
            "q1_start": query,
            "q1_end": prefix_end(query),
            "limit": limit * 4,
        }

        # Build the SQL with UNION to use index on both queries
        if qc and qc != query:
            # Both original name and normalized name
            params["q2_start"] = qc
            params["q2_end"] = prefix_end(qc)
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

        if city:
            # Wrap with city filter - explicitly list columns instead of SELECT *
            params["city"] = f"{city}%"
            sql = f"SELECT id, name, city, postal_code, latitude, longitude FROM ({sql}) WHERE city LIKE :city"

        sql += " LIMIT :limit"

        try:
            res = await db.execute(text(sql), params)
            for r in res.fetchall():
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
                    sc = _distance_penalized(sc, d)
                local.append((resp, sc, sid))
        except Exception:
            pass
        return local

    # Stage B: Trigram FTS search on normalized_name
    async def trigram_search() -> list[tuple[StreetAutocompleteResponse, float, int]]:
        if len(qc) < 2:
            return []
        trigram_limit = max(limit * 25, 400)
        params: Dict[str, Any] = {"pattern": f"{qc}*", "limit": trigram_limit}
        sql = [
            "SELECT s.id AS street_id, s.name, s.city, s.postal_code, s.latitude, s.longitude,",
            "       bm25(street_trigram) AS rnk, s.normalized_name AS nn",
            "FROM street_trigram JOIN streets s ON s.id = street_trigram.rowid",
            "WHERE street_trigram MATCH :pattern",
        ]
        if city:
            sql.append("AND s.city LIKE :city")
            params["city"] = f"{city}%"
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
                base = _distance_penalized(base, d)
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
            sql += " AND city LIKE :city"
            params["city"] = f"{city}%"
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
                    sc = _distance_penalized(sc, d)
                local.append((resp, sc, sid))
        except Exception:
            pass
        return local

    # Stage D: Phonetic search using precomputed phonetic codes (SIMPLIFIED for speed)
    async def phonetic_stage() -> list[tuple[StreetAutocompleteResponse, float, int]]:
        qg, qc_ph = phonetic_forms(query)
        q_cons = consonant_key(query)

        if not qg and not qc_ph and not q_cons:
            return []

        # Simpler, faster phonetic query - just use index prefix matching
        params: Dict[str, Any] = {
            "prelimit": max(100, min(300, limit * 30)),
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
            sql += " AND city LIKE :city"
            params["city"] = f"{city}%"
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
                base = _distance_penalized(base, d)
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
        """Use trigram AND matching to find candidates with typos."""
        qc_lower = qc.lower() if qc else ""
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

        # Strategy: Use multiple AND trigrams from the middle/end of the string
        # These are less affected by typos at the beginning
        # For "galbelstrasse", use trigrams like "bel", "els", "lst", "str"
        # which match "geibelstrasse"

        if len(all_trigrams) >= 6:
            # Use 4 trigrams from the middle portion (indices 3-6 typically)
            # This covers the suffix which is more stable
            start_idx = max(1, len(all_trigrams) // 3)
            and_trigrams = all_trigrams[start_idx : start_idx + 4]
        elif len(all_trigrams) >= 4:
            and_trigrams = all_trigrams[
                1:5
            ]  # Skip first trigram (most likely to have typo)
        else:
            and_trigrams = all_trigrams

        and_pattern = " AND ".join(and_trigrams)

        params: Dict[str, Any] = {
            "pattern": and_pattern,
            "limit": FUZZY_TRIGRAM_CANDIDATE_LIMIT,
        }

        sql = [
            "SELECT s.id AS street_id, s.name, s.city, s.postal_code, s.latitude, s.longitude,",
            "       s.normalized_name AS nn",
            "FROM street_trigram JOIN streets s ON s.id = street_trigram.rowid",
            "WHERE street_trigram MATCH :pattern",
        ]
        if city:
            sql.append("AND s.city LIKE :city")
            params["city"] = f"{city}%"
        sql.append("LIMIT :limit")

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
                base = _distance_penalized(base, d)
            local.append((resp, base, resp.street_id))

        # Sort by score
        local.sort(key=lambda t: (-t[1], t[0].name))
        return local[: max(limit * 5, 50)]

    # Run retrieval stages with early exit optimization
    # Fast stages first, skip slow stages if we have enough results

    # Stage A: Exact prefix (fast, uses index)
    stage_a = await exact_prefix()

    # Check if Stage A found any exact prefix matches (score ~= 1.0 or 0.97)
    # If so, skip expensive fuzzy stages as we likely have good results
    stage_a_has_matches = len(stage_a) > 0
    stage_a_has_good_match = any(
        sc >= HIGH_QUALITY_SCORE_THRESHOLD for _, sc, _ in stage_a
    )

    # Stage B: Trigram prefix search (fast, uses FTS5 index)
    # Only run if Stage A didn't find enough results
    stage_b = []
    if len(stage_a) < limit:
        stage_b = await trigram_search()

    # Early exit check after fast stages
    fast_results = [*stage_a, *stage_b]
    has_good_fast_results = len(fast_results) >= limit or (
        stage_a_has_matches and stage_a_has_good_match
    )

    # Stage G: Fuzzy trigram OR (moderate speed, good for typos)
    # ONLY run if we don't have good results from exact matching
    # This is expensive for queries with common suffixes like "straße"
    stage_g = []
    if not has_good_fast_results:
        stage_g = await fuzzy_trigram_search()

    # Combined results
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
    top_candidates = sorted(by_id.values(), key=lambda t: t[1], reverse=True)[
        : max(limit * 8, 100)
    ]
    reranked: list[tuple[StreetAutocompleteResponse, float]] = []
    qn_norm = normalize_string(query)
    for resp, base in top_candidates:
        ph = phonetic_match_score(query, resp.name)
        _, fuzz = calculate_fuzzy_score_normalized(qn_norm, normalize_string(resp.name))
        combined = min(1.0, 0.55 * base + 0.30 * ph + 0.15 * fuzz)
        if (
            latitude is not None
            and longitude is not None
            and resp.distance_km is not None
        ):
            combined = _distance_penalized(combined, resp.distance_km)
        resp.match_score = combined
        reranked.append((resp, combined))

    reranked.sort(key=lambda t: (-(t[1]), t[0].name, t[0].city))
    return [r[0] for r in reranked[:limit]]


@app.get("/validate", response_model=AddressValidationResponse)
async def validate_address(
    street_name: str = Query(..., description="Street name"),
    house_number: str = Query(..., description="House number"),
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

    # Try strict normalized match first
    stmt = select(Street).where(Street.normalized_search == target_norm)
    if city:
        stmt = stmt.where(Street.city.ilike(f"{city}%"))
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
        stmt2 = select(Street).where(or_(*conditions)).limit(60)
        if city:
            stmt2 = stmt2.where(Street.city.ilike(f"{city}%"))
        res2 = await db.execute(stmt2)
        streets = res2.scalars().all()

    # Check addresses on found streets
    for st in streets:
        a_stmt = (
            select(Address)
            .where(Address.street_id == st.id, Address.house_number == house_number)
            .limit(1)
        )
        ares = await db.execute(a_stmt)
        addr = ares.scalars().first()
        if addr:
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
