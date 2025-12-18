import asyncio
from sqlalchemy import text, func
from database import AsyncSessionLocal

async def test():
    async with AsyncSessionLocal() as db:
        # Get average coordinates for Neumünster
        sql = """
            SELECT AVG(latitude) as lat, AVG(longitude) as lon
            FROM streets
            WHERE city = 'Neumünster'
        """
        res = await db.execute(text(sql))
        row = res.fetchone()
        
        print(f"Neumünster center: {row._mapping['lat']:.6f}, {row._mapping['lon']:.6f}")
        
        # Check if Kampstraße exists in Neumünster
        sql2 = """
            SELECT name, city, latitude, longitude
            FROM streets
            WHERE name LIKE 'Kampstraße%' AND city = 'Neumünster'
        """
        res2 = await db.execute(text(sql2))
        for r in res2.fetchall():
            print(f"  {r._mapping['name']}: {r._mapping['latitude']:.6f}, {r._mapping['longitude']:.6f}")

asyncio.run(test())
