"""Import streets and addresses from an OSM PBF file into the database.

This version focuses on streaming inserts and minimal in-memory footprint so that
large extracts (e.g. the full Germany dataset) can be processed without running
out of RAM. Streets and addresses are deduplicated via database constraints and
`ON CONFLICT` upserts.
"""

from __future__ import annotations

import gc
import os
import logging
import argparse
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from collections import OrderedDict

from osmium import SimpleHandler
from osmium.geom import WKBFactory
from shapely import wkb
from shapely.ops import polygonize
from shapely.geometry import MultiPolygon, Point
from shapely.strtree import STRtree
from sqlalchemy import select, update, or_, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

import database
from database import init_db, configure_db
from models import Address, Street, StreetSegment


class BoundaryRelationCollector(SimpleHandler):
    """Collects relation metadata for postal and administrative areas."""

    def __init__(self) -> None:
        super().__init__()
        self.relations: List[Dict[str, object]] = []
        self.required_way_ids: set[int] = set()

    def relation(self, relation):  # type: ignore[override]
        tags = relation.tags
        boundary = tags.get('boundary')
        if boundary not in {'postal_code', 'administrative'}:
            return

        if boundary == 'postal_code':
            postal_code = tags.get('postal_code') or tags.get('ref')
            if not postal_code:
                return
            record: Dict[str, object] = {
                'boundary': boundary,
                'postal_code': postal_code,
                'member_way_ids': [],
            }
        else:
            admin_level = tags.get('admin_level')
            name = tags.get('name')
            if not admin_level or not name:
                return
            record = {
                'boundary': boundary,
                'admin_level': admin_level,
                'name': name,
                'ref': tags.get('ref'),
                'member_way_ids': [],
            }

        for member in relation.members:
            if member.type == 'w' and member.role in {'outer', ''}:
                record['member_way_ids'].append(member.ref)  # type: ignore[index]
                self.required_way_ids.add(member.ref)

        if record['member_way_ids']:  # type: ignore[index]
            self.relations.append(record)


class FilteredWayCollector(SimpleHandler):
    """Collect geometries only for the set of required way IDs."""

    def __init__(self, way_ids: Iterable[int]) -> None:
        super().__init__()
        self.required_way_ids = set(way_ids)
        self.ways: Dict[int, object] = {}
        self._factory = WKBFactory()

    def way(self, way):  # type: ignore[override]
        if way.id not in self.required_way_ids:
            return

        try:
            geom_wkb = self._factory.create_linestring(way)
            geom = wkb.loads(geom_wkb)
            if geom.is_empty or not geom.is_valid:
                return
            self.ways[way.id] = geom
        except Exception:
            return


def _build_geometries(relations: Sequence[Dict[str, object]], ways: Dict[int, object]) -> Tuple[
    Dict[str, MultiPolygon],
    Dict[str, MultiPolygon],
    Dict[str, MultiPolygon],
    Dict[str, MultiPolygon],
    Dict[str, str],
    Dict[str, MultiPolygon],
    Dict[str, MultiPolygon],
]:
    """Assemble shapely multipolygons for all stored relation records."""

    postal_areas: Dict[str, MultiPolygon] = {}
    level8_areas: Dict[str, MultiPolygon] = {}
    level7_areas: Dict[str, MultiPolygon] = {}
    level6_areas: Dict[str, MultiPolygon] = {}
    municipality_refs: Dict[str, str] = {}
    borough_areas: Dict[str, MultiPolygon] = {}
    suburb_areas: Dict[str, MultiPolygon] = {}

    for record in relations:
        member_ids: List[int] = record['member_way_ids']  # type: ignore[assignment]
        member_lines = [ways.get(member_id) for member_id in member_ids]
        line_geoms = [geom for geom in member_lines if geom is not None]
        if not line_geoms:
            continue

        polygons = list(polygonize(line_geoms))
        if not polygons:
            if record.get('admin_level') == '6':
                logger.warning("Failed to polygonize Level 6 area: %s (missing segments or open ring)", record.get('name'))
            continue

        multipolygon = MultiPolygon(polygons)
        if multipolygon.is_empty or not multipolygon.is_valid:
            multipolygon = multipolygon.buffer(0)
        
        # Simplify geometry to reduce memory usage
        multipolygon = multipolygon.simplify(0.0001, preserve_topology=True)
        
        if multipolygon.is_empty:
            continue

        if record['boundary'] == 'postal_code':
            postal_code = record['postal_code']  # type: ignore[assignment]
            postal_areas[str(postal_code)] = multipolygon
        else:
            admin_level = str(record['admin_level'])
            name = str(record['name'])
            
            if admin_level == '8':
                level8_areas[name] = multipolygon
                ref = record.get('ref')
                if ref:
                    municipality_refs[name] = str(ref)
            elif admin_level == '7':
                level7_areas[name] = multipolygon
            elif admin_level == '6':
                level6_areas[name] = multipolygon
                logger.info("Built geometry for Level 6 city: %s", name)
            elif admin_level == '9':
                borough_areas[name] = multipolygon
            elif admin_level == '10':
                suburb_areas[name] = multipolygon

    return postal_areas, level8_areas, level7_areas, level6_areas, municipality_refs, borough_areas, suburb_areas


