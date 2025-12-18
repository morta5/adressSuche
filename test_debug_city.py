import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from main import app

@pytest.mark.asyncio
async def test():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Test city extraction
        response = await client.get(
            "/autocomplete",
            params={
                "query": "jungfernstieg norderstedt",
                "limit": 10,
                "latitude": 54.0863,
                "longitude": 9.9757,
            },
        )
        
        print("Status:", response.status_code)
        results = response.json()
        print("\nResults:")
        for i, r in enumerate(results[:5], 1):
            print(f"{i}. {r['name']} - {r['city']} (score: {r['match_score']:.2f})")

asyncio.run(test())
