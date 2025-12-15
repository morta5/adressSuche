"""
Test for Issue #10: Streets with short distance not found with incomplete input

This test verifies that incomplete street queries with suffix abbreviations
are correctly expanded and matched.

Examples:
- "kampst" should match "Kampstraße, Neumünster"
- "klosterstr" should match "Klosterstraße, Neumünster"

The fix involves:
1. Adding "st" as a recognized suffix abbreviation (with min length check)
2. Using expanded query variants in Stage A exact prefix search
"""

try:
    import pytest
    HAS_PYTEST = True
except ImportError:
    HAS_PYTEST = False
    
from query_processor import QueryProcessor
from utils import normalize_string


def test_query_expansion_kampst():
    """Test that 'kampst' expands to 'kampstraße'"""
    expanded = QueryProcessor.expand_query('kampst')
    expanded_norm = [normalize_string(e) for e in expanded]
    
    assert 'kampst' in expanded_norm, "Original query should be in expanded list"
    assert 'kampstrasse' in expanded_norm, "Should expand 'st' to 'strasse'"
    

def test_query_expansion_klosterstr():
    """Test that 'klosterstr' expands to 'klosterstraße'"""
    expanded = QueryProcessor.expand_query('klosterstr')
    expanded_norm = [normalize_string(e) for e in expanded]
    
    assert 'klosterstr' in expanded_norm, "Original query should be in expanded list"
    assert 'klosterstrasse' in expanded_norm, "Should expand 'str' to 'strasse'"


def test_query_expansion_hauptst():
    """Test that 'hauptst' expands to 'hauptstraße'"""
    expanded = QueryProcessor.expand_query('hauptst')
    expanded_norm = [normalize_string(e) for e in expanded]
    
    assert 'hauptst' in expanded_norm, "Original query should be in expanded list"
    assert 'hauptstrasse' in expanded_norm, "Should expand 'st' to 'strasse'"


def test_query_expansion_short_st():
    """Test that very short queries ending in 'st' are not expanded"""
    # "ast" is too short (< 5 chars total), should not expand
    expanded = QueryProcessor.expand_query('ast')
    expanded_norm = [normalize_string(e) for e in expanded]
    
    assert expanded_norm == ['ast'], "Short queries should not be expanded"


def test_suffix_expansion_logic():
    """Test the suffix expansion logic for various cases"""
    test_cases = [
        ('kampst', ['kampst', 'kampstrasse']),  # st suffix
        ('klosterstr', ['klosterstr', 'klosterstrasse']),  # str suffix
        ('haupts', ['haupts', 'hauptstrasse']),  # s suffix
        ('parkw', ['parkw', 'parkweg']),  # w suffix
        ('marktpl', ['marktpl', 'marktplatz']),  # pl suffix
        ('ast', ['ast']),  # too short, no expansion
    ]
    
    for query, expected_norm in test_cases:
        expanded = QueryProcessor.expand_query(query)
        expanded_norm = list(dict.fromkeys([normalize_string(e) for e in expanded]))
        
        # Check that all expected variants are present
        for expected in expected_norm:
            assert expected in expanded_norm, \
                f"Query '{query}' should expand to include '{expected}', got {expanded_norm}"

if HAS_PYTEST:
    @pytest.mark.asyncio
    async def test_autocomplete_kampst_integration():
        """
        Integration test for 'kampst' query (requires database)
        
        This test verifies that the autocomplete endpoint correctly finds
        "Kampstraße, Neumünster" when searching for "kampst" with coordinates
        near Neumünster.
        """
        from httpx import ASGITransport, AsyncClient
        from main import app
        
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/autocomplete", params={
                "query": "kampst",
                "limit": 10,
                "latitude": 54.32730000000001,
                "longitude": 10.1234
            })
            
            assert response.status_code == 200, f"Expected 200, got {response.status_code}"
            
            results = response.json()
            
            # Check if Kampstraße in Neumünster is in the results
            kampstrasse_found = any(
                'kampstra' in r['name'].lower() and 'neumünster' in r['city'].lower() 
                for r in results
            )
            
            assert kampstrasse_found, \
                f"Kampstraße, Neumünster should be found in results. Got: {[(r['name'], r['city']) for r in results[:5]]}"


    @pytest.mark.asyncio
    async def test_autocomplete_klosterstr_integration():
        """
        Integration test for 'klosterstr' query (requires database)
        
        This test verifies that the autocomplete endpoint correctly finds
        "Klosterstraße, Neumünster" when searching for "klosterstr" with
        coordinates near Neumünster.
        """
        from httpx import ASGITransport, AsyncClient
        from main import app
        
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/autocomplete", params={
                "query": "klosterstr",
                "limit": 10,
                "latitude": 54.32730000000001,
                "longitude": 10.1234
            })
            
            assert response.status_code == 200, f"Expected 200, got {response.status_code}"
            
            results = response.json()
            
            # Check if Klosterstraße in Neumünster is in the results
            klosterstrasse_found = any(
                'klosterstra' in r['name'].lower() and 'neumünster' in r['city'].lower() 
                for r in results
            )
            
            assert klosterstrasse_found, \
                f"Klosterstraße, Neumünster should be found in results. Got: {[(r['name'], r['city']) for r in results[:5]]}"
else:
    # Stub functions when pytest is not available
    async def test_autocomplete_kampst_integration():
        pass
    
    async def test_autocomplete_klosterstr_integration():
        pass


if __name__ == "__main__":
    # Run unit tests only (without database)
    print("Testing query expansion logic...")
    test_query_expansion_kampst()
    print("✓ kampst expansion works")
    
    test_query_expansion_klosterstr()
    print("✓ klosterstr expansion works")
    
    test_query_expansion_hauptst()
    print("✓ hauptst expansion works")
    
    test_query_expansion_short_st()
    print("✓ short query handling works")
    
    test_suffix_expansion_logic()
    print("✓ suffix expansion logic works")
    
    print("\n✅ All unit tests passed!")
    print("\nNote: Integration tests require a populated database.")
    print("Run with: pytest test_issue_10.py")
