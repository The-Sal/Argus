from py_clob_client import ApiCreds
from argus.polymarket_direct.rest import REST_CACHE
from argus.polymarket_direct.wss import PolyMarketAccountEventWss


def do_testing_thing():
    creds: ApiCreds = REST_CACHE.get('_create_or_derive_api_creds:():{}')
    wss = PolyMarketAccountEventWss(auth={
        "apiKey": creds.api_key,
        "secret": creds.api_secret,
        "passphrase": creds.api_passphrase,
    })
    wss.wait_till_first_pong.wait()
    print('Now waiting to see how the WSS handles the disconnection...')
    input('Press Enter to exit...')


if __name__ == '__main__':
    do_testing_thing()
