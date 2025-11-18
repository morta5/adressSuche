"""Test: Vergleich ALT vs NEU Logik für Stadt-Lookup"""

import osmium
from shapely.geometry import Point

class CityLookupComparison(osmium.SimpleHandler):
    """Vergleicht alte und neue Stadt-Lookup Logik."""
    
    def __init__(self, areas, max_samples=50000):
        super().__init__()
        self.areas = areas
        self.max_samples = max_samples
        self.samples = 0
        
        # Old logic stats
        self.old_found_via_tags = 0
        self.old_found_via_spatial = 0
        self.old_not_found = 0
        
        # New logic stats
        self.new_found_via_tags = 0
        self.new_found_via_spatial = 0
        self.new_not_found = 0
        
        # Improvements
        self.improved_by_new_tags = 0
        self.improved_by_suburb_fallback = 0
        
    def node(self, node):
        if self.samples >= self.max_samples:
            return
            
        if 'addr:housenumber' not in node.tags or 'addr:street' not in node.tags:
            return
            
        if not node.location.valid():
            return
            
        self.samples += 1
        tags = node.tags
        
        # === OLD LOGIC ===
        old_city_from_tags = (tags.get('addr:city') or 
                              tags.get('addr:town') or 
                              tags.get('addr:village'))
        
        if old_city_from_tags:
            self.old_found_via_tags += 1
            old_city = old_city_from_tags
        else:
            point = Point(node.location.lon, node.location.lat)
            _, old_municipality, _, _, _ = self.areas.lookup(point)
            if old_municipality:
                self.old_found_via_spatial += 1
                old_city = old_municipality
            else:
                self.old_not_found += 1
                old_city = None
        
        # === NEW LOGIC ===
        new_city_from_tags = (tags.get('addr:city') or 
                              tags.get('addr:town') or 
                              tags.get('addr:village') or
                              tags.get('addr:municipality') or
                              tags.get('addr:hamlet'))
        
        if new_city_from_tags:
            self.new_found_via_tags += 1
            new_city = new_city_from_tags
            
            # Track if new tags helped
            if not old_city_from_tags:
                self.improved_by_new_tags += 1
        else:
            point = Point(node.location.lon, node.location.lat)
            _, new_municipality, borough, suburb, _ = self.areas.lookup(point)
            new_city = new_municipality or borough or suburb
            
            if new_city:
                self.new_found_via_spatial += 1
                
                # Track if suburb/borough fallback helped
                if not old_city and (borough or suburb):
                    self.improved_by_suburb_fallback += 1
            else:
                self.new_not_found += 1
        
        # Progress
        if self.samples % 5000 == 0:
            print(f"  Verglichen: {self.samples} Adressen...")
            print(f"    ALT: {self.old_not_found} ohne Stadt")
            print(f"    NEU: {self.new_not_found} ohne Stadt")
            print(f"    Verbesserung: {self.old_not_found - self.new_not_found}")

def main():
    import sys
    from v2.import_osm import BoundaryRelationCollector, FilteredWayCollector, _build_geometries, AreaLookup
    
    pbf_file = 'germany-latest.osm.pbf'
    
    print("=" * 70)
    print("STADT-LOOKUP VERGLEICH: ALT vs NEU")
    print("=" * 70)
    
    print("\nSchritt 1: Lade Boundary-Daten...")
    collector = BoundaryRelationCollector()
    collector.apply_file(pbf_file, locations=True)
    print(f"  ✓ {len(collector.relations)} relations, {len(collector.required_way_ids)} ways benötigt")
    
    print("\nSchritt 2: Lade Way-Geometrien...")
    way_collector = FilteredWayCollector(collector.required_way_ids)
    way_collector.apply_file(pbf_file, locations=True)
    print(f"  ✓ {len(way_collector.ways)} ways geladen")
    
    print("\nSchritt 3: Baue Polygone...")
    postal_areas, municipality_areas, municipality_refs, borough_areas, suburb_areas = \
        _build_geometries(collector.relations, way_collector.ways)
    print(f"  ✓ {len(municipality_areas)} municipalities")
    print(f"  ✓ {len(borough_areas)} boroughs")
    print(f"  ✓ {len(suburb_areas)} suburbs")
    
    print("\nSchritt 4: Erstelle AreaLookup...")
    areas = AreaLookup(postal_areas, municipality_areas, municipality_refs, borough_areas, suburb_areas)
    print(f"  ✓ Spatial indices erstellt")
    
    print("\nSchritt 5: Vergleiche Logik an 50.000 Adressen...")
    print()
    
    comparison = CityLookupComparison(areas, max_samples=50000)
    comparison.apply_file(pbf_file, locations=True)
    
    print("\n" + "=" * 70)
    print("ERGEBNISSE")
    print("=" * 70)
    
    print(f"\nAnalysierte Adressen: {comparison.samples:,}")
    
    print(f"\n{'ALTE LOGIK:':<40}")
    print(f"  Via Tags gefunden:        {comparison.old_found_via_tags:>7,} ({100*comparison.old_found_via_tags/comparison.samples:>5.2f}%)")
    print(f"  Via Spatial gefunden:     {comparison.old_found_via_spatial:>7,} ({100*comparison.old_found_via_spatial/comparison.samples:>5.2f}%)")
    print(f"  NICHT gefunden:           {comparison.old_not_found:>7,} ({100*comparison.old_not_found/comparison.samples:>5.2f}%)")
    
    print(f"\n{'NEUE LOGIK:':<40}")
    print(f"  Via Tags gefunden:        {comparison.new_found_via_tags:>7,} ({100*comparison.new_found_via_tags/comparison.samples:>5.2f}%)")
    print(f"  Via Spatial gefunden:     {comparison.new_found_via_spatial:>7,} ({100*comparison.new_found_via_spatial/comparison.samples:>5.2f}%)")
    print(f"  NICHT gefunden:           {comparison.new_not_found:>7,} ({100*comparison.new_not_found/comparison.samples:>5.2f}%)")
    
    improvement = comparison.old_not_found - comparison.new_not_found
    print(f"\n{'VERBESSERUNG:':<40}")
    print(f"  Weniger 'no city' Fehler: {improvement:>7,} ({100*improvement/max(1,comparison.old_not_found):>5.2f}% weniger)")
    print(f"    davon via neue Tags:    {comparison.improved_by_new_tags:>7,}")
    print(f"    davon via suburb/borough:{comparison.improved_by_suburb_fallback:>7,}")
    
    # Hochrechnung
    total_addresses_estimated = 25_000_000  # Geschätzt für Deutschland
    old_missing_estimated = int(total_addresses_estimated * comparison.old_not_found / comparison.samples)
    new_missing_estimated = int(total_addresses_estimated * comparison.new_not_found / comparison.samples)
    saved_estimated = old_missing_estimated - new_missing_estimated
    
    print(f"\n{'HOCHRECHNUNG AUF DEUTSCHLAND:':<40}")
    print(f"  Geschätzte Gesamt-Adressen:    {total_addresses_estimated:>12,}")
    print(f"  ALT: 'no city' Fehler:         {old_missing_estimated:>12,}")
    print(f"  NEU: 'no city' Fehler:         {new_missing_estimated:>12,}")
    print(f"  GERETTET:                      {saved_estimated:>12,} Adressen! 🎉")
    
    print("\n" + "=" * 70)

if __name__ == '__main__':
    main()
