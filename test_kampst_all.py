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
                "limit": 50,  # Get more results
                "latitude": 54.32730000000001,
                "longitude": 10.1234,
            },
        )
        
        results = response.json()
        
        # Find Neumünster
        neumuenster_pos = None
        for i, r in enumerate(results, 1):
            if r['city'] == 'Neumünster':
                neumuenster_pos = i
                print(f">>> #{i}. {r['name']:35} - {r['city']:25} (dist: {r['distance_km']}km, score: {r['match_score']:.3f})")
                break
        
        if not neumuenster_pos:
            print("Neumünster not found in top 50!")
        
        print(f"\nFirst 10:")
        for i, r in enumerate(results[:10], 1):
            print(f"    {i}. {r['name']:35} - {r['city']:25} (dist: {r['distance_km']}km, score: {r['match_score']:.3f})")

asyncio.run(test())