class AreaIndex:
    """Spatial index helper around shapely STRtree."""

    def __init__(self, areas: Dict[str, MultiPolygon]) -> None:
        self.keys = list(areas.keys())
        self.geometries = list(areas.values())
        self.tree = STRtree(self.geometries) if self.geometries else None

    def find(self, point: Point) -> Optional[str]:
        if not self.tree:
            return None

        # Query returns geometry indices, not objects
        try:
            candidates = self.tree.query(point, predicate='contains')
        except TypeError:
            candidates = self.tree.query(point)

        best_match = None
        min_area = float('inf')

        # candidates are indices into self.geometries
        for idx in candidates:
            idx = int(idx)
            if 0 <= idx < len(self.geometries):
                geom = self.geometries[idx]
                # Double-check containment for query() without predicate
                if geom.contains(point):
                    area = geom.area
                    if area < min_area:
                        min_area = area
                        best_match = self.keys[idx]
        
        return best_match

    def nearest(self, point: Point) -> Tuple[Optional[str], float]:
        if not self.tree:
            return None, float('inf')

        try:
            idx = self.tree.nearest(point)
        except (TypeError, AttributeError):
            return None, float('inf')
        
        if idx is None:
            return None, float('inf')

        idx = int(idx)
        if 0 <= idx < len(self.keys):
            # Calculate actual distance
            geom = self.geometries[idx]
            dist = geom.distance(point)
            return self.keys[idx], dist
        
        return None, float('inf')


class AreaLookup:
    """Container for all administrative lookup trees."""

    def __init__(
        self,
        postal_areas: Dict[str, MultiPolygon],
        level8_areas: Dict[str, MultiPolygon],
        level7_areas: Dict[str, MultiPolygon],
        level6_areas: Dict[str, MultiPolygon],
        municipality_refs: Dict[str, str],
        borough_areas: Dict[str, MultiPolygon],
        suburb_areas: Dict[str, MultiPolygon],
    ) -> None:
        self.postal_index = AreaIndex(postal_areas)
        self.level8_index = AreaIndex(level8_areas)
        self.level7_index = AreaIndex(level7_areas)
        self.level6_index = AreaIndex(level6_areas)
        self.borough_index = AreaIndex(borough_areas)
        self.suburb_index = AreaIndex(suburb_areas)
        self.municipality_refs = municipality_refs
        
        # Map children to Level 8 (municipality)
        self._borough_to_municipality = self._map_children_to_parent(borough_areas, self.level8_index)
        self._suburb_to_municipality = self._map_children_to_parent(suburb_areas, self.level8_index)

    def lookup(self, point: Point) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
        postal = self.postal_index.find(point)
        if not postal:
            postal, _ = self.postal_index.nearest(point)

        level8 = self.level8_index.find(point)
        level7 = self.level7_index.find(point)
        level6 = self.level6_index.find(point)
        borough = self.borough_index.find(point)
        suburb = self.suburb_index.find(point)

        # Try to find municipality from children if direct lookup fails
        if not level8 and borough:
            level8 = self._borough_to_municipality.get(borough)
        if not level8 and suburb:
            level8 = self._suburb_to_municipality.get(suburb)
        
        # Last resort: use nearest municipality (with reasonable distance limit)
        # Only if we haven't found a higher-level city (level 6 or 7)
        if not level8 and not level6 and not level7:
            # Find nearest candidates from all relevant levels
            l8_name, l8_dist = self.level8_index.nearest(point)
            l6_name, l6_dist = self.level6_index.nearest(point)
            l7_name, l7_dist = self.level7_index.nearest(point)
            
            # Find the absolute closest match
            candidates = []
            if l8_name: candidates.append((l8_dist, l8_name, '8'))
            if l6_name: candidates.append((l6_dist, l6_name, '6'))
            if l7_name: candidates.append((l7_dist, l7_name, '7'))
            
            if candidates:
                candidates.sort(key=lambda x: x[0])
                best_dist, best_name, best_level = candidates[0]
                
                if best_level == '8':
                    level8 = best_name
                elif best_level == '6':
                    level6 = best_name
                elif best_level == '7':
                    level7 = best_name

        regional_key = self.municipality_refs.get(level8) if level8 else None
        return postal, level8, level7, level6, borough, suburb, regional_key

    def _map_children_to_parent(self, areas: Dict[str, MultiPolygon], parent_index: AreaIndex) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for name, geom in areas.items():
            try:
                representative = geom.representative_point()
            except Exception:
                continue
            parent = parent_index.find(representative)
            if not parent:
                parent, _ = parent_index.nearest(representative)
            if parent:
                mapping[name] = parent
        return mapping


