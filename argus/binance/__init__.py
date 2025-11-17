import json
import uuid
import time
import platform
import threading
from utils3 import runAsThread
from websocket import WebSocketApp
from argus._argus_utils import throw_fuss
from argus.binance._classes import (DepthUpdate, DepthStreamMessage, AggTradeMessage,
                                    AggTradeData, KlineEventData, KlineData, KlineMessage)

class BinanceTypes:
    DEPTH_STREAM = 'depth_stream'
    AGG_TRADE = 'agg_trade'
    KLINE = 'kline'

class AbstractBinanceType:
    """
    Abstract wrapper for Binance WebSocket message types.
    The only attribute directly accessible is 'idx' to identify the type.
    Everything else is taken from the 'obj' attribute.
    """
    def __init__(self, idx: str, obj: object):
        self.idx = idx
        self.obj = obj

class BinanceWssConfig:
    AUTO_DUMP = 'auto_dump'
    TOTAL_MESSAGE_STATISTICS = 'total_message_statistics'
    SHOW_ME_CHARTS = 'show_me_charts'

class BinanceWss:
    def __init__(self):
        self.endpoint = 'wss://stream.binance.com/stream'
        self.ws = WebSocketApp(
            url=self.endpoint,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

        self.semaphore = threading.Semaphore(0)
        self.init_websocket()


        self.callbacks = {}
        self.msgs = []
        self.stats_stamps = []
        self.configs = {
            BinanceWssConfig.AUTO_DUMP: True,
            BinanceWssConfig.TOTAL_MESSAGE_STATISTICS: True,
            BinanceWssConfig.SHOW_ME_CHARTS: True,
        }

        if platform.platform() != 'Darwin':
            print("Show me charts disabled: not running on macOS")
            self.configs[BinanceWssConfig.SHOW_ME_CHARTS] = False

        self._dump_interval = 30
        self._statistics_interval = 10
        self._max_message_count = 5000

        self.auto_message_dumper()
        self.statistic_showcase()

        self.uuid = str(uuid.uuid4())
        self.message_seg_id = 0

        print('[BinanceWss] Initialized with UUID:', self.uuid)

    def unique_file_name(self, file_name, file_type):
        return '{}_{}-{}.{}'.format(file_name, self.uuid, self.message_seg_id, file_type)

    def rollover_message_segment(self):
        self.message_seg_id += 1
        self.msgs = []

    def init_websocket(self):
        self.semaphore = threading.Semaphore(0)
        self.run_ws_forever()
        self.semaphore.acquire()

    def _config_active(self, config_name: str) -> bool:
        return self.configs[config_name]

    @staticmethod
    def _craft_msg(symbol: str, auto_dump=True) -> dict | str:
        symbol = symbol.lower()
        msg = {
            "method": "SUBSCRIBE",
            "params": [
                "!miniTicker@arr@1000ms",
                symbol+"@aggTrade",
                symbol+"@depth@100ms",
                symbol+"@kline_1s"
            ],
            "id": 1
        }
        if auto_dump:
            return json.dumps(msg)
        return msg

    def _on_open(self, ws):
        print("WebSocket connection opened.")
        self.semaphore.release()

    def _on_message(self, ws, message):
        self.stats_stamps.append(time.time())
        msg = json.loads(message)
        self.msgs.append(msg)

        if len(self.msgs) > self._max_message_count:
            self.rollover_message_segment()

        message_type = msg.get('stream', None)
        if message_type is None:
            print('Malformed message received:', msg)
            return

        try:
            symbol, stream_type = message_type.split('@', 1)
        except ValueError:
            print('Malformed message received:', msg)
            return

        if stream_type == 'depth@100ms':
            msg = AbstractBinanceType(
                idx=BinanceTypes.DEPTH_STREAM,
                obj=DepthStreamMessage.from_dict(msg)
            )
        elif stream_type == 'aggTrade':
            msg = AbstractBinanceType(
                idx=BinanceTypes.AGG_TRADE,
                obj=AggTradeMessage.from_dict(msg)
            )
        elif stream_type == 'kline_1s':
            msg = AbstractBinanceType(
                idx=BinanceTypes.KLINE,
                obj=KlineMessage.from_dict(msg)
            )
        elif '!miniTicker' in stream_type:
            # Currently ignoring miniTicker messages
            return
        elif 'arr@1000ms' in stream_type:
            # Currently ignoring arr@1000ms messages
            return
        else:
            print('Unknown message {} received: {}'.format(stream_type, str(msg)[:100]+'...'))
            return

        callback = self.callbacks.get(symbol, None)
        if callback is None:
            throw_fuss(
                msg="No callback registered for symbol: {}".format(symbol),
                notify=False,
                boarder="<>"
            )
        callback(msg)

    def _on_error(self, ws, error):
        print("WebSocket error:", error)
        throw_fuss(
            msg="Binance WebSocket error occurred:\n{}".format(error),
            title="Binance WebSocket Error",
        )
        _ = self

    def _on_close(self, ws, close_status_code, close_msg):
        print("WebSocket connection closed:", close_status_code, close_msg)
        throw_fuss(
            msg="Binance WebSocket connection closed:\nCode: {}\nMessage: {}".format(close_status_code, close_msg),
            title="Binance WebSocket Closed",
        )
        _ = self

    def subscribe(self, symbol: str, callback):
        self.ws.send(self._craft_msg(symbol))
        self.callbacks[symbol.lower()] = callback

    @runAsThread
    def run_ws_forever(self):
        self.ws.run_forever()

    @runAsThread
    def auto_message_dumper(self):
        while True:
            time.sleep(self._dump_interval)
            if self._config_active(BinanceWssConfig.AUTO_DUMP):
                fname = self.unique_file_name('binance_wss_dump', 'json')
                try:
                    with open(fname, 'w') as f:
                        json.dump(self.msgs, f)
                    print('[AUTO-DUMP] Dumped {} messages to {}'.format(len(self.msgs), fname))
                except KeyboardInterrupt:
                    throw_fuss('WAIT A SECOND ATTEMPTING TO WRITE DUMP, AUTO-DUMP WILL STOP WHEN THIS IS COMPLETE', notify=False)
                    with open(fname, 'w') as f:
                        json.dump(self.msgs, f)
                    throw_fuss('DUMP COMPLETE, AUTO-DUMP STOPPED', notify=False)
                    break

    @runAsThread
    def statistic_showcase(self):
        while True:
            time.sleep(self._statistics_interval)
            if self._config_active(BinanceWssConfig.TOTAL_MESSAGE_STATISTICS):
                now = time.time()
                cutoff = now - self._statistics_interval
                count = len([stamp for stamp in self.stats_stamps if stamp >= cutoff])
                print('[STATISTICS] Received {} messages in the last {} seconds (avg: {:.2f} msgs/sec)'.format(
                    count,
                    self._statistics_interval,
                    count / self._statistics_interval
                ))
                # Clean up old stamps
                self.stats_stamps = [stamp for stamp in self.stats_stamps if stamp >= cutoff]



if __name__ == '__main__':

    def highest_bid_ask_price_callback(msg: AbstractBinanceType):
        from termcolor import colored
        if msg.idx == BinanceTypes.DEPTH_STREAM:
            depth: DepthStreamMessage = msg.obj
            update: DepthUpdate = depth.data
            asks = update.a
            bids = update.b
            try:
                top_ask = float(asks[0][0])
            except IndexError:
                top_ask = 0
            try:
                top_bid = float(bids[0][0])
            except IndexError:
                top_bid = 0
            print('[{}] Top Bid: {:.2f}, Top Ask: {:.2f}'.format(update.s, top_bid, top_ask))


    binance_wss = BinanceWss()
    binance_wss.subscribe('BTCUSDT', lambda msg: highest_bid_ask_price_callback(msg))
    binance_wss.subscribe('ETHUSDT', lambda msg: highest_bid_ask_price_callback(msg))
    input('Press Enter to exit...\n')