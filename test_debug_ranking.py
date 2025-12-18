import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from main import app

# Monkey-patch to add debug output
original_autocomplete = None

@pytest.mark.asyncio
async def test():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Test with "kampstraße" (full word) instead of "kampst" to see if it helps
        response = await client.get(
            "/autocomplete",
            params={
                "query": "kampstraße",
                "limit": 10,
                "latitude": 54.32730000000001,
                "longitude": 10.1234,
            },
        )
        
        results = response.json()
        print(f"Query: 'kampstraße' near (54.327, 10.123)")
        print(f"Total results: {len(results)}\n")
        for i, r in enumerate(results[:15], 1):
            dist = r.get('distance_km', 'N/A')
            marker = ">>> " if r['city'] == 'Neumünster' else "    "
            print(f"{marker}{i}. {r['name']:30} - {r['city']:25} (dist: {dist}km, score: {r['match_score']:.3f})")

asyncio.run(test())
