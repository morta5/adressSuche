"""Der Backend-Vertrag wird über die tatsächliche FastAPI-Route geprüft."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import main


@pytest.mark.asyncio
async def test_nominatim_suche_validiert_hausnummer_und_stadt(monkeypatch):
    validate = AsyncMock(return_value=SimpleNamespace(exists=True, latitude=54.0, longitude=10.0))
    monkeypatch.setattr(main, "validate_address", validate)
    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[main.get_async_db] = lambda: None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://fixture") as client:
            response = await client.get("/nominatim/search", params={"q":"Stagingallee 1, 00000 Teststadt", "format":"json", "limit":1})
        assert response.status_code == 200
        data = response.json()
        assert data[0]["lat"] == "54.0" and data[0]["lon"] == "10.0"
        assert validate.await_args.kwargs["street_name"] == "Stagingallee"
        assert validate.await_args.kwargs["house_number"] == "1"
        assert validate.await_args.kwargs["city"] == "Teststadt"
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)


@pytest.mark.asyncio
@pytest.mark.parametrize('query, expected_status', [
    ({'q':'Unbekanntweg 5, Teststadt','format':'json'}, 200),
    ({'q':'Stagingallee 1','format':'xml'}, 422),
    ({'q':'Stagingallee 1','limit':0}, 422),
    ({'q':'x' * 513}, 422),
])
async def test_nominatim_unbekannte_adresse_und_ungueltige_parameter(monkeypatch, query, expected_status):
    validate = AsyncMock(return_value=SimpleNamespace(exists=False, latitude=None, longitude=None))
    monkeypatch.setattr(main, 'validate_address', validate)
    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[main.get_async_db] = lambda: None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://fixture') as client:
            response = await client.get('/nominatim/search', params=query)
        assert response.status_code == expected_status
        if expected_status == 200:
            assert response.json() == []
        else:
            validate.assert_not_awaited()
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)
