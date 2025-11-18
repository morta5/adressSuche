"""Advanced Street Autocomplete API v2."""
import asyncio
import math
from typing import List, Optional, Tuple, Dict, Any

from fastapi import Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text, or_
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_async_db, init_db
from .models import Street, Address
from .schemas import StreetAutocompleteResponse, AddressValidationResponse
from .utils import (
    haversine_distance,
    normalize_string,
    normalize_compact,
    calculate_fuzzy_score_normalized,
    consonant_key,
)
from advanced_search.query_processor import QueryProcessor
from advanced_search.phonetic import phonetic_match_score, phonetic_forms


async def _geo_bounds(lat: float, lon: float, radius_km: float) -> Tuple[float, float, float, float]:
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
    qc = normalize_compact(query)
    qn = normalize_string(query)

    # Query expansion (abbr/suffix/hyphen)
    expanded_queries = QueryProcessor.expand_query(query)
    expanded_norm = list(dict.fromkeys([normalize_string(q) for q in expanded_queries]))

    processed_ids = set()
    candidates: list[tuple[StreetAutocompleteResponse, float]] = []

    # Stage A: Exact prefix on name and normalized_name across variants
    async def exact_prefix() -> list[tuple[StreetAutocompleteResponse, float, int]]:
        local = []
        for v in expanded_queries:
            stmt = select(Street).where(Street.name.ilike(f"{v}%"))
            if city:
                stmt = stmt.where(Street.city.ilike(f"{city}%"))
            stmt = stmt.limit(limit * 3)
            res = await db.execute(stmt)
            for s in res.scalars().all():
                sc = 1.0 if v == query else 0.98
                local.append((_to_response(s, latitude, longitude), sc, s.id))
        # Also try normalized_name prefix with compact normalization
        if qc:
            stmt2 = select(Street).where(Street.normalized_name.ilike(f"{qc}%"))
            if city:
                stmt2 = stmt2.where(Street.city.ilike(f"{city}%"))
            stmt2 = stmt2.limit(limit * 3)
            res2 = await db.execute(stmt2)
            for s in res2.scalars().all():
                sc = 0.97
                local.append((_to_response(s, latitude, longitude), sc, s.id))
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
            nn = r._mapping.get("nn") or normalize_compact(r._mapping["name"])  # fallback
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
                d = haversine_distance(latitude, longitude, resp.latitude, resp.longitude)
                resp.distance_km = round(d, 2)
                base = _distance_penalized(base, d)
            local.append((resp, base, resp.street_id))
        return local

    # Stage C: SQL fuzzy LIKE on normalized_search using generated patterns
    async def sql_typos() -> list[tuple[StreetAutocompleteResponse, float, int]]:
        if len(qn) < 3:
            return []
        patterns = [qn]
        # Single-wildcard patterns around the string
        for i in range(min(6, len(qn))):
            patterns.append(qn[:i] + '_' + qn[i + 1 :])
        # Vowel-insensitive: replace vowels by wildcard
        vowels = set('aeiou')
        vw = ''.join('_' if c in vowels else c for c in qn)
        patterns.append(vw)
        local = []
        added = set()
        for p in patterns[:8]:
            stmt = select(Street).where(Street.normalized_search.like(f"%{p}%")).limit(max(50, limit * 8))
            if city:
                stmt = stmt.where(Street.city.ilike(f"{city}%"))
            res = await db.execute(stmt)
            for s in res.scalars().all():
                if s.id in added:
                    continue
                added.add(s.id)
                cand_norm = normalize_compact(getattr(s, "name"))
                sc = 0.62 + _prefix_bonus(qc, cand_norm)
                local.append((_to_response(s, latitude, longitude), sc, s.id))
        return local

    # Stage D: Phonetic search using precomputed phonetic codes
    async def phonetic_stage() -> list[tuple[StreetAutocompleteResponse, float, int]]:
        german_codes, cologne_codes = _collect_phonetic_codes([query, *expanded_norm])
        if not german_codes and not cologne_codes:
            return []

        # Use approximate matching on phonetic codes with constraints to keep it fast
        qg, qc_ph = phonetic_forms(query)
        q_cons = consonant_key(query)
        params: Dict[str, Any] = {
            "qg": qg or "",
            "qc": qc_ph or "",
            "qcons": q_cons or "",
            "gmin": max(1, (len(qg) - 3) if qg else 1),
            "gmax": (len(qg) + 3) if qg else 99,
            "cmin": max(1, (len(qc_ph) - 3) if qc_ph else 1),
            "cmax": (len(qc_ph) + 3) if qc_ph else 99,
            "prelimit": max(300, min(800, limit * 60)),
        }

        where_core = [
                "( (substr(phonetic_german,1,1) = substr(:qg,1,1) AND length(phonetic_german) BETWEEN :gmin AND :gmax)",
                "  OR (substr(phonetic_cologne,1,1) = substr(:qc,1,1) AND length(phonetic_cologne) BETWEEN :cmin AND :cmax)",
                "  OR (substr(consonant_key,1,1) = substr(:qcons,1,1)) )",
        ]
        if city:
            where_core.append("city LIKE :city")
            params["city"] = f"{city}%"

        sql = [
            "WITH cand AS (",
            "  SELECT id, name, city, postal_code, latitude, longitude, phonetic_german pg, phonetic_cologne pc",
            "  FROM streets",
            "  WHERE " + " AND ".join(where_core),
            "  LIMIT :prelimit",
            ")",
                "SELECT id, name, city, postal_code, latitude, longitude, pg, pc, ck,",
                "       MAX(fuzzy_score_norm(pg, :qg), fuzzy_score_norm(pc, :qc)) AS ph,",
                "       fuzzy_score_norm(ck, :qcons) AS cs,",
                "       (0.55 * MAX(fuzzy_score_norm(pg, :qg), fuzzy_score_norm(pc, :qc)) + 0.45 * fuzzy_score_norm(ck, :qcons)) AS ph_total",
            "FROM cand",
            "ORDER BY ph_total DESC, name ASC",
            "LIMIT :limit",
        ]
        params["limit"] = max(60, limit * 6)

        try:
            rows = (await db.execute(text("\n".join(sql)), params)).fetchall()
        except Exception:
            return []

        local: list[tuple[StreetAutocompleteResponse, float, int]] = []
        for r in rows:
            name = r._mapping["name"]
            base = 0.8 + min(0.15, float(r._mapping.get("ph") or 0) * 0.2)
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
                d = haversine_distance(latitude, longitude, resp.latitude, resp.longitude)
                resp.distance_km = round(d, 2)
                base = _distance_penalized(base, d)
            local.append((resp, base, resp.street_id))
        return local

    # Stage E: Broad prefix fallback + rerank (guarantee recall)
    async def broad_prefix_fallback() -> list[tuple[StreetAutocompleteResponse, float, int]]:
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
            stmt = select(Street).where(Street.normalized_search.like(f"{p}%")).limit(prelimit)
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
                _, fuzz = calculate_fuzzy_score_normalized(normalize_string(query), normalize_string(resp.name))
                base = 0.4 + _prefix_bonus(qc, normalize_compact(resp.name))
                score = min(1.0, 0.4 * base + 0.4 * ph + 0.2 * fuzz)
                if latitude is not None and longitude is not None and resp.distance_km is not None:
                    score = _distance_penalized(score, resp.distance_km)
                resp.match_score = score
                local.append((resp, score, resp.street_id))
        # Return already reranked subset
        local.sort(key=lambda t: (-t[1], t[0].name, t[0].city))
        return local[: max(limit * 5, 50)]

    # Run retrieval stages sequentially to avoid concurrent DB operations on the same session
    stage_a = await exact_prefix()
    stage_b = await trigram_search()
    stage_c = await sql_typos()
    stage_d = await phonetic_stage()
    # If everything above is weak, run a broad prefix fallback to guarantee recall
    stage_e = []
    if not (stage_a or stage_b or stage_c or stage_d):
        stage_e = await broad_prefix_fallback()
    flat: list[tuple[StreetAutocompleteResponse, float, int]] = [*stage_a, *stage_b, *stage_c, *stage_d, *stage_e]

    # Dedup + keep best score
    by_id: Dict[int, tuple[StreetAutocompleteResponse, float]] = {}
    for resp, sc, sid in flat:
        if sid in by_id:
            if sc > by_id[sid][1]:
                by_id[sid] = (resp, sc)
        else:
            by_id[sid] = (resp, sc)

    # Optional phonetic + normalized fuzzy reranking on top K candidates
    top_candidates = sorted(by_id.values(), key=lambda t: t[1], reverse=True)[: max(limit * 8, 100)]
    reranked: list[tuple[StreetAutocompleteResponse, float]] = []
    qn_norm = normalize_string(query)
    for resp, base in top_candidates:
        ph = phonetic_match_score(query, resp.name)
        _, fuzz = calculate_fuzzy_score_normalized(qn_norm, normalize_string(resp.name))
        combined = min(1.0, 0.55 * base + 0.30 * ph + 0.15 * fuzz)
        if latitude is not None and longitude is not None and resp.distance_km is not None:
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
    latitude: Optional[float] = Query(None, description="Latitude for distance calculation"),
    longitude: Optional[float] = Query(None, description="Longitude for distance calculation"),
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
            conditions.append(Street.phonetic_german.like(f"{qg[: max(1, len(qg)-1)]}%"))
        if qc_ph:
            conditions.append(Street.phonetic_cologne.like(f"{qc_ph[: max(1, len(qc_ph)-1)]}%"))
        stmt2 = select(Street).where(or_(*conditions)).limit(60)
        if city:
            stmt2 = stmt2.where(Street.city.ilike(f"{city}%"))
        res2 = await db.execute(stmt2)
        streets = res2.scalars().all()

    # Check addresses on found streets
    for st in streets:
        a_stmt = select(Address).where(Address.street_id == st.id, Address.house_number == house_number).limit(1)
        ares = await db.execute(a_stmt)
        addr = ares.scalars().first()
        if addr:
            resp = AddressValidationResponse(
                exists=True,
                address_id=int(getattr(addr, "id")),
                street_name=str(getattr(st, "name")),
                city=str(getattr(st, "city")),
                postal_code=str(getattr(st, "postal_code")) if getattr(st, "postal_code") is not None else None,
                house_number=str(getattr(addr, "house_number")),
                latitude=float(getattr(addr, "latitude")),
                longitude=float(getattr(addr, "longitude")),
            )
            if latitude is not None and longitude is not None:
                resp.distance_km = round(haversine_distance(float(latitude), float(longitude), float(getattr(addr, "latitude")), float(getattr(addr, "longitude"))), 2)
            return resp

    return AddressValidationResponse(exists=False)


def _to_response(s: Street, lat: Optional[float], lon: Optional[float]) -> StreetAutocompleteResponse:
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
    uvicorn.run("v2.main:app", host="0.0.0.0", port=8001, workers=4)
