# """
# The official 'py_clob_client' is incomplete
# """
# import json
#
# from requests import get as g
# from py_clob_client.client import ClobClient
# from argus.polymarket._types import PMarket, PMarketToken
#
# ADDITIONAL_ENDPOINTS = {
#     'events': "https://gamma-api.polymarket.com/events?order=id&ascending=false&closed=false&limit={}&offset={}",
# }
#
# class EnhancedPMClient(ClobClient):
#
#     @staticmethod
#     def get_events(limit: int = 50, offset: int = 0) -> list[dict]:
#         """
#         Fetches market events from Polymarket. Will NOT return closed events.
#
#         :param limit: The maximum number of events to retrieve.
#         :param offset: Used for pagination to skip a number of events.
#         :return:
#         """
#
#         url = ADDITIONAL_ENDPOINTS['events'].format(limit, offset)
#         response = g(url)
#         return response.json()
#
#
#
# if __name__ == '__main__':
#     client = EnhancedPMClient
#     events = client.get_events(limit=5)
#     for event in events:
#         print(PMarket(**event))