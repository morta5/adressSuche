import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from main import app

@pytest.mark.asyncio
async def test():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Test case10: kampst near Neumünster
        response = await client.get(
            "/autocomplete",
            params={
                "query": "kampst",
                "limit": 10,
                "latitude": 54.32730000000001,
                "longitude": 10.1234,
            },
        )
        
        print("Query: kampst near Neumünster (54.327, 10.123)")
        print("Results:")
        for i, r in enumerate(response.json()[:10], 1):
            print(f"{i}. {r['name']} - {r['city']} (dist: {r.get('distance_km', 'N/A')}km)")

asyncio.run(test())
