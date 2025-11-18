"""Database initialization and utilities."""

import asyncio
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import sessionmaker
import aiosqlite

from advanced_search.phonetic import german_phonetic_phrase, cologne_phonetic_phrase
from .models import Base
from .utils import (
    calculate_fuzzy_score,
    calculate_fuzzy_score_normalized,
    haversine_distance,
    normalize_compact,
    normalize_string,
    consonant_key,
)

# SQLite database file path
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./autocomplete.db")
ASYNC_DATABASE_URL = os.getenv("ASYNC_DATABASE_URL", "sqlite+aiosqlite:///./autocomplete.db")
BASE_DIR = Path(__file__).resolve().parent
SPELLFIX_PATH = BASE_DIR / "spellfix.so"

# Create engine with optimizations for SQLite
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

# Custom creator for aiosqlite to register functions
async def get_aiosqlite_connection():
    """Create aiosqlite connection with custom functions registered."""
    conn = await aiosqlite.connect("./autocomplete.db", check_same_thread=False)

    # Register custom functions on the underlying connection
    await conn.enable_load_extension(True)
    if SPELLFIX_PATH.exists():
        try:
            await conn.load_extension(str(SPELLFIX_PATH))
        except:
            pass
    await conn.enable_load_extension(False)

    # Register Python functions
    _register_sql_functions_for_aiosqlite(conn)

    return conn


def _register_sql_functions_for_aiosqlite(conn):
    """Register custom SQL functions for aiosqlite connection."""

    # Create SQL functions
    conn._conn.create_function("fuzzy_score", 2, calculate_fuzzy_score, deterministic=True)
    conn._conn.create_function("fuzzy_score_norm", 2, calculate_fuzzy_score_normalized, deterministic=True)
    conn._conn.create_function("cons_key", 1, consonant_key, deterministic=True)

    def _prefix_bonus_impl(query: str, candidate: str) -> float:
        if not query or not candidate:
            return 0.0
        if candidate.startswith(query):
            return 0.08
        return 0.0

    conn._conn.create_function("prefix_bonus", 2, _prefix_bonus_impl, deterministic=True)

    def _distance_penalty_impl(base_score: float, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        try:
            distance = haversine_distance(lat1, lon1, lat2, lon2)
            penalty = 1.0 / (1.0 + (distance / 80.0))
            return max(0.1, base_score * penalty)
        except:
            return base_score

    conn._conn.create_function("distance_penalized", 5, _distance_penalty_impl, deterministic=True)


# Create async engine for async operations
async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    echo=False,
    pool_size=20,
    max_overflow=10,
)


@event.listens_for(engine, "connect")
def _load_sqlite_extensions_sync(dbapi_connection, _):
    """Load SQLite extensions (spellfix) for each new DB-API connection."""

    if not SPELLFIX_PATH.exists():
        _register_sql_functions(dbapi_connection)
        return

    try:
        dbapi_connection.enable_load_extension(True)
        dbapi_connection.load_extension(str(SPELLFIX_PATH))
    finally:
        try:
            dbapi_connection.enable_load_extension(False)
        except Exception:
            # Some SQLite builds may not support disabling; ignore failures.
            pass

    _register_sql_functions(dbapi_connection)





def _regexp(pattern, string):
    """SQLite REGEXP function implementation."""
    try:
        return re.search(pattern, string) is not None
    except TypeError:
        return False


