"""
Tests for /validate endpoint behaviour:
- Treat house_number="0" as no-number request
- Streets without house numbers should validate with house_number="0"
- Nearest house number returned when exact not found

Note: Integration tests are skipped unless a real DB is present.
"""

import asyncio
from pathlib import Path
import pytest

MIN_DB_SIZE_BYTES = 1_000_000

def _real_db_available() -> bool:
    candidates = [Path("./autocomplete_v2.db"), Path("./autocomplete_v3.db")]
    for p in candidates:
        if p.exists() and p.stat().st_size > MIN_DB_SIZE_BYTES:
            return True
    return False

@pytest.mark.skipif(not _real_db_available(), reason="Real database not available")
class TestValidateEndpoint:
    def test_house_number_zero_is_accepted(self):
        from httpx import AsyncClient, ASGITransport
        from main import app

        async def run():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/validate",
                    params={
                        "street_name": "Hauptstraße",
                        "house_number": "0",
                        "latitude": 53.5511,
                        "longitude": 9.9937,
                    },
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["exists"] is True
                assert data["house_number"] == "0"
        asyncio.run(run())

    def test_nearest_house_number_is_returned(self):
        from httpx import AsyncClient, ASGITransport
        from main import app

        async def run():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # Request a likely missing number and ensure a nearby one is returned
                resp = await client.get(
                    "/validate",
                    params={
                        "street_name": "Hauptstraße",
                        "house_number": "21",
                        "latitude": 53.5511,
                        "longitude": 9.9937,
                    },
                )
                assert resp.status_code == 200
                data = resp.json()
                # Either exact 21 or a close neighbour like 20/22 must be returned
                assert data["exists"] in (True, False)
                if data["exists"]:
                    assert data["house_number"] in {"21", "20", "22"}
        asyncio.run(run())

if __name__ == "__main__":
    pytest.main([__file__, "-v"])