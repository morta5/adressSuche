import asyncio
from sqlalchemy import text

from database import init_db, AsyncSessionLocal
from main import app
from models import Street


async def seed_if_empty():
    async with AsyncSessionLocal() as session:
        res = await session.execute(text("SELECT COUNT(*) FROM streets"))
        count = res.scalar() or 0
        if count:
            return
        samples = [
            ("Bahnhofstraße", "Musterstadt", 52.5, 13.4),
            ("Schiller Allee", "Musterstadt", 52.51, 13.41),
            ("Friedrich-Ebert-Straße", "Berlin", 52.52, 13.39),
            ("Geibelstraße", "Hannover", 52.37, 9.74),
        ]
        for name, city, lat, lon in samples:
            s = Street(name=name, city=city, latitude=lat, longitude=lon)
            session.add(s)
        await session.commit()


async def run():
    init_db()
    await seed_if_empty()
    print("Seeded. Now try hitting /autocomplete manually.")


if __name__ == "__main__":
    asyncio.run(run())
