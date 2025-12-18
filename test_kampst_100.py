import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from main import app

@pytest.mark.asyncio
async def test():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/autocomplete",
            params={
                "query": "kampst",
                "limit": 100,
                "latitude": 54.32730000000001,
                "longitude": 10.1234,
            },
        )
        
        results = response.json()
        
        # Find Neumünster
        for i, r in enumerate(results, 1):
            if r['city'] == 'Neumünster':
                print(f"FOUND: #{i}. {r['name']:35} - {r['city']:25} (dist: {r['distance_km']}km, score: {r['match_score']:.3f})")
                return
        
        print("Neumünster NOT found in top 100!")
        print(f"\nShowing 'Kampstraße' results sorted by distance:")
        kampstrasse = [(i+1, r) for i, r in enumerate(results) if 'Kampstraße' in r['name']]
        for i, r in kampstrasse[:20]:
            marker = ">>>" if r['city'] == 'Neumünster' else "   "
            print(f"{marker} #{i}. {r['name']:35} - {r['city']:25} (dist: {r['distance_km']}km)")

asyncio.run(test())
