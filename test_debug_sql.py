import asyncio
import pytest
from sqlalchemy import text
from database import AsyncSessionLocal

@pytest.mark.asyncio
async def test():
    async with AsyncSessionLocal() as db:
        # Test if Jungfernstieg exists in Norderstedt
        sql = """
            SELECT name, city 
            FROM streets 
            WHERE name LIKE 'Jungfernstieg%' 
            AND LOWER(city) LIKE 'norderstedt%'
            LIMIT 5
        """
        res = await db.execute(text(sql))
        rows = res.fetchall()
        
        print(f"Found {len(rows)} Jungfernstieg in Norderstedt:")
        for r in rows:
            print(f"  {r._mapping['name']} - {r._mapping['city']}")
        
        # Also check all Jungfernstieg
        sql2 = """
            SELECT name, city 
            FROM streets 
            WHERE name LIKE 'Jungfernstieg%'
            LIMIT 20
        """
        res2 = await db.execute(text(sql2))
        rows2 = res2.fetchall()
        
        print(f"\nAll Jungfernstieg (first 20):")
        cities = {}
        for r in rows2:
            city = r._mapping['city']
            cities[city] = cities.get(city, 0) + 1
        for city, count in sorted(cities.items()):
            print(f"  {city}: {count}")

asyncio.run(test())