class StreetCache:
    """Lazy street ID cache to avoid repeated SELECTs."""

    def __init__(self, session, max_size=100000) -> None:
        self.session = session
        # Cache maps (name, city) -> List[Tuple[id, postal_code]]
        self._cache: OrderedDict[Tuple[str, str], List[Tuple[int, Optional[str]]]] = OrderedDict()
        self.max_size = max_size

    def update_from_rows(self, rows: Iterable[Tuple[int, str, str, Optional[str]]]) -> None:
        for street_id, name, city, postal_code in rows:
            key = (name, city)
            if key in self._cache:
                self._cache.move_to_end(key)
            
            entries = self._cache.get(key, [])
            # Avoid duplicates
            if not any(x[0] == street_id for x in entries):
                entries.append((street_id, postal_code))
                self._cache[key] = entries
            
            if len(self._cache) > self.max_size:
                self._cache.popitem(last=False)

    def get(self, name: str, city: str, postal_code: Optional[str] = None) -> Optional[int]:
        key = (name, city)
        
        # Helper to find match in a list of (id, pcode) tuples
        def find_match(entries):
            # 1. Try exact postal code match
            for sid, pcode in entries:
                if pcode == postal_code:
                    return sid
            
            # 2. Try to find an entry with None or empty postal code (to be updated later)
            for sid, pcode in entries:
                if not pcode:
                    return sid
            
            # 3. If we have a postal code, but only found streets with DIFFERENT postal codes,
            # we strictly shouldn't match. But if we didn't provide a postal code (postal_code is None),
            # we can return any.
            if not postal_code and entries:
                return entries[0][0]
                
            return None

        # Check memory cache first
        if key in self._cache:
            self._cache.move_to_end(key)
            match = find_match(self._cache[key])
            if match is not None:
                return match

        # Not in cache, query DB
        # We fetch ALL streets with this name/city to populate cache fully
        stmt = select(Street.id, Street.postal_code).where(Street.name == name, Street.city == city)
        rows = self.session.execute(stmt).all()
        
        if not rows:
            return None
            
        # Populate cache
        entries = []
        for row in rows:
            entries.append((row.id, row.postal_code))
        self._cache[key] = entries
        
        # Enforce size limit after adding new entry
        if len(self._cache) > self.max_size:
            self._cache.popitem(last=False)
        
        return find_match(entries)


# Module logger
logger = logging.getLogger(__name__)


