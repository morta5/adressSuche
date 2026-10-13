"""Staging darf sein Schema ausschließlich vor dem API-Start kontrolliert ändern."""
import pytest
import main


@pytest.mark.asyncio
async def test_externes_schema_wird_beim_start_nicht_geaendert(monkeypatch):
    calls = []
    monkeypatch.setenv('ADDRESS_AUTO_MIGRATE', '0')
    monkeypatch.setattr(main, 'init_db', lambda: calls.append('migration'))
    await main._on_startup()
    assert calls == []


@pytest.mark.asyncio
async def test_bestandsstart_bleibt_ohne_staging_schalter_erhalten(monkeypatch):
    calls = []
    monkeypatch.delenv('ADDRESS_AUTO_MIGRATE', raising=False)
    monkeypatch.setattr(main, 'init_db', lambda: calls.append('migration'))
    await main._on_startup()
    assert calls == ['migration']


@pytest.mark.asyncio
async def test_ungueltiger_schemaschalter_sperrt_den_start(monkeypatch):
    monkeypatch.setenv('ADDRESS_AUTO_MIGRATE', 'falsch')
    with pytest.raises(ValueError):
        await main._on_startup()
