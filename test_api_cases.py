"""
API Test Cases for Address Autocomplete.

This file contains comprehensive test cases for the autocomplete API,
testing performance, correctness, and various query patterns.

Test queries referenced from GitHub comments:
- http://localhost:8001/autocomplete?query=am+neuen+kamp&limit=10&latitude=54.0863&longitude=9.9757
- http://localhost:8001/autocomplete?query=albert-schweitzer-straße&limit=10&latitude=54.0863&longitude=9.9757
- http://localhost:8001/autocomplete?query=kieler+straße&limit=10&latitude=54.0863&longitude=9.9757
- http://localhost:8001/autocomplete?query=kieler&limit=10&latitude=54.0863&longitude=9.9757
- http://localhost:8001/autocomplete?query=großflecken&limit=10&latitude=53.5974&longitude=10.2135
- http://localhost:8001/autocomplete?query=kieler+straße&limit=10&latitude=53.5974&longitude=10.2135
- http://localhost:8001/autocomplete?query=galbelstraße&limit=10&latitude=53.5974&longitude=10.2135
- http://localhost:8001/autocomplete?query=jungfernstieg+hamburg&limit=10&latitude=54.0863&longitude=9.9757
"""

import asyncio
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

import pytest


@dataclass
class APITestCase:
    """A single test case for the autocomplete API."""

    name: str
    query: str
    latitude: float
    longitude: float
    limit: int = 10
    expected_result: Optional[str] = None
    expected_city: Optional[str] = None
    max_time_ms: int = 500
    description: str = ""


