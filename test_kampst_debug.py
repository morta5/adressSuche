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
                "limit": 15,
                "latitude": 54.32730000000001,
                "longitude": 10.1234,
            },
        )
        
        results = response.json()
        print(f"Query: 'kampst'")
        print(f"Results: {len(results)}\n")
        for i, r in enumerate(results, 1):
            marker = ">>> " if r['city'] == 'Neumünster' else "    "
            print(f"{marker}{i}. {r['name']:35} - {r['city']:25} (score: {r['match_score']:.3f})")

asyncio.run(test())
