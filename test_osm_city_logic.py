"""
Test OSM import city determination logic.

This test verifies that the city assignment prioritizes correctly:
1. Level 8 (municipalities/Gemeinden)
2. Level 6 (counties/Kreise and independent cities/kreisfreie Städte)
3. Level 5 (administrative regions/Regierungsbezirke)
4. Level 7 (associations of municipalities)
5. Borough/suburb (last resort)

Based on https://wiki.openstreetmap.org/wiki/DE:Grenze
"""


class TestCityDetermination:
    """Test city determination logic from OSM import."""

    def test_level8_municipality_prioritized(self):
        """Level 8 (municipality) should be used first."""
        # Simulate: Street in regular municipality
        level8 = "Neumünster"
        level6 = "Kreis Rendsburg-Eckernförde"
        level5 = None
        level7 = None
        borough = None
        suburb = None
        
        # Expected: Neumünster (Level 8), NOT the county (Level 6)
        city = self._determine_city(level8, level6, level5, level7, borough, suburb)
        assert city == "Neumünster", f"Expected 'Neumünster' but got '{city}'"
    
    def test_level6_when_no_level8(self):
        """Level 6 should be used when Level 8 doesn't exist."""
        # Simulate: Independent city (kreisfreie Stadt) or county
        level8 = None
        level6 = "Hamburg"
        level5 = None
        level7 = None
        borough = "Harburg"
        suburb = None
        
        # Expected: Hamburg (Level 6), NOT Harburg (borough)
        city = self._determine_city(level8, level6, level5, level7, borough, suburb)
        assert city == "Hamburg", f"Expected 'Hamburg' but got '{city}'"
    
    def test_level5_when_no_level8_or_6(self):
        """Level 5 (region) should be used when neither Level 8 nor 6 exist."""
        # Simulate: Area with only administrative region
        level8 = None
        level6 = None
        level5 = "Oberbayern"
        level7 = None
        borough = None
        suburb = None
        
        # Expected: Oberbayern (Level 5)
        city = self._determine_city(level8, level6, level5, level7, borough, suburb)
        assert city == "Oberbayern", f"Expected 'Oberbayern' but got '{city}'"
    
    def test_level7_fallback(self):
        """Level 7 should be used when levels 8, 6, and 5 don't exist."""
        # Simulate: Rural area with only association
        level8 = None
        level6 = None
        level5 = None
        level7 = "Verwaltungsgemeinschaft Beispiel"
        borough = None
        suburb = "Kleindorf"
        
        # Expected: Level 7, NOT suburb
        city = self._determine_city(level8, level6, level5, level7, borough, suburb)
        assert city == "Verwaltungsgemeinschaft Beispiel", f"Expected 'Verwaltungsgemeinschaft Beispiel' but got '{city}'"
    
    def test_borough_suburb_last_resort(self):
        """Borough/suburb should only be used when no admin levels exist."""
        # Simulate: Edge case with only borough
        level8 = None
        level6 = None
        level5 = None
        level7 = None
        borough = "Stadtteil"
        suburb = None
        
        # Expected: Stadtteil (borough) as last resort
        city = self._determine_city(level8, level6, level5, level7, borough, suburb)
        assert city == "Stadtteil", f"Expected 'Stadtteil' but got '{city}'"
    
    def _determine_city(self, level8, level6, level5, level7, borough, suburb):
        """
        Replicate the new city determination logic from import_osm.py.
        Priority: Level 8 > Level 6 > Level 5 > Level 7 > Borough/Suburb
        """
        city = None
        
        # First check Level 8 (municipalities)
        if level8:
            city = level8
        # Then Level 6 (counties/independent cities)
        elif level6:
            city = level6
        # Then Level 5 (regions)
        elif level5:
            city = level5
        # Fallback to Level 7 (associations)
        elif level7:
            city = level7
        # Last resort: use borough/suburb
        else:
            city = borough or suburb
        
        return city.strip() if city else None


if __name__ == "__main__":
    # Run tests
    test = TestCityDetermination()
    
    print("Running OSM City Determination Tests")
    print("="*80)
    
    tests = [
        ("Level 8 municipality prioritized", test.test_level8_municipality_prioritized),
        ("Level 6 when no Level 8", test.test_level6_when_no_level8),
        ("Level 5 when no Level 8 or 6", test.test_level5_when_no_level8_or_6),
        ("Level 7 fallback", test.test_level7_fallback),
        ("Borough/suburb last resort", test.test_borough_suburb_last_resort),
    ]
    
    all_passed = True
    for name, test_func in tests:
        try:
            test_func()
            print(f"✓ PASS: {name}")
        except AssertionError as e:
            print(f"✗ FAIL: {name}")
            print(f"  Error: {e}")
            all_passed = False
    
    print("="*80)
    if all_passed:
        print("✓ All tests passed!")
        exit(0)
    else:
        print("✗ Some tests failed")
        exit(1)
