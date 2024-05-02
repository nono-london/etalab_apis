import asyncio
from math import ceil
from time import time
from typing import (Union, Dict,
                    Optional, List, Tuple)

import aiohttp
import numpy as np
from tqdm import tqdm


class EtalabGpsApi:
    def __init__(self):
        pass

    @staticmethod
    async def _build_url(postal_address: str, insee_city_code: Optional[str] = None, limit: int = 1) -> str:
        """Use French gov API: https://api-adresse.data.gouv.fr/search/?q=8+bd+du+port&limit=1"""
        if insee_city_code:
            url = f"https://api-adresse.data.gouv.fr/search/?q={postal_address}&citycode={insee_city_code}&limit={limit}"
        else:
            url = f"https://api-adresse.data.gouv.fr/search/?q={postal_address}&limit={limit}"
        return url

    @staticmethod
    async def _read_json_response(json_response: dict) -> Union[Dict, None]:
        json_response = json_response.get("features")
        if json_response is None or len(json_response) == 0:
            return None

        gps = json_response[0].get("geometry").get("coordinates")  # (LNG, LAT)
        if gps is None:
            return None

        if isinstance(gps, list):
            gps = tuple(gps)
        postcode = json_response[0].get("properties").get("postcode")
        insee_city_code = json_response[0].get("properties").get("citycode")
        city = json_response[0].get("properties").get("city")
        postal_address = json_response[0].get("properties").get("label")
        gps_dict = {'gps': gps, "postcode": postcode, "insee_city_code": insee_city_code, "city": city,
                    'postal_address': postal_address,
                    'lat': gps[1], 'lng': gps[0]
                    }

        return gps_dict

    async def get_gps_coordinates(self, postal_address: str, insee_city_code: Optional[str] = None, limit: int = 1) -> \
            Union[Dict]:
        result: Union[Dict, None] = None

        # check that address has between 3 and 200 chars
        if len(postal_address) < 4:
            result['found_result'] = True
            result['postal_address'] = postal_address
        postal_address = postal_address[:200]

        url: str = await self._build_url(postal_address=postal_address, insee_city_code=insee_city_code, limit=limit)

        # mysql_connection = aiohttp.TCPConnector(limit=5)
        # mysql_connection=mysql_connection
        async with aiohttp.ClientSession() as session:
            async with session.get(url=url, ) as response:
                if response.status == 200:
                    try:
                        json_response = await response.json()
                        result = await self._read_json_response(json_response=json_response)
                    except Exception as ex:
                        print("Json error:", ex)

                elif response.status == 504:
                    # request is taking too long
                    pass
                else:
                    print(f"Error status: {response.status}\n"
                          f"Message: {await response.json()}")

                await asyncio.sleep(0.1)

        if result is not None:
            result['found_result'] = True
            result['postal_address'] = postal_address
        else:
            result = {'found_result': False, 'postal_address': postal_address}

        return result

    async def batch_gps_coordinates(self, postal_addresses: Optional[List] = None,
                                    addresses_insees: Optional[List[Tuple]] = None) -> List:
        """
        Get gps coordinates from a list of addresses or a list of address, commune INSEE code tuples
        :param postal_addresses: a list of addresses
        :param addresses_insees: a list of tuples with [0] being the postal address and [1] being the insse code
        :return: a list of gps/city... dicts that contain the postal_address key being the one used in the query
        """
        max_calls: int = 5
        if postal_addresses:
            addresses_splits = list(np.array_split(postal_addresses, ceil(len(postal_addresses) / max_calls)))
        else:
            addresses_splits = list(np.array_split(addresses_insees, ceil(len(addresses_insees) / max_calls)))

        results = []
        for addresses_split in tqdm(addresses_splits):
            if postal_addresses:
                tasks = [asyncio.create_task(self.get_gps_coordinates(postal_address=postal_address))
                         for postal_address in addresses_split]
            else:
                tasks = [asyncio.create_task(self.get_gps_coordinates(postal_address=postal_address[0],
                                                                      insee_city_code=postal_address[1])
                                             ) for postal_address in addresses_split]
            result = await asyncio.gather(*tasks)
            await asyncio.sleep(0.1)
            results.extend(result)

        return results

    async def get_address_from_gps(self, gps_long_lat: Union[Dict, Tuple],
                                   limit: int = 1) -> Union[None, Dict]:
        """Use French gov API: https://api-adresse.data.gouv.fr/reverse/?lon=${longitude}&lat=${latitude}"""

        long, lat = None, None
        if isinstance(gps_long_lat, dict):
            long = gps_long_lat['long']
            lat = gps_long_lat['lat']
        if isinstance(gps_long_lat, tuple):
            long = gps_long_lat[0]
            lat = gps_long_lat[1]
        if not long or not lat:
            return None

        url: str = f"""https://api-adresse.data.gouv.fr/reverse/?lon={long}&lat={lat}"""
        result = None
        async with aiohttp.ClientSession() as session:
            async with session.get(url=url, ) as response:
                if response.status == 200:
                    json_response = await response.json()
                    result = await self._read_json_response(json_response=json_response)
                else:
                    print(f"Error status: {response.status}\n"
                          f"Message: {await response.json()}")
                await asyncio.sleep(0.1)

            return result


if __name__ == '__main__':
    start = time()

    my_postal_addresses: list = [("VILLARS LES DOMBES", "01443"),
                                 ("DIVONNE LES BAINS", "01143"),
                                 ("YZEURE", "03400")]

    dvf_api = EtalabGpsApi()
    try:
        gps_datas = asyncio.run(dvf_api.batch_gps_coordinates(addresses_insees=my_postal_addresses))
        print(gps_datas)
        postal_address = "1 FOND DE BOSSART 08460 NEUFMAISON"
        gps_datas = asyncio.run(dvf_api.get_gps_coordinates(postal_address=postal_address))
        print(gps_datas)

    except Exception as e:
        print(e)

    print(f'App ran in {round(time() - start, 3)} seconds')
