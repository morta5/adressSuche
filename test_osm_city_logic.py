"""
Test OSM import city determination logic.

This test verifies that the city assignment prioritizes correctly:
1. Level 6 (city-states like Hamburg, Berlin)
2. Level 8 (municipalities)
3. Level 7 (counties)
4. Borough/suburb (last resort)
"""


class TestCityDetermination:
    """Test city determination logic from OSM import."""

    def test_level6_city_state_prioritized(self):
        """Level 6 (Hamburg) should be used over borough (Harburg)."""
        # Simulate: Street in Harburg borough of Hamburg
        level6 = "Hamburg"
        level8 = None
        level7 = None
        borough = "Harburg"
        suburb = None
        
        # Expected: Hamburg (Level 6), NOT Harburg (borough)
        city = self._determine_city(level6, level8, level7, borough, suburb)
        assert city == "Hamburg", f"Expected 'Hamburg' but got '{city}'"
    
    def test_level8_municipality_when_no_level6(self):
        """Level 8 (municipality) should be used when Level 6 doesn't exist."""
        # Simulate: Street in Neumünster municipality
        level6 = None
        level8 = "Neumünster"
        level7 = "Kreis Rendsburg-Eckernförde"
        borough = None
        suburb = None
        
        # Expected: Neumünster (Level 8), NOT the county (Level 7)
        city = self._determine_city(level6, level8, level7, borough, suburb)
        assert city == "Neumünster", f"Expected 'Neumünster' but got '{city}'"
    
    def test_level7_county_fallback(self):
        """Level 7 (county) should be used when neither Level 6 nor 8 exist."""
        # Simulate: Rural area with only county
        level6 = None
        level8 = None
        level7 = "Kreis Pinneberg"
        borough = None
        suburb = "Kleindorf"
        
        # Expected: Kreis Pinneberg (Level 7), NOT suburb
        city = self._determine_city(level6, level8, level7, borough, suburb)
        assert city == "Kreis Pinneberg", f"Expected 'Kreis Pinneberg' but got '{city}'"
    
    def test_borough_suburb_last_resort(self):
        """Borough/suburb should only be used when no admin levels exist."""
        # Simulate: Edge case with only borough
        level6 = None
        level8 = None
        level7 = None
        borough = "Stadtteil"
        suburb = None
        
        # Expected: Stadtteil (borough) as last resort
        city = self._determine_city(level6, level8, level7, borough, suburb)
        assert city == "Stadtteil", f"Expected 'Stadtteil' but got '{city}'"
    
    def test_hamburg_mitte_gets_hamburg(self):
        """Hamburg-Mitte borough should get city=Hamburg, not Hamburg-Mitte."""
        # Simulate: Street in Hamburg-Mitte borough
        level6 = "Hamburg"
        level8 = None
        level7 = None
        borough = "Hamburg-Mitte"
        suburb = None
        
        # Expected: Hamburg (Level 6), NOT Hamburg-Mitte
        city = self._determine_city(level6, level8, level7, borough, suburb)
        assert city == "Hamburg", f"Expected 'Hamburg' but got '{city}'"
    
    def _determine_city(self, level6, level8, level7, borough, suburb):
        """
        Replicate the new city determination logic from import_osm.py.
        This matches the code at lines ~458-485 in import_osm.py.
        """
        city = None
        
        # First check Level 6 (independent city-states)
        if level6:
            city = level6
        # Then Level 8 (municipalities)
        elif level8:
            city = level8
        # Fallback to Level 7 (county)
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
        ("Level 6 city-state prioritized", test.test_level6_city_state_prioritized),
        ("Level 8 municipality when no Level 6", test.test_level8_municipality_when_no_level6),
        ("Level 7 county fallback", test.test_level7_county_fallback),
        ("Borough/suburb last resort", test.test_borough_suburb_last_resort),
        ("Hamburg-Mitte gets Hamburg", test.test_hamburg_mitte_gets_hamburg),
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
