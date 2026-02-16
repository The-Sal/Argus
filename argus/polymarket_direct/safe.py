import os
import requests
from argus.wireproxy import wrapper as wp_wrappers



class IPSafety:
    def __init__(self):
        self.KNOWN_BAD_REGIONS = ["US", "GB", "FR", "DE", "IT", "BE", "PL", "AU", "SG", "TW",
                                  "TH", "RU", "BY", "CU", "IR",
                                  "IQ", "KP", "SY", "VE", "MM", "LY", "SD", "SS", "SO",
                                  "YE", "ZW", "LB", "ET", "NI", "BI", "CF", "CD", "UM", "AE"]

        self.session = requests.Session()
        wp_wrappers.update_request_session_proxy(
            idx='POLYMARKET',
            session=self.session,
            verbose=False
        )
        self._ip_info_token = os.environ.get('IPINFO_TOKEN', None)

    def get_ip_info(self) -> dict:
        """
        Fetch IP information from the ipinfo.io service.
        :return: A dictionary containing IP information.
        """
        response = self.session.get('https://ipinfo.io/json', headers=self.get_auth_headers(self))
        response.raise_for_status()
        return response.jsonx()

    def is_ip_in_bad_region(self, ip_info: dict) -> bool:
        """
        Determine if the IP is located in a known bad region.
        :param ip_info: A dictionary containing IP information.
        :return: True if the IP is in a bad region, False otherwise.
        """
        country = ip_info.get('country', '')
        return country in self.KNOWN_BAD_REGIONS

    @staticmethod
    def get_auth_headers(self):
        """
        Returns {} if no token is set, otherwise returns {'Authorization': 'Bearer <token>'}
        :return:
        """
        if not self._ip_info_token:
            return {}
        return {'Authorization': f'Bearer {self._ip_info_token}'}