"""
A module to test client behavior and order execution with PolymarketDispatcher.

This module defines a `TestClient` class for handling protocol communication and
order execution testing, along with a `run_test` function for integration testing.

Classes
-------
TestClient
    Implements a client to interact with PolymarketDispatcher for subscribing to
    market tickers and processing received data.

Functions
---------
run_test()
    Runs the test scenario to ensure proper functionality of the system.

Results
---------
[TIMER][_handle_place_multiple_orders] start: start
[TIMER][_handle_place_multiple_orders] after_block_check: start
[TIMER][_handle_place_multiple_orders] after_get_args: start
[TIMER][_handle_place_multiple_orders] after_get_orders_list: start
[TIMER][_handle_place_multiple_orders] after_check_orders_list_empty: start
[TIMER][_handle_place_multiple_orders] after_check_orders_list_type: start
[TIMER][_handle_place_multiple_orders] before_order_specs_loop: start
[TIMER][_handle_place_multiple_orders] loop_start: start
[TIMER][_handle_place_multiple_orders] after_get_token_id: start
[TIMER][_handle_place_multiple_orders] after_check_token_id: start
[TIMER][_handle_place_multiple_orders] after_get_price: start
[TIMER][_handle_place_multiple_orders] after_check_price: start
[TIMER][_handle_place_multiple_orders] after_get_size: start
[TIMER][_handle_place_multiple_orders] after_check_size: start
[TIMER][_handle_place_multiple_orders] after_get_side: start
[TIMER][_handle_place_multiple_orders] after_check_side: start
[TIMER][_handle_place_multiple_orders] after_resolve_market: start
[TIMER][_handle_place_multiple_orders] after_get_tick_size: start
[TIMER][_handle_place_multiple_orders] after_append_order_spec: start
[TIMER][_handle_place_multiple_orders] loop_start: +0.547ms
[TIMER][_handle_place_multiple_orders] after_get_token_id: +0.563ms
[TIMER][_handle_place_multiple_orders] after_check_token_id: +0.570ms
[TIMER][_handle_place_multiple_orders] after_get_price: +0.573ms
[TIMER][_handle_place_multiple_orders] after_check_price: +0.576ms
[TIMER][_handle_place_multiple_orders] after_get_size: +0.580ms
[TIMER][_handle_place_multiple_orders] after_check_size: +0.583ms
[TIMER][_handle_place_multiple_orders] after_get_side: +0.585ms
[TIMER][_handle_place_multiple_orders] after_check_side: +0.587ms
[TIMER][_handle_place_multiple_orders] after_resolve_market: +0.269ms
[TIMER][_handle_place_multiple_orders] after_get_tick_size: +0.190ms
[TIMER][_handle_place_multiple_orders] after_append_order_spec: +0.182ms
[TIMER][_handle_place_multiple_orders] after_order_specs_loop: start
[TIMER][_handle_place_multiple_orders] after_built_orders_init: start
[TIMER][_handle_place_multiple_orders] after_build_errors_init: start
[TIMER][_handle_place_multiple_orders] after_start_time: start
[TIMER][build_order] start: start[TIMER][build_order] start: start
[TIMER][build_order] after_tick_size_check: start
[TIMER][build_order] after_mapped_dict: start
[TIMER][build_order] after_get_type_side: start
[TIMER][build_order] after_type_side_validation: start
[TIMER][build_order] before_create_order: start
[TIMER][_handle_place_multiple_orders] after_submit_futures: start
[TIMER][build_order] after_tick_size_check: +1.348ms
[TIMER][build_order] after_mapped_dict: +1.347ms
[TIMER][build_order] after_get_type_side: +1.343ms
[TIMER][build_order] after_type_side_validation: +1.698ms
[TIMER][build_order] before_create_order: +3.681ms
[TIMER][build_order] after_create_order: start[TIMER][build_order] after_create_order: start
[TIMER][_handle_place_multiple_orders] future_loop_start: start
[TIMER][_handle_place_multiple_orders] after_get_spec: start
[TIMER][_handle_place_multiple_orders] before_future_result: start
[TIMER][_handle_place_multiple_orders] after_future_result: start
[TIMER][_handle_place_multiple_orders] after_append_built_order: start
[TIMER][_handle_place_multiple_orders] future_loop_start: +0.032ms
[TIMER][_handle_place_multiple_orders] after_get_spec: +0.032ms
[TIMER][_handle_place_multiple_orders] before_future_result: +0.032ms
[TIMER][_handle_place_multiple_orders] after_future_result: +0.030ms
[TIMER][_handle_place_multiple_orders] after_append_built_order: +0.030ms
[TIMER][_handle_place_multiple_orders] after_threadpool_done: start
[TIMER][_handle_place_multiple_orders] after_build_errors_check: start
[TIMER][_handle_place_multiple_orders] after_built_orders_check: start
INFO:root:Successfully Built 2 orders in 0.0062 seconds. Placing batch order now.
[TIMER][_handle_place_multiple_orders] after_build_log: start
[TIMER][_handle_place_multiple_orders] before_place_built_orders: start

Time taken to place order: 0.0074 seconds
"""

