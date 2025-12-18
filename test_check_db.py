import asyncio
import pytest
from sqlalchemy import text
from database import AsyncSessionLocal

@pytest.mark.asyncio
async def test():
    async with AsyncSessionLocal() as db:
        # Check Kampstraße in Neumünster
        sql = """
            SELECT name, city 
            FROM streets 
            WHERE name LIKE 'Kampstraße%' 
            AND city LIKE 'Neumünster%'
            LIMIT 5
        """
        res = await db.execute(text(sql))
        rows = res.fetchall()
        
        print(f"Kampstraße in Neumünster: {len(rows)} found")
        for r in rows:
            print(f"  {r._mapping['name']} - {r._mapping['city']}")

asyncio.run(test())
