"""Die bestehende Kundenoberfläche erwartet GeoJSON mit Länge vor Breite."""
import main


def test_kompatible_geometrie_und_adressfelder():
    feature = main.photon_feature({'street_id': 3, 'name': 'Stagingallee', 'city': 'Teststadt',
        'postal_code': '00000', 'latitude': 54.0, 'longitude': 10.0})
    assert feature['type'] == 'Feature'
    assert feature['geometry'] == {'type': 'Point', 'coordinates': [10.0, 54.0]}
    assert feature['properties']['street'] == 'Stagingallee'
    assert feature['properties']['city'] == 'Teststadt'
    assert feature['properties']['postcode'] == '00000'