import os
import json
import time
import socket
import logging
logging.basicConfig(level=logging.DEBUG)
from utils3 import runAsThread
from poly_cli import P2PacketParser, decode_packet
from argus.polymarket import PolymarketDispatcher, ArgsObject, encode_packet, OrderExecutionDisabledError



class TestClient:
    def __init__(self):
        self.null_socket = socket.socket()
        self.null_socket.connect(('localhost', 12345))
        self.msgs = {
            'protocol_1': [],
            'protocol_2': [],
        }
        self.recv_feed()

    @staticmethod
    def generate_btc_15_market():
        # https://polymarket.com/event/
        mkt = "btc-updown-15m-{}"
        # we need a unix timestamp of the last 15 min block which is
        # divisible by 900 (15 min in seconds)
        now = int(time.time())
        block_num = now // 900 * 900
        return mkt.format(block_num)

    @runAsThread
    def recv_feed(self):
        p2 = P2PacketParser()
        while True:
            try:
                data = self.null_socket.recv(9999)
            except OSError:
                return
            try:
                if data.endswith(b'L') and data.startswith(b'~'):
                    self.msgs['protocol_2'].append(p2.parse(data))
                else:
                    self.msgs['protocol_1'].append(json.loads(decode_packet(data)))
            except Exception as e:
                _ = e
                pass

    def subscribe(self, ticker):
        msg = {
            "action": "subscribe_to_market_by_ticker",
            "data": [ticker]
        }
        pkt = encode_packet(json.dumps(msg).encode())
        self.null_socket.sendall(pkt)


def run_test():
    os.environ['POLYMARKET_MEMORY_PRUNING'] = 'false'
    os.environ['POLYMARKET_NO_SAFETY_CHECK'] = 'true'

    dispatcher = PolymarketDispatcher(port=12345, host='localhost')
    dispatcher._configs['Block Order Execution'] = True
    dispatcher._configs['show response times'] = True
    dispatcher.run()

    tc = TestClient()
    clob_ids_req = dispatcher._handle_fetch_market_by_ticker(ArgsObject(None, [tc.generate_btc_15_market()]))
    tokens = clob_ids_req['markets'][0]['clobTokenIds']
    up_token, down_token = tokens
    print(f"up_token: {up_token}")
    print(f"down_token: {down_token}")
    tc.subscribe(tc.generate_btc_15_market())
    time.sleep(5)
    latest_msg = tc.msgs['protocol_2'][-1]
    print(f"latest_msg: {latest_msg}")
    latest_ask = latest_msg['ask_0_price']
    print(f"latest_ask: {latest_ask}")
    start = time.perf_counter()
    try:
        _ = dispatcher._handle_place_multiple_orders(ArgsObject(None, {
            "orders": [{
                "token_id": up_token,
                "price": latest_ask,
                "size": latest_msg['ask_0_size'],
                "side": "buy",
            }, {
                "token_id": up_token,
                "price": latest_ask,
                "size": latest_msg['ask_0_size'],
                "side": "buy",
            }]
        }))
    except OrderExecutionDisabledError:
        pass
    end_time = time.perf_counter()
    tc.null_socket.close()
    del dispatcher
    del tc
    time.sleep(5)

    # print how long it took in seconds
    diff = (end_time - start)
    print(f"Time taken to place order: {diff:.4f} seconds")
    if diff > 0.05:
        raise Exception("Order placement took too long, cache system is not working")

if __name__ == '__main__':
    run_test()