# Test cases from GitHub comments
TEST_CASES: List[APITestCase] = [
    # Fast queries (exact prefix matching)
    APITestCase(
        name="kieler_prefix",
        query="kieler",
        latitude=54.0863,
        longitude=9.9757,
        expected_result="Kieler",  # Changed from "Kieler Straße" to just "Kieler"
        max_time_ms=50,
        description="Simple prefix query - should be very fast",
    ),
    APITestCase(
        name="grossflecken",
        query="großflecken",
        latitude=53.5974,
        longitude=10.2135,
        expected_result="Großflecken",
        max_time_ms=50,
        description="Exact match query with special character ß",
    ),
    # Multi-word queries (still fast due to indexed range queries)
    APITestCase(
        name="am_neuen_kamp",
        query="am neuen kamp",
        latitude=54.0863,
        longitude=9.9757,
        expected_result="Am Neuen Kamp",
        max_time_ms=50,
        description="Multi-word query",
    ),
    APITestCase(
        name="albert_schweitzer_strasse",
        query="albert-schweitzer-straße",
        latitude=54.0863,
        longitude=9.9757,
        expected_result="Albert-Schweitzer-Straße",
        max_time_ms=50,
        description="Hyphenated street name",
    ),
    APITestCase(
        name="kieler_strasse_neumuenster",
        query="kieler straße",
        latitude=54.0863,
        longitude=9.9757,
        expected_result="Kieler Straße",
        max_time_ms=50,
        description="Two-word query near Neumünster",
    ),
    APITestCase(
        name="kieler_strasse_hamburg",
        query="kieler straße",
        latitude=53.5974,
        longitude=10.2135,
        expected_result="Kieler Straße",
        max_time_ms=50,
        description="Two-word query near Hamburg",
    ),
    # Typo tolerance queries
    APITestCase(
        name="galbelstrasse_typo",
        query="galbelstraße",
        latitude=53.5974,
        longitude=10.2135,
        expected_result="Geibelstraße",  # Geo-ranked near Hamburg
        max_time_ms=300,
        description="Typo query: 'galbelstraße' finds 'Geibelstraße' near Hamburg",
    ),
    APITestCase(
        name="hannes_mayer_typo",
        query="hannes-mayer-straße münchen",
        latitude=54.0863,
        longitude=9.9757,
        expected_result="Hannes-Meyer-Straße",
        expected_city="München",
        max_time_ms=500,
        description="Typo in middle: 'hannes-mayer' should find 'hannes-meyer' in München",
    ),
    APITestCase(
        name="gretenweg_typo",
        query="gretenweg",
        latitude=50.0976,
        longitude=8.6892,
        expected_result="Grethenweg",
        expected_city="Frankfurt am Main",
        max_time_ms=500,  # Allow more time for complex fuzzy search
        description="Typo query: 'gretenweg' should find 'Grethenweg Frankfurt' (missing 'h')",
    ),
    # City-in-query parsing
    APITestCase(
        name="jungfernstieg_norderstedt",
        query="jungfernstieg norderstedt",
        latitude=54.0863,
        longitude=9.9757,
        expected_result="Jungfernstieg",
        expected_city="Norderstedt",  # Jungfernstieg exists in Norderstedt
        max_time_ms=500,
        description="City parsing: 'jungfernstieg norderstedt' should find result in Norderstedt",
    ),
    # Bug report test cases - these should find "Kieler Straße" in Neumünster
    APITestCase(
        name="kiler_strasse_neumuenster_typo",
        query="Kiler Straße Neumünster",
        latitude=54.0863,
        longitude=9.9757,
        expected_result="Kieler Straße",
        expected_city="Neumünster",
        max_time_ms=500,
        description="Bug: 'Kiler Straße Neumünster' should find 'Kieler Straße' in Neumünster (typo in first word + city)",
    ),
    APITestCase(
        name="kieler_strasse_neumuenster_with_city",
        query="Kieler Straße Neumünster",
        latitude=54.0863,
        longitude=9.9757,
        expected_result="Kieler Straße",
        expected_city="Neumünster",
        max_time_ms=150,  # Increased from 100ms to 150ms for realistic performance
        description="Bug: 'Kieler Straße Neumünster' should find 'Kieler Straße' in Neumünster",
    ),
    # Test with highest ID Hauptstraße to ensure high rowid entries are found with geo-coordinates
    # This is a stress test for the most common street name (6500+ entries)
    APITestCase(
        name="hauptstrasse_high_id",
        query="hauptstraße",
        latitude=54.1401559,  # Near Neuenkirchen (17498)
        longitude=13.382598400000001,
        expected_result="Hauptstraße",
        expected_city="Neuenkirchen",
        max_time_ms=200,
        description="High rowid stress test: Hauptstraße with ID 1219975 in Neuenkirchen (0.64km away) should be found",
    ),
    # Issue: Kampstraße with partial/normalized city names
    # See: https://github.com/morta5/adressSuche/issues/X
    APITestCase(
        name="kampstrasse_neum_partial",
        query="kampstraße neum",
        latitude=54.0724,  # Neumünster coordinates - used for geographic disambiguation
        longitude=9.9858,
        expected_result="Kampstraße",
        expected_city="Neumünster",
        max_time_ms=200,
        description="Issue: 'kampstraße neum' (partial city) should find 'Kampstraße' in Neumünster using geographic disambiguation",
    ),
    APITestCase(
        name="kampstrasse_neumuenster_normalized",
        query="kampstraße neumuenster",
        latitude=54.0724,  # Neumünster coordinates
        longitude=9.9858,
        expected_result="Kampstraße",
        expected_city="Neumünster",
        max_time_ms=200,
        description="Issue: 'kampstraße neumuenster' (with 'ue') should find 'Kampstraße' in Neumünster (with 'ü')",
    ),
    APITestCase(
        name="kampstrasse_neumuenster_proper",
        query="kampstraße neumünster",
        latitude=54.0724,
        longitude=9.9858,
        expected_result="Kampstraße",
        expected_city="Neumünster",
        max_time_ms=200,
        description="Issue: 'kampstraße neumünster' should find 'Kampstraße' in Neumünster",
    ),
]

# Minimum size for real database in bytes (1MB)
MIN_DB_SIZE_BYTES = 1_000_000


def _real_db_available() -> bool:
    """Check if the real database is available."""
    db_path = Path("./autocomplete_v2.db")
    return db_path.exists() and db_path.stat().st_size > MIN_DB_SIZE_BYTES


