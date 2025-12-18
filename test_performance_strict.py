"""
Strict API Performance Validation Tests.

These tests use stricter thresholds based on user-provided scenarios.
Tests are skipped unless a real SQLite database is available locally.
"""

import asyncio
import time
from pathlib import Path
import pytest

# Check for either v2 or v3 DB files
MIN_DB_SIZE_BYTES = 1_000_000

def _real_db_available() -> bool:
    candidates = [Path("./autocomplete_v2.db"), Path("./autocomplete_v3.db")]
    for p in candidates:
        if p.exists() and p.stat().st_size > MIN_DB_SIZE_BYTES:
            return True
    return False

TEST_QUERIES = [
    # Fast queries (< 20ms)
    {
        "url": "/autocomplete",
        "params": {"query": "kieler", "limit": 10, "latitude": 54.0863, "longitude": 9.9757},
        "expected_time_ms": 20,
        "expected_result": "Kieler Straße",
    },
    {
        "url": "/autocomplete",
        "params": {"query": "großflecken", "limit": 10, "latitude": 53.5974, "longitude": 10.2135},
        "expected_time_ms": 20,
        "expected_result": "Großflecken",
    },
    # Multi-word (< 20ms)
    {
        "url": "/autocomplete",
        "params": {"query": "am neuen kamp", "limit": 10, "latitude": 54.0863, "longitude": 9.9757},
        "expected_time_ms": 20,
        "expected_result": "Am Neuen Kamp",
    },
    {
        "url": "/autocomplete",
        "params": {"query": "albert-schweitzer-straße", "limit": 10, "latitude": 54.0863, "longitude": 9.9757},
        "expected_time_ms": 20,
        "expected_result": "Albert-Schweitzer-Straße",
    },
    {
        "url": "/autocomplete",
        "params": {"query": "kieler straße", "limit": 10, "latitude": 54.0863, "longitude": 9.9757},
        "expected_time_ms": 20,
        "expected_result": "Kieler Straße",
    },
    {
        "url": "/autocomplete",
        "params": {"query": "kieler straße", "limit": 10, "latitude": 53.5974, "longitude": 10.2135},
        "expected_time_ms": 20,
        "expected_result": "Kieler Straße",
    },
    # Typo tolerance (< 200ms)
    {
        "url": "/autocomplete",
        "params": {"query": "galbelstraße", "limit": 10, "latitude": 53.5974, "longitude": 10.2135},
        "expected_time_ms": 200,
        "expected_result": "Gabelstraße",
    },
    {
        "url": "/autocomplete",
        "params": {"query": "gretenweg", "limit": 10},
        "expected_time_ms": 200,
        "expected_result": "Grethenweg",
        "expected_city": "Frankfurt am Main",
    },
    {
        "url": "/autocomplete",
        "params": {"query": "hannes-mayer-straße münchen", "limit": 10, "latitude": 54.0863, "longitude": 9.9757},
        "expected_time_ms": 500,
        "expected_result": "Hannes-Meyer-Straße",
        "expected_city": "München",
    },
    # City-in-query parsing (< 300ms)
    {
        "url": "/autocomplete",
        "params": {"query": "jungfernstieg hamburg", "limit": 10, "latitude": 54.0863, "longitude": 9.9757},
        "expected_time_ms": 300,
        "expected_result": "Jungfernstieg",
        "expected_city": "Hamburg",
    },
]

@pytest.mark.skipif(not _real_db_available(), reason="Real database not available")
@pytest.mark.parametrize("case", TEST_QUERIES)
def test_strict_performance(case):
    from httpx import AsyncClient, ASGITransport
    from main import app

    async def run():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Warm up
            await client.get("/autocomplete", params={"query": "test", "limit": 1})

            start = time.perf_counter()
            resp = await client.get(case["url"], params=case["params"])  # type: ignore
            elapsed_ms = (time.perf_counter() - start) * 1000

            assert resp.status_code == 200
            assert elapsed_ms < case["expected_time_ms"], f"{elapsed_ms:.0f}ms >= {case['expected_time_ms']}ms"

            results = resp.json()
            # Expected result present
            exp = case.get("expected_result")
            if exp:
                assert any(exp in r["name"] or r["name"].startswith(exp) for r in results), \
                    f"Expected '{exp}' not found in results: {[r['name'] for r in results]}"
            # Expected city present
            exp_city = case.get("expected_city")
            if exp_city:
                assert any(exp_city.lower() == r["city"].lower() for r in results), \
                    f"Expected city '{exp_city}' not found in results: {[r['city'] for r in results]}"

    asyncio.run(run())

if __name__ == "__main__":
    pytest.main([__file__, "-v"])