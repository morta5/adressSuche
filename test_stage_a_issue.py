# Test to see what's happening with stage A
import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from main import app

@pytest.mark.asyncio
async def test():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Test with the exact coordinates from case10
        response = await client.get(
            "/autocomplete",
            params={
                "query": "kampst",
                "limit": 10,
                "latitude": 54.32730000000001,
                "longitude": 10.1234,
            },
        )
        
        results = response.json()
        print(f"Total results: {len(results)}")
        print("\nTop 15 results:")
        for i, r in enumerate(results[:15], 1):
            dist = r.get('distance_km', 'N/A')
            print(f"{i}. {r['name']:30} - {r['city']:25} (dist: {dist}km, score: {r['match_score']:.3f})")

asyncio.run(test())