class StreetStreamingHandler(SimpleHandler):
    """Stream street geometries directly into the database."""

    _ALLOWED_HIGHWAYS = {
        'motorway', 'motorway_link', 'trunk', 'trunk_link',
        'primary', 'primary_link', 'secondary', 'secondary_link',
        'tertiary', 'tertiary_link', 'residential', 'living_street',
        'road', 'unclassified', 'footway', 'pedestrian', 'track', 'service',
    }

    def __init__(self, areas: AreaLookup, session, cache: StreetCache, batch_size: int = 5000) -> None:
        super().__init__()
        self.areas = areas
        self.session = session
        self.cache = cache
        self.batch_size = batch_size  # Keep for compatibility but won't be used
        self._factory = WKBFactory()
        self.processed = 0
        self.persisted = 0
        self.segments_persisted = 0
        self.skipped_missing_city = 0
        self._last_report = 0

    def way(self, way):  # type: ignore[override]
        tags = way.tags
        if not self._is_valid_street(tags):
            # logger.debug("Skipping way %s: not a valid street (filtered by tags)", getattr(way, 'id', None))
            return

        name = tags.get('name')
        if not name:
            # logger.debug("Skipping way %s: missing name tag", getattr(way, 'id', None))
            return

        try:
            geom_wkb = self._factory.create_linestring(way)
            line = wkb.loads(geom_wkb)
        except Exception:
            logger.debug("Failed to create linestring for way %s", getattr(way, 'id', None), exc_info=True)
            return

        if line.is_empty or not line.is_valid or line.geom_type != 'LineString':
            # logger.debug("Skipping way %s: invalid or empty geometry; type=%s", getattr(way, 'id', None), getattr(line, 'geom_type', None))
            return

        # Extract segments first
        segments = self._extract_segments(line)
        if not segments:
            return

        # Group segments by (city, postal_code)
        # We iterate over segments and find the PLZ for each segment's midpoint
        grouped_segments = {} # Key: (city, postal_code, regional_key, borough, suburb) -> List[segment]

        for seg in segments:
            mid_lat = (seg['start_lat'] + seg['end_lat']) / 2
            mid_lon = (seg['start_lon'] + seg['end_lon']) / 2
            point = Point(mid_lon, mid_lat)
            
            match = self.areas.lookup(point)
            postal, level8, level7, level6, borough, suburb, regional_key = match
            
            # Fallback to tags if spatial lookup fails for postal code
            if not postal:
                postal = tags.get('postal_code') or tags.get('addr:postcode')
            
            # Ensure postal is not None for grouping and DB uniqueness
            if postal is None:
                postal = ""
            
            # Determine city
            # Priority:
            # 1. Level 6 (independent cities: Hamburg, Berlin, Bremen, etc.) - these are city-states
            # 2. Level 8 (municipalities) - regular towns and cities
            # 3. Tags (addr:city, etc.)
            # 4. Level 7 (counties/Landkreise) - only as fallback
            # 5. Borough/suburb - ONLY if no higher-level city found AND no tags
            #    (borough/suburb should supplement, not replace the city)
            
            city = None
            
            # First check Level 6 (independent city-states)
            if level6:
                city = level6
            # Then Level 8 (municipalities)
            elif level8:
                city = level8
            # Try tags
            elif not city:
                city = self._infer_city_from_tags(tags)
            # Fallback to Level 7 (county)
            elif not city and level7:
                city = level7
            # Last resort: use borough/suburb only if absolutely nothing else found
            elif not city:
                city = borough or suburb
            
            if city:
                city = city.strip()
            
            if not city:
                # If we can't determine city for this segment, we skip it
                continue
                
            key = (city, postal, regional_key, borough, suburb)
            if key not in grouped_segments:
                grouped_segments[key] = []
            grouped_segments[key].append(seg)

        if not grouped_segments:
            self.skipped_missing_city += 1
            return

        # Process each group
        for (city, postal, regional_key, borough, suburb), batch in grouped_segments.items():
            # Calculate centroid for this group of segments
            avg_lat = sum((s['start_lat'] + s['end_lat']) / 2 for s in batch) / len(batch)
            avg_lon = sum((s['start_lon'] + s['end_lon']) / 2 for s in batch) / len(batch)
            
            payload = {
                'name': name,
                'city': city,
                'postal_code': postal,
                'regional_key': regional_key,
                'borough': borough,
                'suburb': suburb,
                'latitude': avg_lat,
                'longitude': avg_lon,
            }

            # Upsert Street
            stmt = sqlite_insert(Street).values([payload])
            stmt = stmt.on_conflict_do_update(
                index_elements=['name', 'postal_code', 'city'],
                set_={
                    'regional_key': stmt.excluded.regional_key,
                    'borough': stmt.excluded.borough,
                    'suburb': stmt.excluded.suburb,
                    'latitude': stmt.excluded.latitude,
                    'longitude': stmt.excluded.longitude,
                },
            ).returning(Street.id, Street.name, Street.city, Street.postal_code)

            try:
                result = self.session.execute(stmt)
                row = result.fetchone()
                
                if row:
                    self.cache.update_from_rows([(row.id, row.name, row.city, row.postal_code)])
                    self.persisted += 1
                    
                    # Insert segments
                    segment_values = []
                    for seg in batch:
                        seg['street_id'] = row.id
                        segment_values.append(seg)
                    
                    # Batch insert segments
                    SEGMENT_BATCH_SIZE = 1000
                    for i in range(0, len(segment_values), SEGMENT_BATCH_SIZE):
                        sub_batch = segment_values[i:i + SEGMENT_BATCH_SIZE]
                        seg_stmt = sqlite_insert(StreetSegment).values(sub_batch)
                        seg_stmt = seg_stmt.on_conflict_do_nothing()
                        try:
                            seg_result = self.session.execute(seg_stmt)
                            if seg_result.rowcount:
                                self.segments_persisted += seg_result.rowcount
                        except Exception:
                            logger.exception("Failed to insert segment batch")
            except Exception:
                logger.exception("Upsert for street '%s' failed", name)

        self.processed += 1
        if self.processed - self._last_report >= 10000:
            self.session.commit()
            logger.info(
                "  Streets: %d processed, %d persisted (entries), %d segments, %d skipped",
                self.processed,
                self.persisted,
                self.segments_persisted,
                self.skipped_missing_city,
            )
            self._last_report = self.processed

    def _extract_segments(self, line) -> List[Dict[str, object]]:
        """Extract line segments from a LineString geometry."""
        segments = []
        coords = list(line.coords)
        
        for i in range(len(coords) - 1):
            start_lon, start_lat = coords[i]
            end_lon, end_lat = coords[i + 1]
            
            # Calculate bounding box for this segment
            min_lat = min(start_lat, end_lat)
            max_lat = max(start_lat, end_lat)
            min_lon = min(start_lon, end_lon)
            max_lon = max(start_lon, end_lon)
            
            segments.append({
                'start_lat': float(start_lat),
                'start_lon': float(start_lon),
                'end_lat': float(end_lat),
                'end_lon': float(end_lon),
                'min_lat': float(min_lat),
                'max_lat': float(max_lat),
                'min_lon': float(min_lon),
                'max_lon': float(max_lon),
            })
        
        return segments

    def flush(self) -> None:
        """No-op since we insert immediately now."""
        pass

    def _is_valid_street(self, tags) -> bool:
        highway = tags.get('highway')
        leisure = tags.get('leisure')
        place = tags.get('place')

        if highway:
            if highway not in self._ALLOWED_HIGHWAYS:
                return False
            if highway == 'track' and tags.get('tracktype') != 'grade1':
                return False
            if highway == 'service' and tags.get('service') != 'alley':
                return False
        elif leisure == 'park' or place == 'square':
            pass
        else:
            return False

        access = tags.get('access')
        if access in {'private', 'forestry', 'military'}:
            return False

        return True

    @staticmethod
    def _infer_city_from_tags(tags) -> Optional[str]:
        # Most common tags first for performance
        candidate_keys = [
            'addr:city',
            'addr:town',
            'addr:village',
            'addr:municipality',
            'addr:hamlet',
            'addr:suburb',  # Added
            'is_in:city',
            'is_in:town',
            'is_in:village',
            'is_in:municipality',
            'is_in:hamlet',  # Added
        ]

        for key in candidate_keys:
            value = tags.get(key)
            if value:
                return str(value)

        # Try is_in field which might contain multiple locations
        is_in = tags.get('is_in')
        if is_in:
            parts = [part.strip() for part in str(is_in).split(';') if part.strip()]
            if parts:
                return parts[0]

        return None