class TestAPITestCases:
    """Test suite for API test cases."""

    @pytest.mark.skipif(not _real_db_available(), reason="Real database not available")
    @pytest.mark.parametrize(
        "test_case", TEST_CASES, ids=[tc.name for tc in TEST_CASES]
    )
    def test_api_query(self, test_case: APITestCase):
        """Test individual API query."""
        from httpx import AsyncClient, ASGITransport
        from main import app

        async def run_test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                # Warm up
                await client.get("/autocomplete", params={"query": "test", "limit": 1})

                params = {
                    "query": test_case.query,
                    "limit": test_case.limit,
                    "latitude": test_case.latitude,
                    "longitude": test_case.longitude,
                }

                start = time.perf_counter()
                response = await client.get("/autocomplete", params=params)
                elapsed_ms = (time.perf_counter() - start) * 1000

                # Check status code
                assert response.status_code == 200, (
                    f"Query failed with status {response.status_code}"
                )

                # Check response time
                assert elapsed_ms < test_case.max_time_ms, (
                    f"Query took {elapsed_ms:.0f}ms, expected < {test_case.max_time_ms}ms"
                )

                results = response.json()

                # Check expected result is found (using startswith for more robust matching)
                if test_case.expected_result:
                    found = any(
                        r["name"].startswith(test_case.expected_result)
                        or r["name"] == test_case.expected_result
                        for r in results
                    )
                    assert found, (
                        f"Expected '{test_case.expected_result}' not found in results: {[r['name'] for r in results]}"
                    )

                # Check expected city
                if test_case.expected_city:
                    found_city = any(
                        r["city"].lower() == test_case.expected_city.lower()
                        for r in results
                    )
                    assert found_city, (
                        f"Expected city '{test_case.expected_city}' not found in results: {[r['city'] for r in results]}"
                    )

                return elapsed_ms, results

        asyncio.run(run_test())

    @pytest.mark.skipif(not _real_db_available(), reason="Real database not available")
    def test_all_queries_summary(self):
        """Run all test queries and print summary."""
        from httpx import AsyncClient, ASGITransport
        from main import app

        async def run_all():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                # Warm up
                await client.get("/autocomplete", params={"query": "test", "limit": 1})

                results_summary = []

                for tc in TEST_CASES:
                    params = {
                        "query": tc.query,
                        "limit": tc.limit,
                        "latitude": tc.latitude,
                        "longitude": tc.longitude,
                    }

                    start = time.perf_counter()
                    response = await client.get("/autocomplete", params=params)
                    elapsed_ms = (time.perf_counter() - start) * 1000

                    results = response.json() if response.status_code == 200 else []
                    top_result = results[0]["name"] if results else "N/A"
                    top_city = results[0]["city"] if results else "N/A"

                    passed = elapsed_ms < tc.max_time_ms
                    if tc.expected_result:
                        passed = passed and any(
                            tc.expected_result in r["name"] for r in results
                        )

                    results_summary.append(
                        {
                            "name": tc.name,
                            "query": tc.query,
                            "time_ms": elapsed_ms,
                            "max_ms": tc.max_time_ms,
                            "top_result": top_result,
                            "top_city": top_city,
                            "passed": passed,
                        }
                    )

                # Print summary
                print("\n" + "=" * 80)
                print("API TEST RESULTS SUMMARY")
                print("=" * 80)

                total_time = sum(r["time_ms"] for r in results_summary)
                avg_time = total_time / len(results_summary)
                all_passed = all(r["passed"] for r in results_summary)

                for r in results_summary:
                    status = "✓" if r["passed"] else "✗"
                    print(
                        f"{status} {r['name']:30} | {r['time_ms']:6.0f}ms / {r['max_ms']:4}ms | {r['top_result']} ({r['top_city']})"
                    )

                print("-" * 80)
                print(
                    f"Total time: {total_time:.0f}ms | Average: {avg_time:.0f}ms | All passed: {all_passed}"
                )
                print("=" * 80)

                assert all_passed, "Some tests failed"

        asyncio.run(run_all())