def _register_sql_functions(dbapi_connection) -> None:
    """Register custom SQL functions used for scoring and distance penalties."""

    def _sql_fuzzy_score(query: str, candidate: str) -> float:
        if not query or not candidate:
            return 0.0

        try:
            _, score = calculate_fuzzy_score(query, candidate)
        except Exception:
            return 0.0

        return float(score or 0.0)

    def _sql_fuzzy_score_normalized(query_norm: str, candidate_norm: str) -> float:
        if not query_norm or not candidate_norm:
            return 0.0

        try:
            _, score = calculate_fuzzy_score_normalized(query_norm, candidate_norm)
        except Exception:
            return 0.0

        return float(score or 0.0)

    def _sql_prefix_bonus(query_compact: str, normalized_candidate: str) -> float:
        if not query_compact or not normalized_candidate:
            return 0.0

        match_len = 0
        for qc_char, cand_char in zip(query_compact, normalized_candidate):
            if qc_char != cand_char:
                break
            match_len += 1

        return float(min(match_len, 4) * 0.02)

    def _sql_distance_penalty(
        score: float,
        origin_lat: float,
        origin_lon: float,
        target_lat: float,
        target_lon: float,
    ) -> float:
        if score is None:
            return 0.0

        if (
            origin_lat is None
            or origin_lon is None
            or target_lat is None
            or target_lon is None
        ):
            return float(score)
        try:
            distance = haversine_distance(origin_lat, origin_lon, target_lat, target_lon)
        except Exception:
            return float(score)

        penalty = 1.0 / (1.0 + (distance / 80.0))
        return float(max(0.1, score * penalty))

    dbapi_connection.create_function("fuzzy_score", 2, _sql_fuzzy_score)
    dbapi_connection.create_function("fuzzy_score_norm", 2, _sql_fuzzy_score_normalized)
    dbapi_connection.create_function("cons_key", 1, consonant_key)
    dbapi_connection.create_function("prefix_bonus", 2, _sql_prefix_bonus)
    dbapi_connection.create_function("distance_penalized", 5, _sql_distance_penalty)
    dbapi_connection.create_function("REGEXP", 2, _regexp)

@event.listens_for(async_engine.sync_engine, "connect")
def _load_sqlite_extensions_async(dbapi_connection, connection_record):
    """Load SQLite extensions and functions for async DB-API connection."""

    loop = asyncio.get_event_loop()

    # Get the actual sqlite3 connection from aiosqlite wrapper
    actual_conn = dbapi_connection
    if hasattr(dbapi_connection, '_conn'):
        actual_conn = dbapi_connection._conn

    # Try to load spellfix extension
    if SPELLFIX_PATH.exists():
        try:
            coro = actual_conn.enable_load_extension(True)
            loop.run_until_complete(coro)
            coro = actual_conn.load_extension(str(SPELLFIX_PATH))
            loop.run_until_complete(coro)
            coro = actual_conn.enable_load_extension(False)
            loop.run_until_complete(coro)
        except Exception:
            pass

    # Register custom SQL functions
    _register_sql_functions(actual_conn)


# Create sessionmaker
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create async sessionmaker
AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


def init_db():
    """Initialize the database by creating all tables and ensuring derived columns."""
    Base.metadata.create_all(bind=engine)
    _ensure_street_normalized_column()
    _ensure_phonetic_columns()
    _ensure_trigram_index()
    _ensure_spellfix_index()


