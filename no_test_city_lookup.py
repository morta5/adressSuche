import logging
import argparse
import os
import sys
from shapely.geometry import Point

# Add current directory to path to ensure imports work
sys.path.append(os.getcwd())

try:
    from import_osm import BoundaryRelationCollector, FilteredWayCollector, _build_geometries, AreaLookup
except ImportError:
    print("Error: Could not import modules from import_osm.py. Make sure you are in the correct directory.")
    sys.exit(1)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def test_lookup(pbf_file, lat, lon):
    if not os.path.exists(pbf_file):
        logger.error(f"PBF file {pbf_file} not found. Please provide the correct path using --pbf")
        return

    logger.info(f"Loading data from {pbf_file}...")
    
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

    logger.info('Building geometries...')
    (
        postal_areas,
        level8_areas,
        level7_areas,
        level6_areas,
        level5_areas,
        level4_areas,
        municipality_refs,
        borough_areas,
        suburb_areas,
    ) = _build_geometries(relation_collector.relations, way_collector.ways)

    area_lookup = AreaLookup(
        postal_areas,
        level8_areas,
        level7_areas,
        level6_areas,
        level5_areas,
        level4_areas,
        municipality_refs,
        borough_areas,
        suburb_areas,
    )

    point = Point(lon, lat)
    logger.info(f"Looking up point: Lat {lat}, Lon {lon}")
    
    # Debug: Check all levels
    for level_name, index in [
        ('L8', area_lookup.level8_index),
        ('L6', area_lookup.level6_index),
        ('L4', area_lookup.level4_index),
        ('L9 (Borough)', area_lookup.borough_index),
    ]:
        if index.keys:
            found = index.find(point)
            if found:
                logger.info(f"{level_name} found: {found}")
    
    # Debug borough mappings
    borough = area_lookup.borough_index.find(point)
    if borough:
        logger.info(f"Borough found: {borough}")
        logger.info(f"Borough to municipality mapping: {area_lookup._borough_to_municipality.get(borough)}")
    
    result = area_lookup.lookup(point)
    postal, level8, level7, level6, level5, level4, borough, suburb, regional_key = result

    print("\n" + "="*30)
    print("       LOOKUP RESULT       ")
    print("="*30)
    print(f"Postal Code:       {postal}")
    print(f"City (Level 8):    {level8}")
    print(f"County (Level 6):  {level6}")
    print(f"State (Level 4):   {level4}")
    print(f"Region (Level 5):  {level5}")
    print(f"Association (L7):  {level7}")
    print(f"Borough:           {borough}")
    print(f"Suburb:            {suburb}")
    print(f"Regional Key:      {regional_key}")
    print("="*30 + "\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Test city lookup for a coordinate using OSM data')
    parser.add_argument('lat', type=float, help='Latitude (e.g. 53.551086)')
    parser.add_argument('lon', type=float, help='Longitude (e.g. 9.993682)')
    parser.add_argument('--pbf', default='germany-latest.osm.pbf', help='Path to OSM PBF file (default: germany-latest.osm.pbf)')
    
    args = parser.parse_args()
    # test_lookup(args.pbf, args.lat, args.lon)
    # skip this test