class AddressStreamingHandler(SimpleHandler):
    """Stream address nodes, ways, and relations directly into the database."""

    def __init__(self, session, cache: StreetCache, areas: AreaLookup, batch_size: int = 2000) -> None:
        super().__init__()
        self.session = session
        self.cache = cache
        self.areas = areas
        self.batch_size = batch_size
        self._factory = WKBFactory()
        self.processed = 0
        self.inserted = 0
        self.processed_nodes = 0
        self.processed_ways = 0
        self.processed_relations = 0
        self.skipped_no_street = 0
        self.skipped_no_city = 0
        self._last_report = 0
        self.updated_streets = set()
        self.address_buffer = []

    def _extract_address(self, tags, lat: float, lon: float) -> None:
        """Common address extraction logic for nodes, ways, and relations."""
        # Fast early exit before dict lookups
        if 'addr:housenumber' not in tags or 'addr:street' not in tags:
            self.skipped_no_street += 1
            logger.debug("Skipping address: missing addr:housenumber or addr:street tags: %s", {t.k: t.v for t in tags})
            return
        
        house_number = tags['addr:housenumber']
        street_name = tags['addr:street']

        # Try multiple city tag variants (most common first for performance)
        city = (tags.get('addr:city') or 
                tags.get('addr:town') or 
                tags.get('addr:village') or
                tags.get('addr:municipality') or
                tags.get('addr:hamlet'))
        
        postal_code = tags.get('addr:postcode')
        
        # Only do expensive spatial lookup if no city tag exists or postal code is missing
        if not city or not postal_code:
            point = Point(lon, lat)
            lookup_postal, level8, level7, level6, borough, suburb, _ = self.areas.lookup(point)
            # Use city priority: Level 6 (city-states) > Level 8 (municipalities) > Level 7 (counties)
            # Borough/suburb should only be used as last resort
            if not city:
                if level6:
                    city = level6
                elif level8:
                    city = level8
                elif level7:
                    city = level7
                else:
                    city = borough or suburb
            
            if not postal_code:
                postal_code = lookup_postal
        
        if not city:
            self.skipped_no_city += 1
            logger.debug("Skipping address for street '%s' house '%s': could not determine city", street_name, house_number)
            return

        street_id = self.cache.get(street_name, city, postal_code)
        if street_id is None:
            # If we failed to find a street with the specific postal code, 
            # check if we can find ANY street with that name/city.
            # If we find one, it means the street exists but with a different (or missing) postal code.
            # In that case, we should create a NEW street entry for this specific postal code.
            any_street_id = self.cache.get(street_name, city, None)
            
            if any_street_id and postal_code:
                logger.debug("Street '%s' in '%s' exists but not for PLZ %s. Creating new street entry.", street_name, city, postal_code)
                street_id = self._create_street_for_postal_code(any_street_id, postal_code, lat, lon)

            if street_id is None:
                if any_street_id:
                    logger.debug("Address %s %s in %s (PLZ %s) skipped: Street exists but could not create/find match", street_name, house_number, city, postal_code)
                else:
                    logger.debug("Address references unknown street name='%s' city='%s' — skipping", street_name, city)
                return

        # Backfill missing postal code on the street if we have one
        if postal_code and street_id not in self.updated_streets:
            try:
                stmt = update(Street).where(
                    Street.id == street_id,
                    or_(Street.postal_code.is_(None), Street.postal_code == "")
                ).values(postal_code=postal_code)
                result = self.session.execute(stmt)
                if result.rowcount > 0:
                    logger.debug("Backfilled postal code %s for street '%s' (id=%s)", postal_code, street_name, street_id)
                self.updated_streets.add(street_id)
            except Exception:
                # If update fails (likely unique constraint), it means the street with this PLZ already exists.
                # We should try to find it and switch street_id to it.
                # logger.warning("Failed to update postal code for street %s. Trying to find existing match.", street_id)
                
                try:
                    lookup_stmt = select(Street.id).where(
                        Street.name == street_name,
                        Street.city == city,
                        Street.postal_code == postal_code
                    )
                    existing_id = self.session.execute(lookup_stmt).scalar()
                    if existing_id:
                        street_id = existing_id
                        # Update cache to avoid future errors for this street
                        self.cache.update_from_rows([(existing_id, street_name, city, postal_code)])
                        logger.debug("Switched to existing street %s for address", street_id)
                except Exception:
                    pass

        payload = {
            'street_id': street_id,
            'house_number': house_number,
            'latitude': float(lat),
            'longitude': float(lon),
        }
        
        self.address_buffer.append(payload)
        if len(self.address_buffer) >= self.batch_size:
            self._flush_buffer()
        
        self.processed += 1

        # Progress report every 10000 addresses
        if self.processed - self._last_report >= 10000:
            self.session.commit()
            print(
                f"  Addresses: {self.processed} total ({self.processed_nodes} nodes, "
                f"{self.processed_ways} ways, {self.processed_relations} relations), "
                f"{self.inserted} inserted"
            )
            self._last_report = self.processed

    def _flush_buffer(self) -> None:
        if not self.address_buffer:
            return
            
        stmt = sqlite_insert(Address).values(self.address_buffer)
        stmt = stmt.on_conflict_do_nothing(index_elements=['street_id', 'house_number'])
        try:
            result = self.session.execute(stmt)
            if result.rowcount and result.rowcount > 0:
                self.inserted += result.rowcount
        except Exception:
            logger.exception("Failed to insert address batch")
            
        self.address_buffer = []

    def node(self, node):  # type: ignore[override]
        # Ultra-fast pre-filter: skip 99% of nodes immediately
        if 'addr:housenumber' not in node.tags:
            return
            
        if not node.location.valid():
            return

        self.processed_nodes += 1
        self._extract_address(node.tags, node.location.lat, node.location.lon)

    def way(self, way):  # type: ignore[override]
        """Process ways (buildings with addresses) by calculating centroid."""
        # Ultra-fast pre-filter
        if 'addr:housenumber' not in way.tags:
            return
        
        tags = way.tags
        if not tags.get('addr:street'):
            return

        try:
            # Calculate centroid from way nodes
            lats = []
            lons = []
            for node in way.nodes:
                if node.location.valid():
                    lats.append(node.lat)
                    lons.append(node.lon)
            
            if not lats or not lons:
                return
            
            # Simple centroid: average of all points
            centroid_lat = sum(lats) / len(lats)
            centroid_lon = sum(lons) / len(lons)
            
            self.processed_ways += 1
            self._extract_address(tags, centroid_lat, centroid_lon)
        except Exception:
            return

    def relation(self, relation):  # type: ignore[override]
        """Process relations with address information."""
        tags = relation.tags
        
        # Quick check if this relation has address info
        if not (tags.get('addr:housenumber') and tags.get('addr:street')):
            return

        try:
            # Try to get a representative point from the relation
            # This is approximate - we use the first valid member location
            for member in relation.members:
                if member.type == 'n' and hasattr(member, 'location'):
                    if member.location.valid():
                        self.processed_relations += 1
                        self._extract_address(tags, member.lat, member.lon)
                        return
        except Exception:
            return

    def flush(self) -> None:
        """Flush remaining items in buffer."""
        self._flush_buffer()

    def finish(self) -> None:
        """Print summary."""
        self.flush()
        logger.info(
            "\n=== Address Import Summary ===\n  Nodes processed: %d\n  Ways processed: %d\n  Relations processed: %d\n  Total addresses: %d\n  Inserted: %d\n  Skipped (no street): %d\n  Skipped (no city): %d",
            self.processed_nodes,
            self.processed_ways,
            self.processed_relations,
            self.processed,
            self.inserted,
            self.skipped_no_street,
            self.skipped_no_city,
        )

    def _create_street_for_postal_code(self, ref_street_id: int, postal_code: str, lat: float, lon: float) -> Optional[int]:
        """Create a new street entry for a specific postal code, copying details from an existing street."""
        try:
            # Fetch reference street to copy auxiliary fields
            stmt = select(Street).where(Street.id == ref_street_id)
            ref_street = self.session.execute(stmt).scalar_one_or_none()
            if not ref_street:
                return None
                
            new_street = {
                'name': ref_street.name,
                'city': ref_street.city,
                'postal_code': postal_code,
                'regional_key': ref_street.regional_key,
                'borough': ref_street.borough,
                'suburb': ref_street.suburb,
                'latitude': lat, # Use address location as anchor for this PLZ segment
                'longitude': lon
            }
            
            # Insert new street
            stmt = sqlite_insert(Street).values([new_street])
            # If it already exists (race condition), do nothing
            stmt = stmt.on_conflict_do_nothing(index_elements=['name', 'postal_code', 'city'])
            
            result = self.session.execute(stmt)
            
            # If we inserted a new row, we can't easily get the ID with returning() if on_conflict_do_nothing triggered
            # So we might need to query it back if rowcount is 0
            
            if result.rowcount > 0:
                # We need to fetch the ID. SQLite returning works with insert.
                # But SQLAlchemy's returning() with on_conflict_do_nothing might be tricky in some versions.
                # Let's try to query it back to be safe and simple.
                pass
            
            # Query back the ID (safest approach for all drivers/versions)
            # We need the ID whether we just inserted it or it already existed
            lookup_stmt = select(Street.id).where(
                Street.name == ref_street.name,
                Street.city == ref_street.city,
                Street.postal_code == postal_code
            )
            new_id = self.session.execute(lookup_stmt).scalar()
            
            if new_id:
                self.cache.update_from_rows([(new_id, ref_street.name, ref_street.city, postal_code)])
                logger.debug("Created/Found split street '%s' for PLZ %s (id=%s)", ref_street.name, postal_code, new_id)
                return new_id
                
        except Exception:
            logger.exception("Failed to create split street for PLZ %s", postal_code)
            
        return None