def get_db():
    """Get database session dependency for FastAPI."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_db():
    """Get async database session dependency for FastAPI."""
    async with AsyncSessionLocal() as session:
        yield session


def fuzzy_search_streets_sql(
    query: str,
    city: str = None,
    limit: int = 10,
    latitude: float = None,
    longitude: float = None,
) -> List[Dict[str, Any]]:
    """
    Perform fuzzy street search using SQL-based strategies.
    Much faster than loading all streets into Python.

    Args:
    dbapi_connection.create_function("fuzzy_score_norm", 2, _sql_fuzzy_score_normalized)
        query: Search query
        city: Optional city filter
        limit: Maximum results
        latitude/longitude: Optional coordinates for distance calculation

    Returns:
        List of street dictionaries with match scores
    """
    # Normalize query for better matching
    query_clean = query.strip().lower()

    # Build SQL query with multiple fuzzy matching strategies
    sql_parts = []
    params = {"limit": limit}

    # Strategy 1: Exact prefix match (highest priority)
    exact_sql = """
    SELECT
        id as street_id, name, city, postal_code, latitude, longitude,
        1.0 as match_score, 'exact' as match_type
    FROM streets
    WHERE LOWER(name) LIKE :query_prefix
    """
    if city:
        exact_sql += " AND LOWER(city) LIKE :city_filter"
        params["city_filter"] = f"{city.lower()}%"

    params["query_prefix"] = f"{query_clean}%"
    sql_parts.append(exact_sql)

    # Strategy 2: Word boundary matches
    if " " in query_clean:
        word_sql = """
        SELECT
            id as street_id, name, city, postal_code, latitude, longitude,
            0.95 as match_score, 'word' as match_type
        FROM streets
        WHERE LOWER(name) LIKE :query_word
        """
        if city:
            word_sql += " AND LOWER(city) LIKE :city_filter"

        params["query_word"] = f"%{query_clean}%"
        sql_parts.append(word_sql)

        # Multi-word fuzzy matching with typo tolerance per word
        words = query_clean.split()
        if len(words) >= 2:
            # For each word, generate fuzzy variants and create combined patterns
            for word_idx, word in enumerate(words):
                if len(word) >= 3:  # Only for words with 3+ chars
                    # Generate fuzzy patterns for this specific word
                    word_patterns = [
                        word,  # exact
                        f"{word[0]}_{word[1:]}"
                        if len(word) >= 2
                        else word,  # missing 2nd char
                        f"{word[: len(word) // 2]}_{word[len(word) // 2 + 1 :]}"
                        if len(word) >= 4
                        else word,  # missing middle char
                        f"{word[:-1]}_{word[-1]}"
                        if len(word) >= 2
                        else word,  # missing 2nd to last
                    ]

                    # Add common character insertions for German
                    if "h" not in word and len(word) >= 3:
                        # Try adding 'h' in different positions (common in German)
                        for i in range(1, min(4, len(word))):
                            word_patterns.append(f"{word[:i]}h{word[i:]}")

                    # Create full query patterns with fuzzy word
                    for pattern in word_patterns[:5]:  # Limit to 5 patterns per word
                        other_words = [
                            words[i] for i in range(len(words)) if i != word_idx
                        ]
                        if word_idx == 0:
                            full_pattern = f"{pattern} {' '.join(other_words)}"
                        else:
                            full_pattern = f"{' '.join(words[:word_idx])} {pattern}"
                            if word_idx < len(words) - 1:
                                full_pattern += f" {' '.join(words[word_idx + 1 :])}"

                        pattern_idx = len(
                            [
                                p
                                for p in params.keys()
                                if p.startswith(f"word_fuzzy_pattern_{word_idx}")
                            ]
                        )
                        param_name = f"word_fuzzy_pattern_{word_idx}_{pattern_idx}"

                        fuzzy_word_sql = f"""
                        SELECT
                            id as street_id, name, city, postal_code, latitude, longitude,
                            0.82 as match_score, 'word_fuzzy' as match_type
                        FROM streets
                        WHERE LOWER(name) LIKE :{param_name}
                        """
                        if city:
                            fuzzy_word_sql += " AND LOWER(city) LIKE :city_filter"

                        params[param_name] = f"%{full_pattern}%"
                        sql_parts.append(fuzzy_word_sql)

    # Strategy 3: Simplified single-character wildcard patterns for typos
    if len(query_clean) >= 3:
        # Generate simple but effective typo patterns
        simple_patterns = []

        # Pattern: wildcard for each position (handles missing/wrong char)
        for i in range(len(query_clean)):
            pattern = query_clean[:i] + "_" + query_clean[i + 1 :]
            simple_patterns.append(pattern + "%")

        # Pattern: extra character at each position
        for i in range(len(query_clean) + 1):
            pattern = query_clean[:i] + "_" + query_clean[i:]
            simple_patterns.append(pattern + "%")

        # Add patterns (limit to avoid performance issues)
        for i, pattern in enumerate(simple_patterns[:15]):
            param_name = f"typo_pattern_{i}"
            typo_sql = f"""
            SELECT
                id as street_id, name, city, postal_code, latitude, longitude,
                0.78 as match_score, 'typo' as match_type
            FROM streets
            WHERE LOWER(name) LIKE :{param_name}
            """
            if city:
                typo_sql += " AND LOWER(city) LIKE :city_filter"

            params[param_name] = pattern
            sql_parts.append(typo_sql)

    # Strategy 5: GLOB patterns for wildcard matching
    if len(query_clean) >= 4 and " " not in query_clean:  # Only for single words
        glob_sql = """
        SELECT
            id as street_id, name, city, postal_code, latitude, longitude,
            0.7 as match_score, 'glob' as match_type
        FROM streets
        WHERE LOWER(name) GLOB :glob_pattern
        """
        if city:
            glob_sql += " AND LOWER(city) LIKE :city_filter"

        # Create glob pattern with wildcards for single character variations
        glob_pattern = ""
        for i, char in enumerate(query_clean):
            if i > 0 and i < len(query_clean) - 1:
                # Allow single character substitution
                glob_pattern += f"[{char}?]"
            else:
                glob_pattern += char
        glob_pattern += "*"

        params["glob_pattern"] = glob_pattern
        sql_parts.append(glob_sql)

    # Combine all strategies with UNION and deduplication
    if sql_parts:
        final_sql = f"""
        WITH combined_results AS (
            {" UNION ALL ".join(sql_parts)}
        ),
        deduplicated AS (
            SELECT
                street_id, name, city, postal_code, latitude, longitude,
                MAX(match_score) as match_score,
                MIN(match_type) as match_type
            FROM combined_results
            GROUP BY street_id
        )
        """

        # Add distance calculation if coordinates provided
        if latitude is not None and longitude is not None:
            final_sql += """
            SELECT *,
                NULL as distance_km
            FROM deduplicated
            ORDER BY match_score DESC, name ASC
            LIMIT :limit
            """
            params["lat"] = latitude
            params["lon"] = longitude
        else:
            final_sql += """
            SELECT *, NULL as distance_km
            FROM deduplicated
            ORDER BY match_score DESC, name ASC
            LIMIT :limit
            """
    else:
        # No SQL parts generated, return empty result
        return []

    # Execute query
    with engine.connect() as conn:
        result = conn.execute(text(final_sql), params)
        return [dict(row._mapping) for row in result]


def generate_typo_patterns_sql(query: str) -> List[str]:
    """
    Generate SQL LIKE patterns for common typos with multi-word support.

    Args:
        query: Original query string

    Returns:
        List of SQL LIKE patterns
    """
    patterns = []

    if len(query) < 3:
        return patterns

    # Check if query contains multiple words
    words = query.strip().split()

    if len(words) > 1:
        # Multi-word fuzzy matching
        patterns.extend(_generate_multiword_patterns(words))
    else:
        # Single word fuzzy matching
        patterns.extend(_generate_singleword_patterns(query))

    return list(set(patterns))  # Remove duplicates


def _ensure_street_normalized_column() -> None:
    """Ensure normalized columns exist, are populated, and indexed."""

    with engine.begin() as conn:
        columns = {
            row._mapping["name"]
            for row in conn.execute(text("PRAGMA table_info(streets)"))
        }

        if "normalized_name" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE streets ADD COLUMN normalized_name TEXT DEFAULT ''"
                )
            )

        if "normalized_search" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE streets ADD COLUMN normalized_search TEXT DEFAULT ''"
                )
            )

        missing = conn.execute(
            text(
                "SELECT id, name FROM streets "
                "WHERE normalized_name IS NULL OR normalized_name = ''"
            )
        ).fetchall()

        if missing:
            for row in missing:
                normalized = normalize_compact(row.name or "")
                conn.execute(
                    text(
                        "UPDATE streets SET normalized_name = :normalized WHERE id = :id"
                    ),
                    {"normalized": normalized, "id": row.id},
                )

        missing_search = conn.execute(
            text(
                "SELECT id, name FROM streets "
                "WHERE normalized_search IS NULL OR normalized_search = ''"
            )
        ).fetchall()

        if missing_search:
            for row in missing_search:
                normalized_search = normalize_string(row.name or "")
                conn.execute(
                    text(
                        "UPDATE streets SET normalized_search = :normalized WHERE id = :id"
                    ),
                    {"normalized": normalized_search, "id": row.id},
                )

        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_street_normalized_name "
                "ON streets (normalized_name)"
            )
        )

        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_street_normalized_search "
                "ON streets (normalized_search)"
            )
        )


def _ensure_phonetic_columns() -> None:
    """Ensure phonetic columns are available and populated for streets."""

    with engine.begin() as conn:
        columns = {
            row._mapping["name"]
            for row in conn.execute(text("PRAGMA table_info(streets)"))
        }

        if "phonetic_german" not in columns:
            conn.execute(
                text("ALTER TABLE streets ADD COLUMN phonetic_german TEXT DEFAULT ''")
            )

        if "phonetic_cologne" not in columns:
            conn.execute(
                text("ALTER TABLE streets ADD COLUMN phonetic_cologne TEXT DEFAULT ''")
            )

        if "consonant_key" not in columns:
            conn.execute(
                text("ALTER TABLE streets ADD COLUMN consonant_key TEXT DEFAULT ''")
            )

        missing = conn.execute(
            text(
                "SELECT id, name FROM streets "
                "WHERE phonetic_german IS NULL OR phonetic_german = '' "
                "   OR phonetic_cologne IS NULL OR phonetic_cologne = '' "
                "   OR consonant_key IS NULL OR consonant_key = ''"
            )
        ).fetchall()

        if missing:
            for row in missing:
                source = row._mapping.get("name") or ""
                german = german_phonetic_phrase(source)
                cologne = cologne_phonetic_phrase(source)
                ckey = consonant_key(source)
                conn.execute(
                    text(
                        "UPDATE streets SET phonetic_german = :g, phonetic_cologne = :c, consonant_key = :ck "
                        "WHERE id = :id"
                    ),
                    {"g": german, "c": cologne, "ck": ckey, "id": row._mapping["id"]},
                )

        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_street_phonetic_german "
                "ON streets (phonetic_german)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_street_phonetic_cologne "
                "ON streets (phonetic_cologne)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_street_consonant_key "
                "ON streets (consonant_key)"
            )
        )


def _ensure_spellfix_index() -> None:
    """Ensure spellfix virtual table mirrors the current streets dataset."""

    if not SPELLFIX_PATH.exists():
        return

    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS street_spellfix USING spellfix1"
            )
        )

        street_total = conn.execute(
            text("SELECT COUNT(*) FROM streets WHERE normalized_name != ''")
        ).scalar()
        spellfix_total = conn.execute(
            text("SELECT COUNT(*) FROM street_spellfix")
        ).scalar()

        if street_total != spellfix_total:
            conn.execute(text("DELETE FROM street_spellfix"))
            conn.execute(
                text(
                    "INSERT INTO street_spellfix(rowid, word, rank) "
                    "SELECT id, normalized_name, 0 FROM streets "
                    "WHERE normalized_name != ''"
                )
            )


def _ensure_trigram_index() -> None:
    """Ensure trigram FTS index exists and reflects current street names."""

    with engine.begin() as conn:
        try:
            conn.execute(
                text(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS street_trigram "
                    "USING fts5(normalized_name, content='', tokenize='trigram')"
                )
            )
        except Exception:
            # FTS5 or trigram tokenizer not available; skip silently.
            return

        street_total = conn.execute(
            text("SELECT COUNT(*) FROM streets WHERE normalized_name != ''")
        ).scalar()
        trigram_total = conn.execute(
            text("SELECT COUNT(*) FROM street_trigram")
        ).scalar()

        if street_total != trigram_total:
            conn.execute(text("DROP TABLE IF EXISTS street_trigram"))
            conn.execute(
                text(
                    "CREATE VIRTUAL TABLE street_trigram "
                    "USING fts5(normalized_name, content='', tokenize='trigram')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO street_trigram(rowid, normalized_name) "
                    "SELECT id, normalized_name FROM streets "
                    "WHERE normalized_name != ''"
                )
            )


def _generate_multiword_patterns(words: List[str]) -> List[str]:
    """
    Generate fuzzy patterns for multi-word queries.
    Handles cases like "am banhof" -> "Am Bahnhof"
    """
    patterns = []

    # Generate patterns where each word can have typos
    for word_idx, word in enumerate(words):
        if len(word) >= 2:  # Lowered threshold for better matching
            word_patterns = _generate_word_typo_variants(word)[:5]  # More variants

            for pattern in word_patterns:
                # Build full pattern with this word having typos
                full_pattern_parts = []
                for i, w in enumerate(words):
                    if i == word_idx:
                        full_pattern_parts.append(pattern)
                    else:
                        full_pattern_parts.append(w)

                full_pattern = " ".join(full_pattern_parts)
                patterns.append(f"%{full_pattern}%")
                patterns.append(f"{full_pattern}%")  # Also try without leading %

    # More aggressive multi-word typo combinations
    if len(words) == 2:
        word1_variants = _generate_word_typo_variants(words[0])[:4]
        word2_variants = _generate_word_typo_variants(words[1])[:4]

        for w1 in word1_variants:
            for w2 in word2_variants:
                if w1 != words[0] or w2 != words[1]:  # Only if at least one has typo
                    patterns.append(f"%{w1} {w2}%")
                    patterns.append(f"{w1} {w2}%")

    # Add individual word matching patterns (very aggressive)
    if len(words) >= 2:
        for word in words:
            if len(word) >= 3:
                variants = _generate_word_typo_variants(word)[:3]
                for variant in variants:
                    patterns.append(f"%{variant}%")

    return patterns


def _generate_word_typo_variants(word: str) -> List[str]:
    """Generate typo variants for a single word."""
    variants = [word]  # Include original

    if len(word) < 2:
        return variants

    # Missing character (very common typo)
    for i in range(len(word)):
        variant = word[:i] + word[i + 1 :]
        if len(variant) >= 1:
            variants.append(variant)

    # Extra character (common insertions) - more comprehensive
    common_extras = ["h", "n", "m", "l", "r", "s", "t", "a", "e", "i", "o", "u"]
    for i in range(len(word) + 1):  # Include positions at end
        for extra in common_extras[:5]:  # More extras
            variant = word[:i] + extra + word[i:]
            if len(variant) <= len(word) + 2:  # Prevent too long variants
                variants.append(variant)

    # Character substitution - more comprehensive
    for i in range(len(word)):
        # Common substitutions including silent letters
        char_map = {
            "a": ["e", "o", "ä"],
            "e": ["a", "i"],
            "i": ["e", "o"],
            "o": ["a", "u", "ö"],
            "u": ["o", "ü"],
            "ä": ["a", "e"],
            "ö": ["o", "e"],
            "ü": ["u", "i"],
            "b": ["p", "v"],
            "p": ["b"],
            "d": ["t"],
            "t": ["d"],
            "k": ["g", "c"],
            "g": ["k"],
            "f": ["v", "ph"],
            "v": ["f", "b"],
            "s": ["z", "ss"],
            "z": ["s"],
            "c": ["k", "z"],
            "h": [""],  # Silent h
            "n": ["m"],
            "m": ["n"],
        }

        if word[i].lower() in char_map:
            for replacement in char_map[word[i].lower()]:
                if replacement == "":
                    # Remove character
                    variant = word[:i] + word[i + 1 :]
                else:
                    variant = word[:i] + replacement + word[i + 1 :]
                if len(variant) >= 1:
                    variants.append(variant)

    # Adjacent character swap
    for i in range(len(word) - 1):
        variant = word[:i] + word[i + 1] + word[i] + word[i + 2 :]
        variants.append(variant)

    # Double character -> single character (common in German)
    for i in range(len(word) - 1):
        if word[i] == word[i + 1]:
            variant = word[:i] + word[i + 1 :]
            variants.append(variant)

    return variants[:15]  # Increased limit for better matching


def _generate_singleword_patterns(query: str) -> List[str]:
    """Generate patterns for single word queries."""
    patterns = []

    # Pattern 1: Missing character (any position)
    for i in range(len(query)):
        pattern = query[:i] + "_" + query[i:]
        patterns.append(f"{pattern}%")

    # Pattern 2: Extra character (beginning/middle)
    for i in range(len(query)):
        pattern = query[:i] + "_" + query[i:]
        patterns.append(f"{pattern}%")

    # Pattern 3: Character substitution
    for i in range(min(3, len(query))):  # Only first 3 chars for performance
        pattern = query[:i] + "_" + query[i + 1 :]
        patterns.append(f"{pattern}%")

    # Pattern 4: Adjacent character swap
    for i in range(len(query) - 1):
        pattern = query[:i] + query[i + 1] + query[i] + query[i + 2 :]
        patterns.append(f"{pattern}%")

    return patterns


def search_with_suffix_expansion_sql(
    query: str,
    city: str = None,
    limit: int = 10,
    latitude: float = None,
    longitude: float = None,
) -> List[Dict[str, Any]]:
    """
    Search with intelligent German street suffix expansion using SQL.

    Args:
        query: Search query
        city: Optional city filter
        limit: Maximum results
        latitude/longitude: Optional coordinates

    Returns:
        List of street dictionaries
    """
    # Generate suffix variants
    suffix_variants = generate_german_suffix_variants(query)

    # Build SQL for each variant
    sql_parts = []
    params = {"limit": limit}

    for i, variant in enumerate(suffix_variants):
        variant_sql = f"""
        SELECT
            id as street_id, name, city, postal_code, latitude, longitude,
            CASE WHEN :variant_{i} = :original_query THEN 1.0 ELSE 0.98 END as match_score
        FROM streets
        WHERE LOWER(name) LIKE :variant_pattern_{i}
        """
        if city:
            variant_sql += " AND LOWER(city) LIKE :city_filter"

        params[f"variant_{i}"] = variant.lower()
        params[f"variant_pattern_{i}"] = f"{variant.lower()}%"
        sql_parts.append(variant_sql)

    if city:
        params["city_filter"] = f"{city.lower()}%"

    params["original_query"] = query.lower()

    # Combine with deduplication
    if sql_parts:
        final_sql = f"""
        WITH combined_results AS (
            {" UNION ALL ".join(sql_parts)}
        )
        SELECT
            street_id, name, city, postal_code, latitude, longitude,
            MAX(match_score) as match_score
        FROM combined_results
        GROUP BY street_id
        ORDER BY match_score DESC, name ASC
        LIMIT :limit
        """
    else:
        # No variants generated, return empty result
        return []

    with engine.connect() as conn:
        result = conn.execute(text(final_sql), params)
        return [dict(row._mapping) for row in result]


def generate_german_suffix_variants(query: str) -> List[str]:
    """
    Generate German street suffix variants for SQL search.

    Args:
        query: Original query

    Returns:
        List of query variants
    """
    query_lower = query.lower().strip()
    variants = [query]

    # Common German street suffixes
    suffix_map = {
        "s": "straße",
        "st": "straße",
        "str": "straße",
        "stra": "straße",
        "straß": "straße",
        "w": "weg",
        "we": "weg",
        "al": "allee",
        "all": "allee",
        "alle": "allee",
        "pl": "platz",
        "pla": "platz",
        "plat": "platz",
        "g": "gasse",
        "ga": "gasse",
        "gas": "gasse",
        "gass": "gasse",
        "r": "ring",
        "ri": "ring",
        "rin": "ring",
        "d": "damm",
        "da": "damm",
        "dam": "damm",
        "ho": "hof",
        "par": "park",
        "ber": "berg",
    }

    for suffix, full_suffix in suffix_map.items():
        if query_lower.endswith(suffix) and len(query_lower) > len(suffix) + 1:
            base = query_lower[: -len(suffix)]
            variants.append(base + full_suffix)

    return list(set(variants))