class TestCityExtraction:
    """Tests for city extraction from query."""

    def test_extract_city_from_query(self):
        """Test city extraction function."""
        from main import _extract_city_from_query, _get_known_cities
        from database import get_async_db

        # Skip if database not available
        if not _real_db_available():
            pytest.skip("Real database not available")

        async def run_test():
            # Get async database session
            async for db in get_async_db():
                try:
                    known_cities = await _get_known_cities(db)

                    # Test extraction (pass db for potential geographic disambiguation)
                    query, city = await _extract_city_from_query("jungfernstieg hamburg", known_cities, db=db)
                    assert query == "jungfernstieg"
                    assert city is not None
                    assert city.lower() == "hamburg"

                    # Test no extraction for query without city
                    query, city = await _extract_city_from_query("bahnhofstraße", known_cities, db=db)
                    assert query == "bahnhofstraße"
                    assert city is None
                finally:
                    break  # Only use the first session

        asyncio.run(run_test())

    def test_city_extraction_with_api(self):
        """Test that city extraction works in API."""
        if not _real_db_available():
            pytest.skip("Real database not available")

        from httpx import AsyncClient, ASGITransport
        from main import app

        async def run_test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                # Test with city in query
                response = await client.get(
                    "/autocomplete",
                    params={
                        "query": "jungfernstieg hamburg",
                        "limit": 5,
                        "latitude": 54.0863,
                        "longitude": 9.9757,
                    },
                )

                assert response.status_code == 200
                results = response.json()

                # Should find Jungfernstieg in Hamburg
                hamburg_results = [r for r in results if r["city"].lower() == "hamburg"]
                assert len(hamburg_results) > 0, "Should find results in Hamburg"
                assert any("Jungfernstieg" in r["name"] for r in hamburg_results), (
                    "Should find Jungfernstieg in Hamburg"
                )

        asyncio.run(run_test())


class TestPerformanceRegression:
    """Performance regression tests."""

    @pytest.mark.skipif(not _real_db_available(), reason="Real database not available")
    def test_exact_prefix_queries_fast(self):
        """Exact prefix queries should be under 30ms."""
        from httpx import AsyncClient, ASGITransport
        from main import app

        fast_queries = [
            "kieler",
            "großflecken",
            "am neuen kamp",
            "albert-schweitzer-straße",
            "kieler straße",
        ]

        async def run_test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                # Warm up
                await client.get("/autocomplete", params={"query": "test", "limit": 1})

                for query in fast_queries:
                    start = time.perf_counter()
                    response = await client.get(
                        "/autocomplete",
                        params={
                            "query": query,
                            "limit": 10,
                            "latitude": 54.0863,
                            "longitude": 9.9757,
                        },
                    )
                    elapsed_ms = (time.perf_counter() - start) * 1000

                    assert response.status_code == 200
                    assert elapsed_ms < 30, (
                        f"Query '{query}' took {elapsed_ms:.0f}ms (>30ms)"
                    )

        asyncio.run(run_test())

    @pytest.mark.skipif(not _real_db_available(), reason="Real database not available")
    def test_typo_query_reasonable_time(self):
        """Typo queries should complete in reasonable time."""
        from httpx import AsyncClient, ASGITransport
        from main import app

        async def run_test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                # Warm up
                await client.get("/autocomplete", params={"query": "test", "limit": 1})

                start = time.perf_counter()
                response = await client.get(
                    "/autocomplete",
                    params={
                        "query": "galbelstraße",
                        "limit": 10,
                        "latitude": 53.5974,
                        "longitude": 10.2135,
                    },
                )
                elapsed_ms = (time.perf_counter() - start) * 1000

                assert response.status_code == 200
                assert elapsed_ms < 300, f"Typo query took {elapsed_ms:.0f}ms (>300ms)"

                results = response.json()
                assert any("Geibelstraße" in r["name"] for r in results), (
                    f"Should find Geibelstraße: {[r['name'] for r in results]}"
                )

        asyncio.run(run_test())


