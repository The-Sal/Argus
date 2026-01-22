import os
import time
import traceback
from termcolor import colored
from dotenv import load_dotenv
from argus._argus_utils import throw_fuss
from argus.polymarket_direct.rest import PolyRestAPI, PolyMarketAccountEventWss, pm_types

def do_the_test():
    # noinspection PyProtectedMember
    from argus.polymarket_direct._examples.unsub_test import get_all_btc_live_events
    load_dotenv()

    #########################################################################################################
    # WARNING: Be VERY careful when disabling safety checks! Only disable during tight-development loops.
    # where safety checks have been completed at least once and you are SURE of what you are doing.
    #########################################################################################################
    # os.environ['POLYMARKET_NO_SAFETY_CHECK'] = 'true'
    #########################################################################################################

    def fatal_handler(info: dict):
        print(colored(f"[{__name__}] FATAL ERROR HANDLER TRIGGERED. CANCELLING ALL ORDERS.", 'red',
                      attrs=['bold', 'blink']))
        classx: PolyRestAPI = info.get('self')
        if classx:
            for order in classx.order_cache['orders']:
                classx.cancel_order(order_id=order['orderID'])

    rest = PolyRestAPI(
        private_key=os.environ['POLYMARKET_PRIVATE_KEY'],
        proxy_funder=os.environ['POLYMARKET_PROXY_FUNDER'],
        fatal_callback=fatal_handler
    )

    wss = PolyMarketAccountEventWss(rest.credentials)
    wss.wait_till_socket_open.wait()
    print('WebSocket is open and running. Listening for account events...')

    def a_test_function():
        event: pm_types.PolymarketEvent = get_all_btc_live_events()[0]
        print('Testing with event:', event.ticker)
        tkn_id = event.markets[0].clobTokenIds[0]
        print('tkn_id:', tkn_id)
        print(rest.get_balance())
        ordddr = rest.place_order(token_id=tkn_id, price=float(rest.get_tick_size(tkn_id)), size=5, side='buy',
                                  market=event)
        order_property = rest.order_cache['orders']
        order_id = order_property[-1]['orderID']

        # noinspection PyBroadException
        try:
            rest.get_order_status(ordddr['orderID'])
        except:
            traceback.print_exc()

        throw_fuss("Order placed, waiting to cancel...", notify=False)
        print('Canceling order...')
        rest.cancel_order(order_id=order_id)
        time.sleep(1)
        print(rest.get_trades())

    a_test_function()
    input('Press Enter to exit...\n')

if __name__ == '__main__':
    do_the_test()