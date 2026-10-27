"""``/validate``: Hausnummern mit Buchstaben werden unabhaengig von Gross-/Kleinschreibung und Leerzeichen gefunden.

Fehler: Der Abgleich war ein exakter Textvergleich. Stand in den Daten "11a" und tippte jemand "11A" (oder umgekehrt bei "12A"), fiel der Dienst auf die
Nummer ohne Buchstaben zurueck ("11"). In den Daten stehen rund 2,0 Mio. Hausnummern mit Buchstaben, davon 283 000 gross und 95 000 mit Leerzeichen.
"""
import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import main
from models import Address, Base, Street


async def _db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as s:
        strasse = Street(name="Achtern Knick", normalized_name="achternknick", normalized_search="achtern knick", city="Neumünster",
                         postal_code="24534", latitude=54.07, longitude=9.98, phonetic_german="x", phonetic_cologne="x")
        s.add(strasse)
        await s.flush()
        for nummer in ("10", "11", "11a", "12", "12A", "13 b"):
            s.add(Address(street_id=strasse.id, house_number=nummer, latitude=54.07, longitude=9.98))
        await s.commit()
    return engine, sessions


async def _validate(hausnummer):
    engine, sessions = await _db()
    try:
        async with sessions() as s:
            antwort = await main.validate_address(street_name="Achtern Knick", house_number=hausnummer, city="Neumünster",
                                                  latitude=None, longitude=None, db=s)
        return antwort.house_number if antwort.exists else None
    finally:
        await engine.dispose()


@pytest.mark.parametrize("eingabe, erwartet", [
    ("11a", "11a"), ("11A", "11a"), ("11 a", "11a"), (" 11 A ", "11a"),         # Daten klein
    ("12A", "12A"), ("12a", "12A"), ("12 a", "12A"),                              # Daten gross
    ("13b", "13 b"), ("13B", "13 b"), ("13 b", "13 b"),                           # Daten mit Leerzeichen
    ("11", "11"), ("12", "12"),                                                   # ohne Buchstaben unveraendert
])
def test_schreibweise_ist_egal(eingabe, erwartet):
    assert asyncio.run(_validate(eingabe)) == erwartet


def test_nicht_vorhandener_buchstabe_faellt_weiter_auf_die_naechste_nummer_zurueck():
    assert asyncio.run(_validate("11c")) in {"11", "11a"}