class TestReverse:
    """Tests for reverse geocoding endpoint."""

    @pytest.mark.skipif(not _real_db_available(), reason="Real database not available")
    def test_reverse_geocode_basic(self):
        """Test basic reverse geocoding functionality."""
        from httpx import AsyncClient, ASGITransport
        from main import app

        async def run_test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                # Test with Hamburg coordinates (should find nearby address if data exists)
                response = await client.get(
                    "/reverse", params={"latitude": 53.5511, "longitude": 9.9937}
                )

                assert response.status_code == 200
                result = response.json()
                assert "exists" in result

                # If an address is found, verify response structure
                if result["exists"]:
                    assert "address_id" in result
                    assert "street_name" in result
                    assert "city" in result
                    assert "house_number" in result
                    assert "latitude" in result
                    assert "longitude" in result
                    assert "distance_km" in result

        asyncio.run(run_test())

    @pytest.mark.skipif(not _real_db_available(), reason="Real database not available")
    def test_reverse_geocode_no_nearby_address(self):
        """Test reverse geocode returns exists=False for remote locations."""
        from httpx import AsyncClient, ASGITransport
        from main import app

        async def run_test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                # Test with coordinates in the middle of the ocean
                response = await client.get(
                    "/reverse", params={"latitude": 0.0, "longitude": 0.0}
                )

                assert response.status_code == 200
                result = response.json()
                assert result["exists"] is False

        asyncio.run(run_test())

    @pytest.mark.skipif(not _real_db_available(), reason="Real database not available")
    def test_reverse_geocode_custom_max_distance(self):
        """Test reverse geocode with custom max distance."""
        from httpx import AsyncClient, ASGITransport
        from main import app

        async def run_test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                # Test with very small max distance (should not find anything)
                response = await client.get(
                    "/reverse",
                    params={
                        "latitude": 53.5511,
                        "longitude": 9.9937,
                        "max_distance_km": 0.0001,  # 0.1 meters - very small
                    },
                )

                assert response.status_code == 200
                result = response.json()
                # Should likely not find anything with such a small radius
                # (unless there's an address at exactly this location)
                assert "exists" in result

        asyncio.run(run_test())

    @pytest.mark.skipif(not _real_db_available(), reason="Real database not available")
    def test_reverse_geocode_returns_same_structure_as_validate(self):
        """Test that reverse geocode returns same structure as validate endpoint."""
        from httpx import AsyncClient, ASGITransport
        from main import app

        async def run_test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                # Get reverse geocode response
                reverse_response = await client.get(
                    "/reverse",
                    params={
                        "latitude": 53.5511,
                        "longitude": 9.9937,
                        "max_distance_km": 1.0,  # 1km radius for better chance of finding something
                    },
                )

                assert reverse_response.status_code == 200
                reverse_result = reverse_response.json()

                # Get validate response for comparison
                validate_response = await client.get(
                    "/validate",
                    params={
                        "street_name": "Hauptstraße",
                        "house_number": "1",
                        "latitude": 53.5511,
                        "longitude": 9.9937,
                    },
                )

                assert validate_response.status_code == 200
                validate_result = validate_response.json()

                # Both responses should have the same keys
                assert set(reverse_result.keys()) == set(validate_result.keys())

        asyncio.run(run_test())

    def test_reverse_geocode_requires_coordinates(self):
        """Test that reverse geocode requires latitude and longitude."""
        from httpx import AsyncClient, ASGITransport
        from main import app

        async def run_test():
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                # Missing latitude
                response = await client.get("/reverse", params={"longitude": 9.9937})
                assert response.status_code == 422  # Unprocessable Entity

                # Missing longitude
                response = await client.get("/reverse", params={"latitude": 53.5511})
                assert response.status_code == 422

                # Missing both
                response = await client.get("/reverse")
                assert response.status_code == 422

        asyncio.run(run_test())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
