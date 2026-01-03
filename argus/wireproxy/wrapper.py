"""

Supported Dispatchers:
    – Polymarket, IDX=POLYMARKET

"""

import os
import time
import logging
from dotenv import load_dotenv
from websocket import WebSocketApp
from argus.wireproxy.__main__ import send_server_command, ensure_daemon_running

if not load_dotenv():
    print('WARNING: .env was not loaded, wireproxy REQUIRED env vars may be missing!')

BIND_ADDRESS = os.environ.get('WIREPROXY_BIND_ADDRESS', '127.0.0.1:25344')

def load_all_proxy_mappings():
    """
    Load all the Dispatcher ==> WireProxy server mappings from environment variables.
    Each mapping is defined like so `WIREPROXY_MAPPING_<DISPATCHER_NAME>=<CONFIG_NAME>`.
    Where CONFIG_NAME is what's passed to WireProxyServer to spin up.
    :return:
    """

    environ_vars = os.environ
    mappings = {}
    for var_name, config_name in environ_vars.items():
        if var_name.startswith('WIREPROXY_MAPPING_'):
            dispatcher_name = var_name[len('WIREPROXY_MAPPING_'):]
            mappings[dispatcher_name] = config_name
    return mappings


def setup_proxy_for_dispatcher(idx):
    mappings = load_all_proxy_mappings()
    if str(idx) in mappings:
        print(__name__, f'Found WireProxy mapping for dispatcher {idx}, ensuring WireProxy daemon is running...')
        ensure_daemon_running()
        config_name = mappings[str(idx)].split('.conf')[0]
        print(__name__, f'Sending command to WireProxy daemon to start proxy with config name: {config_name}')
        state = send_server_command('state')['result']
        already_running = False
        if state['running']:
            active_config = state['config'].split('.conf')[0]
            if active_config != config_name:
                msg = ("WireProxy is already running with an active configuration, this will be torn down and replaced"
                       "with the new configuration requested. If there are active connections using the existing"
                       "configuration, they wil be dropped.")
                logging.warning(msg)
                logging.warning("State=%s", state)
                response = send_server_command('spin_down')
                print(__name__, f'WireProxy daemon response to spin_down: {response}')
                time.sleep(2)
            else:
                already_running = True
                print(__name__, f'WireProxy daemon is already running with the requested configuration: {config_name}')
                print(__name__, f'Daemon state: {state}')

        if not already_running:
            print(__name__, f'Spinning up WireProxy daemon with configuration: {config_name}')
            response = send_server_command('spin_up', config_name)
            print(__name__, f'WireProxy daemon response: {response}')

        return True
    return False


def start_proxy_aware_ws(idx, websocket: WebSocketApp, *args, **kwargs):
    if setup_proxy_for_dispatcher(idx):
        print(__name__, f'Starting proxy-aware WebSocketApp for dispatcher {idx} via WireProxy at {BIND_ADDRESS}')
        websocket.run_forever(
            proxy_type="socks5",
            http_proxy_host=BIND_ADDRESS.split(':')[0],
            http_proxy_port=int(BIND_ADDRESS.split(':')[1]),
            *args,
            **kwargs
        )
    else:
        print(__name__, f'No WireProxy mapping found for dispatcher {idx}, starting normal WebSocketApp')
        websocket.run_forever(*args, **kwargs)

def update_request_session_proxy(idx, session):
    setup_proxy_for_dispatcher(idx)
    session.proxies.update({
        'http': f'socks5h://{BIND_ADDRESS}',
        'https': f'socks5h://{BIND_ADDRESS}',
    })


if __name__ == '__main__':
    print(load_all_proxy_mappings())
    start_proxy_aware_ws('POLYMARKET', WebSocketApp('wss://ws.polymarket.com/'))