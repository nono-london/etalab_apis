import pytest

from etalab_apis.api_gps import EtalabGpsApi


@pytest.mark.asyncio
async def test_get_gps_coordinates():
    postal_address = "2 rue de la paix 75002 Paris"
    dvf_api = EtalabGpsApi()

    gps_datas = await dvf_api.get_gps_coordinates(postal_address=postal_address)
    assert gps_datas.get("city") is not None
    assert isinstance(gps_datas.get("lat"), float)
    assert isinstance(gps_datas.get("lng"), float)


@pytest.mark.asyncio
async def test_get_gps_coordinates_with_insee():
    postal_address = "2 rue de la paix"
    insee_city_code = '75102'
    dvf_api = EtalabGpsApi()

    gps_datas = await dvf_api.get_gps_coordinates(postal_address=postal_address,
                                                  insee_city_code=insee_city_code)
    assert gps_datas.get("city") is not None
    assert isinstance(gps_datas.get("lat"), float)
    assert isinstance(gps_datas.get("lng"), float)
