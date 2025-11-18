"""Import streets and addresses from an OSM PBF file into the database.

This version focuses on streaming inserts and minimal in-memory footprint so that
large extracts (e.g. the full Germany dataset) can be processed without running
out of RAM. Streets and addresses are deduplicated via database constraints and
`ON CONFLICT` upserts.
"""

from __future__ import annotations

import gc
import os
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from osmium import SimpleHandler
from osmium.geom import WKBFactory
from shapely import wkb
from shapely.ops import polygonize
from shapely.geometry import MultiPolygon, Point
from shapely.strtree import STRtree
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from database import SessionLocal, init_db
from models import Address, Street


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
            if member.type == 'w':
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
    Dict[str, str],
    Dict[str, MultiPolygon],
    Dict[str, MultiPolygon],
]:
    """Assemble shapely multipolygons for all stored relation records."""

    postal_areas: Dict[str, MultiPolygon] = {}
    municipality_areas: Dict[str, MultiPolygon] = {}
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
            continue

        multipolygon = MultiPolygon(polygons)
        if multipolygon.is_empty or not multipolygon.is_valid:
            multipolygon = multipolygon.buffer(0)
        if multipolygon.is_empty:
            continue

        if record['boundary'] == 'postal_code':
            postal_code = record['postal_code']  # type: ignore[assignment]
            postal_areas[str(postal_code)] = multipolygon
        else:
            admin_level = str(record['admin_level'])
            name = str(record['name'])
            if admin_level == '8':
                municipality_areas[name] = multipolygon
                ref = record.get('ref')
                if ref:
                    municipality_refs[name] = str(ref)
            elif admin_level == '9':
                borough_areas[name] = multipolygon
            elif admin_level == '10':
                suburb_areas[name] = multipolygon

    return postal_areas, municipality_areas, municipality_refs, borough_areas, suburb_areas


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

        # candidates are indices into self.geometries
        for idx in candidates:
            if isinstance(idx, int) and 0 <= idx < len(self.geometries):
                # Double-check containment for query() without predicate
                if self.geometries[idx].contains(point):
                    return self.keys[idx]
        
        return None

    def nearest(self, point: Point) -> Optional[str]:
        if not self.tree:
            return None

        try:
            idx = self.tree.nearest(point)
        except (TypeError, AttributeError):
            return None

        if isinstance(idx, int) and 0 <= idx < len(self.keys):
            return self.keys[idx]
        
        return None


class AreaLookup:
    """Container for all administrative lookup trees."""

    def __init__(
        self,
        postal_areas: Dict[str, MultiPolygon],
        municipality_areas: Dict[str, MultiPolygon],
        municipality_refs: Dict[str, str],
        borough_areas: Dict[str, MultiPolygon],
        suburb_areas: Dict[str, MultiPolygon],
    ) -> None:
        self.postal_index = AreaIndex(postal_areas)
        self.municipality_index = AreaIndex(municipality_areas)
        self.borough_index = AreaIndex(borough_areas)
        self.suburb_index = AreaIndex(suburb_areas)
        self.municipality_refs = municipality_refs
        self._borough_to_municipality = self._map_children_to_municipality(borough_areas)
        self._suburb_to_municipality = self._map_children_to_municipality(suburb_areas)

    def lookup(self, point: Point) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
        postal = self.postal_index.find(point)
        municipality = self.municipality_index.find(point)
        borough = self.borough_index.find(point)
        suburb = self.suburb_index.find(point)

        # Try to find municipality from children if direct lookup fails
        if not municipality and borough:
            municipality = self._borough_to_municipality.get(borough)
        if not municipality and suburb:
            municipality = self._suburb_to_municipality.get(suburb)
        
        # Last resort: use nearest municipality (with reasonable distance limit)
        # This helps for addresses near borders or slightly outside polygons
        if not municipality:
            municipality = self.municipality_index.nearest(point)

        regional_key = self.municipality_refs.get(municipality) if municipality else None
        return postal, municipality, borough, suburb, regional_key

    def _map_children_to_municipality(self, areas: Dict[str, MultiPolygon]) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for name, geom in areas.items():
            try:
                representative = geom.representative_point()
            except Exception:
                continue
            parent = self.municipality_index.find(representative)
            if not parent:
                parent = self.municipality_index.nearest(representative)
            if parent:
                mapping[name] = parent
        return mapping


