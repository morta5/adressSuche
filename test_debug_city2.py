import asyncio
import pytest
from database import AsyncSessionLocal
from main import _get_known_cities, _extract_city_from_query

@pytest.mark.asyncio
async def test():
    async with AsyncSessionLocal() as db:
        known_cities = await _get_known_cities(db)
        
        # Test 1: jungfernstieg norderstedt
        query1, city1 = await _extract_city_from_query(
            "jungfernstieg norderstedt", known_cities, 54.0863, 9.9757, db
        )
        print(f"Test 1: 'jungfernstieg norderstedt'")
        print(f"  Query: '{query1}'")
        print(f"  City: '{city1}'")
        print(f"  Norderstedt in known_cities: {'norderstedt' in known_cities}")
        
        # Test 2: kampstraße neum
        query2, city2 = await _extract_city_from_query(
            "kampstraße neum", known_cities, 54.0724, 9.9858, db
        )
        print(f"\nTest 2: 'kampstraße neum'")
        print(f"  Query: '{query2}'")
        print(f"  City: '{city2}'")

asyncio.run(test())
