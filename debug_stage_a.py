import asyncio
from sqlalchemy import text
from database import AsyncSessionLocal

async def test():
    async with AsyncSessionLocal() as db:
        search_lat = 54.32730000000001
        search_lon = 10.1234
        
        # Simulate the exact query from stage_a
        sql = """
            SELECT id, name, city, postal_code, latitude, longitude FROM (
                SELECT id, name, city, postal_code, latitude, longitude
                FROM streets
                WHERE name >= 'kampst' AND name < 'kampsu'
                UNION
                SELECT id, name, city, postal_code, latitude, longitude
                FROM streets
                WHERE normalized_name >= 'kampst' AND normalized_name < 'kampsu'
            ) sub
            ORDER BY ((latitude - :user_lat) * (latitude - :user_lat) + (longitude - :user_lon) * (longitude - :user_lon))
            LIMIT 500
        """
        
        res = await db.execute(text(sql), {"user_lat": search_lat, "user_lon": search_lon})
        rows = res.fetchall()
        
        print(f"Found {len(rows)} rows with limit 500")
        print("\nFirst 25 results:")
        for i, r in enumerate(rows[:25], 1):
            city = r._mapping['city']
            marker = ">>> " if city == 'Neumünster' else "    "
            print(f"{marker}{i}. {r._mapping['name']:30} - {city}")

asyncio.run(test())