def main() -> None:
    pbf_file = 'germany-latest.osm.pbf'
    if not os.path.exists(pbf_file):
        logger.error("PBF file %s not found. Please download germany-latest.osm.pbf from Geofabrik.", pbf_file)
        return

    init_db()

    logger.info('Scanning boundary relations...')
    relation_collector = BoundaryRelationCollector()
    relation_collector.apply_file(pbf_file)
    logger.info(
        "Collected %d relevant relations (requires %d ways)",
        len(relation_collector.relations),
        len(relation_collector.required_way_ids),
    )

    logger.info('Collecting boundary way geometries...')
    way_collector = FilteredWayCollector(relation_collector.required_way_ids)
    way_collector.apply_file(pbf_file, locations=True, idx='flex_mem')
    logger.info("Loaded %d boundary way geometries", len(way_collector.ways))

    (
        postal_areas,
        level8_areas,
        level7_areas,
        level6_areas,
        municipality_refs,
        borough_areas,
        suburb_areas,
    ) = _build_geometries(relation_collector.relations, way_collector.ways)

    logger.info(
        "Areas -> postal: %d, L8: %d, L7: %d, L6: %d, boroughs: %d, suburbs: %d",
        len(postal_areas),
        len(level8_areas),
        len(level7_areas),
        len(level6_areas),
        len(borough_areas),
        len(suburb_areas),
    )

    area_lookup = AreaLookup(
        postal_areas,
        level8_areas,
        level7_areas,
        level6_areas,
        municipality_refs,
        borough_areas,
        suburb_areas,
    )

    del way_collector
    del relation_collector
    gc.collect()

    with database.SessionLocal() as session:
        # Optimize SQLite for bulk insert
        session.execute(text("PRAGMA synchronous = OFF"))
        session.execute(text("PRAGMA journal_mode = MEMORY"))
        session.execute(text("PRAGMA cache_size = 100000"))

        street_cache = StreetCache(session)

        logger.info('Processing street geometries...')
        street_handler = StreetStreamingHandler(area_lookup, session, street_cache, batch_size=5000)
        street_handler.apply_file(pbf_file, locations=True)
        street_handler.flush()
        session.commit()
        logger.info(
            "Processed %d street ways (persisted %d, skipped missing city %d)",
            street_handler.processed,
            street_handler.persisted,
            street_handler.skipped_missing_city,
        )

        logger.info('Processing address nodes...')
        address_handler = AddressStreamingHandler(session, street_cache, area_lookup, batch_size=5000)
        address_handler.apply_file(pbf_file, locations=True)
        address_handler.finish()
        session.commit()

        # Optimize database statistics for query planner
        logger.info("Running ANALYZE to optimize query planner statistics...")
        session.execute(text("ANALYZE"))
        session.commit()

    logger.info('Import completed.')
    
    # Rebuild fuzzy search index after import



if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Import OSM PBF into sqlite DB')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable INFO logging')
    parser.add_argument('-d', '--debug', action='store_true', help='Enable DEBUG logging')
    parser.add_argument('--db-file', help='Path to output database file (default: ./autocomplete.db)')
    parser.add_argument('--reset', action='store_true', help='Drop and recreate tables before import')
    args = parser.parse_args()

    # Configure logging
    if args.debug:
        level = logging.DEBUG
    elif args.verbose:
        level = logging.INFO
    else:
        level = logging.INFO

    logging.basicConfig(level=level, format='%(asctime)s %(levelname)s: %(message)s')

    if args.db_file:
        logger.info("Using database file: %s", args.db_file)
        configure_db(args.db_file)

    if args.reset:
        logger.info("Resetting database...")
        import models
        from database import engine
        models.Base.metadata.drop_all(bind=engine)
        logger.info("Database tables dropped.")

    main()