class StreetCache:
    """Lazy street ID cache to avoid repeated SELECTs."""

    def __init__(self, session) -> None:
        self.session = session
        self._cache: Dict[Tuple[str, str], int] = {}

    def update_from_rows(self, rows: Iterable[Tuple[int, str, str]]) -> None:
        for street_id, name, city in rows:
            self._cache[(name, city)] = street_id

    def get(self, name: str, city: str) -> Optional[int]:
        key = (name, city)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        stmt = select(Street.id).where(Street.name == name, Street.city == city)
        street_id = self.session.execute(stmt).scalar()
        if street_id is not None:
            self._cache[key] = street_id
        return street_id


class StreetStreamingHandler(SimpleHandler):
    """Stream street geometries directly into the database."""

    _ALLOWED_HIGHWAYS = {
        'primary', 'secondary', 'tertiary', 'residential', 'living_street',
        'road', 'unclassified', 'footway', 'pedestrian', 'track', 'service',
    }

    def __init__(self, areas: AreaLookup, session, cache: StreetCache, batch_size: int = 5000) -> None:
        super().__init__()
        self.areas = areas
        self.session = session
        self.cache = cache
        self.batch_size = batch_size
        self._factory = WKBFactory()
        self._pending: List[Dict[str, object]] = []
        self.processed = 0
        self.persisted = 0
        self.skipped_missing_city = 0
        self._last_report = 0

    def way(self, way):  # type: ignore[override]
        tags = way.tags
        if not self._is_valid_street(tags):
            return

        name = tags.get('name')
        if not name:
            return

        try:
            geom_wkb = self._factory.create_linestring(way)
            line = wkb.loads(geom_wkb)
        except Exception:
            return

        if line.is_empty or not line.is_valid or line.geom_type != 'LineString':
            return

        centroid = line.centroid
        point = Point(centroid.x, centroid.y)
        postal, municipality, borough, suburb, regional_key = self.areas.lookup(point)

        # Try to get city: first from spatial lookup, then tags, with fallback to borough/suburb
        city = municipality or self._infer_city_from_tags(tags) or borough or suburb
        if city:
            city = city.strip()
        if not city:
            self.skipped_missing_city += 1
            return

        payload = {
            'name': name,
            'city': city,
            'postal_code': postal,
            'regional_key': regional_key,
            'borough': borough,
            'suburb': suburb,
            'latitude': float(centroid.y),
            'longitude': float(centroid.x),
        }

        self._pending.append(payload)
        self.processed += 1

        # Progress report every 10000 streets
        if self.processed - self._last_report >= 10000:
            print(f"  Streets: {self.processed} processed, {self.persisted} with city, {self.skipped_missing_city} skipped (no city)")
            self._last_report = self.processed

        if len(self._pending) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if not self._pending:
            return

        stmt = sqlite_insert(Street).values(self._pending)
        stmt = stmt.on_conflict_do_update(
            index_elements=['name', 'city'],
            set_={
                'postal_code': stmt.excluded.postal_code,
                'regional_key': stmt.excluded.regional_key,
                'borough': stmt.excluded.borough,
                'suburb': stmt.excluded.suburb,
                'latitude': stmt.excluded.latitude,
                'longitude': stmt.excluded.longitude,
            },
        ).returning(Street.id, Street.name, Street.city)

        result = self.session.execute(stmt)
        rows = [(row.id, row.name, row.city) for row in result]
        self.cache.update_from_rows(rows)
        self.session.flush()
        self.persisted += len(rows)
        self._pending.clear()

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

    def __init__(self, session, cache: StreetCache, areas: AreaLookup, batch_size: int = 10000) -> None:
        super().__init__()
        self.session = session
        self.cache = cache
        self.areas = areas
        self.batch_size = batch_size
        self._pending: List[Dict[str, object]] = []
        self._factory = WKBFactory()
        self.processed = 0
        self.inserted = 0
        self.processed_nodes = 0
        self.processed_ways = 0
        self.processed_relations = 0
        self.skipped_no_street = 0
        self.skipped_no_city = 0
        self._last_report = 0

    def _extract_address(self, tags, lat: float, lon: float) -> None:
        """Common address extraction logic for nodes, ways, and relations."""
        # Fast early exit before dict lookups
        if 'addr:housenumber' not in tags or 'addr:street' not in tags:
            self.skipped_no_street += 1
            return
        
        house_number = tags['addr:housenumber']
        street_name = tags['addr:street']

        # Try multiple city tag variants (most common first for performance)
        city = (tags.get('addr:city') or 
                tags.get('addr:town') or 
                tags.get('addr:village') or
                tags.get('addr:municipality') or
                tags.get('addr:hamlet'))
        
        # Only do expensive spatial lookup if no city tag exists
        if not city:
            point = Point(lon, lat)
            _, municipality, borough, suburb, _ = self.areas.lookup(point)
            # Use municipality, or fallback to borough/suburb if that's all we have
            city = municipality or borough or suburb
        
        if not city:
            self.skipped_no_city += 1
            return

        street_id = self.cache.get(street_name, city)
        if street_id is None:
            return

        self._pending.append(
            {
                'street_id': street_id,
                'house_number': house_number,
                'latitude': float(lat),
                'longitude': float(lon),
            }
        )
        self.processed += 1

        # Progress report every 10000 addresses
        if self.processed - self._last_report >= 10000:
            print(
                f"  Addresses: {self.processed} total ({self.processed_nodes} nodes, "
                f"{self.processed_ways} ways, {self.processed_relations} relations), "
                f"{self.inserted} inserted"
            )
            self._last_report = self.processed

        if len(self._pending) >= self.batch_size:
            self.flush()

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
        if not self._pending:
            return

        stmt = sqlite_insert(Address).values(self._pending)
        stmt = stmt.on_conflict_do_nothing(index_elements=['street_id', 'house_number'])
        result = self.session.execute(stmt)
        self.session.flush()
        if result.rowcount and result.rowcount > 0:
            self.inserted += result.rowcount
        self._pending.clear()

    def finish(self) -> None:
        """Flush remaining batch and print summary."""
        self.flush()
        print(
            f"\n=== Address Import Summary ==="
            f"\n  Nodes processed: {self.processed_nodes:,}"
            f"\n  Ways processed: {self.processed_ways:,}"
            f"\n  Relations processed: {self.processed_relations:,}"
            f"\n  Total addresses: {self.processed:,}"
            f"\n  Inserted: {self.inserted:,}"
            f"\n  Skipped (no street): {self.skipped_no_street:,}"
            f"\n  Skipped (no city): {self.skipped_no_city:,}"
        )


