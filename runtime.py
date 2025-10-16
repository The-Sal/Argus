"""
Argus runtime entrypoint.
- Rewritten to use argparse.
- Supports selecting dispatcher: ib.forecast | ib.core | polymarket
- Optional --host/--port are accepted and forwarded only to dispatchers that support them.
  Dispatchers have their own defaults; if not provided, nothing is passed.
"""
import os
import sys
import argus
import platform
import argparse
from dotenv import load_dotenv
from argus.polymarket import PolyDispatcher
from argus.ib.forecast import FXCDispatcher
from argus.ib import MKTDispatcher, IBKRModes


if not load_dotenv():
    print("Warning: .env was not loaded")

choices = ['ib.forecast', 'ib.core', 'polymarket']

def main(argv=None):
    parser = argparse.ArgumentParser(description='Argus runtime dispatcher launcher')
    parser.add_argument('target', choices=choices, help='Dispatcher to run')
    parser.add_argument('--host', dest='host', help='Listening host (if supported by dispatcher)')
    parser.add_argument('--port', dest='port', type=int, help='Listening port (if supported by dispatcher)')

    args = parser.parse_args(argv)

    if args.target == 'ib.forecast':
        ib_kwargs = {}
        if args.host:
            ib_kwargs['host'] = args.host
        if args.port is not None:
            ib_kwargs['port'] = args.port
        dispatcher = FXCDispatcher(**ib_kwargs)
        dispatcher.select_account_interactive()
        dispatcher.interactive_mode()
        print("Exiting")
    elif args.target == 'ib.core':
        ib_kwargs = {}
        if args.host:
            ib_kwargs['host'] = args.host
        if args.port is not None:
            ib_kwargs['port'] = args.port
        dispatcher = MKTDispatcher(mode=IBKRModes.PROTOCOL_2, **ib_kwargs)
        dispatcher.select_account_interactive()
        dispatcher.ws.interactive_mode()
    elif args.target == 'polymarket':
        poly_kwargs = dict(
            private_key=os.environ['POLYMARKET_PRIVATE_KEY'],
            proxy_funder=os.environ['POLYMARKET_PROXY_FUNDER']
        )
        # Only forward host/port if explicitly provided by user, mapping to PolyDispatcher's kwargs
        if args.host:
            poly_kwargs['listen_host'] = args.host
        if args.port is not None:
            poly_kwargs['listen_port'] = args.port
        dispatcher = PolyDispatcher(**poly_kwargs)
        dispatcher.interactive_mode()
    else:
        parser.error('Unknown target')

if __name__ == '__main__':
    platform_running = platform.system()
    print('Argus:', argus)
    print('Running on', platform_running)
    print('Arguments:', sys.argv)
    main(sys.argv[1:])
