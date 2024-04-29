import pytest

from etalab_apis.api_gps import EtalabGpsApi


@pytest.mark.asyncio
async def test_get_gps_coordinates():
    """test the gps method with a full postal address that includes
        city and postcode
    """
    postal_address = "2 rue de la paix 75002 Paris"
    dvf_api = EtalabGpsApi()

    gps_datas = await dvf_api.get_gps_coordinates(postal_address=postal_address)
    assert gps_datas.get("city") is not None
    assert isinstance(gps_datas.get("lat"), float)
    assert isinstance(gps_datas.get("lng"), float)


@pytest.mark.asyncio
async def test_get_gps_coordinates_with_insee():
    """test the gps method with an address that has no postcode or city
        but has an INSEE commune code
    """
    postal_address = "2 rue de la paix"
    insee_city_code = '75102'
    dvf_api = EtalabGpsApi()

    gps_datas = await dvf_api.get_gps_coordinates(postal_address=postal_address,
                                                  insee_city_code=insee_city_code)
    assert gps_datas.get("city") is not None
    assert isinstance(gps_datas.get("lat"), float)
    assert isinstance(gps_datas.get("lng"), float)


@pytest.mark.asyncio
async def test_batch_gps_coordinates():
    """test the gps method with a List addresses that have postcode and city

    """
    my_postal_addresses: list = ["2 rue de la paix 75002 Paris",
                                 "29 rue de la paix 75002 Paris",
                                 ]
    dvf_api = EtalabGpsApi()

    gps_datas = await dvf_api.batch_gps_coordinates(postal_addresses=my_postal_addresses,
                                                    )
    for gps_data in gps_datas:
        assert gps_data.get("city") is not None
        assert isinstance(gps_data.get("lat"), float)
        assert isinstance(gps_data.get("lng"), float)


@pytest.mark.asyncio
async def test_batch_gps_coordinates_with_insee():
    """test the gps method with a List Tuples containing the address and the commune INSEE code
    """
    addresses_insees: list = [("2 rue de la paix 75002 Paris", '75102'),
                              ("29 rue de la paix 75002 Paris", '75102')
                              ]
    dvf_api = EtalabGpsApi()

    gps_datas = await dvf_api.batch_gps_coordinates(addresses_insees=addresses_insees
                                                    )
    for gps_data in gps_datas:
        assert gps_data.get("city") is not None
        assert isinstance(gps_data.get("lat"), float)
        assert isinstance(gps_data.get("lng"), float)