def main() -> None:
    pbf_file = 'germany-latest.osm.pbf'
    if not os.path.exists(pbf_file):
        print(f"PBF file {pbf_file} not found. Please download germany-latest.osm.pbf from Geofabrik.")
        return

    init_db()

    print('Scanning boundary relations...')
    relation_collector = BoundaryRelationCollector()
    relation_collector.apply_file(pbf_file)
    print(
        f"Collected {len(relation_collector.relations)} relevant relations "
        f"(requires {len(relation_collector.required_way_ids)} ways)"
    )

    print('Collecting boundary way geometries...')
    way_collector = FilteredWayCollector(relation_collector.required_way_ids)
    way_collector.apply_file(pbf_file, locations=True, idx='flex_mem')
    print(f"Loaded {len(way_collector.ways)} boundary way geometries")

    (
        postal_areas,
        municipality_areas,
        municipality_refs,
        borough_areas,
        suburb_areas,
    ) = _build_geometries(relation_collector.relations, way_collector.ways)

    print(
        f"Areas -> postal: {len(postal_areas)}, municipalities: {len(municipality_areas)}, "
        f"boroughs: {len(borough_areas)}, suburbs: {len(suburb_areas)}"
    )

    area_lookup = AreaLookup(
        postal_areas,
        municipality_areas,
        municipality_refs,
        borough_areas,
        suburb_areas,
    )

    del way_collector
    del relation_collector
    gc.collect()

    with SessionLocal() as session:
        street_cache = StreetCache(session)

        print('Processing street geometries...')
        street_handler = StreetStreamingHandler(area_lookup, session, street_cache, batch_size=5000)
        street_handler.apply_file(pbf_file, locations=True)
        street_handler.flush()
        session.commit()
        print(
            f"Processed {street_handler.processed} street ways (persisted {street_handler.persisted}, "
            f"skipped missing city {street_handler.skipped_missing_city})"
        )

        print('Processing address nodes...')
        address_handler = AddressStreamingHandler(session, street_cache, area_lookup, batch_size=5000)
        address_handler.apply_file(pbf_file, locations=True)
        address_handler.finish()
        session.commit()

    print('Import completed.')


if __name__ == '__main__':
    main()