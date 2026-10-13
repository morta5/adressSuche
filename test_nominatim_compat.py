"""Der Backend-Vertrag wird über die tatsächliche FastAPI-Route geprüft."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import json
from urllib.parse import urlencode
import pytest
import main


async def route_request(query):
    messages = []
    async def receive():
        return {'type':'http.request', 'body':b'', 'more_body':False}
    async def send(message):
        messages.append(message)
    scope = {'type':'http','asgi':{'version':'3.0'},'http_version':'1.1',
             'method':'GET','scheme':'http','path':'/nominatim/search',
             'raw_path':b'/nominatim/search','query_string':urlencode(query).encode(),
             'headers':[],'server':('fixture',80),'client':('127.0.0.1',1234),'root_path':''}
    await main.app(scope, receive, send)
    status = next(item['status'] for item in messages if item['type'] == 'http.response.start')
    body = b''.join(item.get('body', b'') for item in messages if item['type'] == 'http.response.body')
    return SimpleNamespace(status_code=status, json=lambda: json.loads(body))


@pytest.mark.asyncio
async def test_nominatim_suche_validiert_hausnummer_und_stadt(monkeypatch):
    validate = AsyncMock(return_value=SimpleNamespace(exists=True, latitude=54.0, longitude=10.0))
    monkeypatch.setattr(main, "validate_address", validate)
    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[main.get_async_db] = lambda: None
    try:
        response = await route_request({"q":"Stagingallee 1, 00000 Teststadt", "format":"json", "limit":1})
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
        response = await route_request(query)
        assert response.status_code == expected_status
        if expected_status == 200:
            assert response.json() == []
        else:
            validate.assert_not_awaited()
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)
