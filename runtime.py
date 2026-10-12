#! /usr/bin/env python3
"""
Argus runtime entrypoint.
- Supports selecting dispatcher: ib.forecast | ib.core | polymarket | capital.com | binance | hyperliquid | lighter
- Optional --host/--port are accepted and forwarded only to dispatchers that support them.
  Dispatchers have their own defaults; if not provided, nothing is passed.
- Supports: macOS, Linux (tested on Debian) does NOT support Windows.
- IB Dispatchers work on all platforms (Linux, macOS)
- Push Notifications requires macOS due to the use of osascript to notify on machine-local notifications
- Capital.com, Polymarket, Binance, TradingView (Chart+Quote), etc... work on all platforms.
- Use .env (or Environment Variables) to configure dispatcher behavior (see docs)
- Automatically loads .env file if present in a working directory.
"""
import os
import sys
import time
import argus
import logging
import platform
import argparse
import sysconfig
import subprocess
from argus import secure_load_dotenv, check_env_compatibility


choices = ['ib.forecast', 'ib.core', 'polymarket', 'capital.com', 'binance', 'hyperliquid', 'lighter']

def git_info():
    """
    Returns the git commit hash and branch name of the current Argus installation.
    :return:
    """
    proc = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        return None, None
    commit_hash = proc.stdout.decode('utf-8').strip()
    proc = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        return commit_hash, None
    branch_name = proc.stdout.decode('utf-8').strip()
    return commit_hash, branch_name


def interpreter_info():
    """
    Returns the Python interpreter version, implementation and if its free thread or not.
    :return:
    """
    version = platform.python_version()
    implementation = platform.python_implementation()
    supports_free_threading = (
            sysconfig.get_config_var("Py_GIL_DISABLED") == 1
    )
    gil_enabled = sys._is_gil_enabled()
    free_threading_active = gil_enabled is False
    return version, implementation, supports_free_threading, free_threading_active

def bootstrap():
    print('Checking Argus runtime...')

    version, implementation, supports_free_threading, free_threading_active = interpreter_info()

    print(f'Python version: {version}')
    print(f'Python implementation: {implementation}')
    print(f'Free threading supported: {supports_free_threading}')
    print(f'Free threading active: {free_threading_active}')
    
    if supports_free_threading:
        # check if the set PYTHON_GIL=0 if not that means when the module imports ckzg the GIL will be re-enabled
        # the code is already tested with GIL disabled extensively so you should force it to be disabled.
        # re-executes the script with GIL force disabled.
        if os.environ.get("PYTHON_GIL") is None:
            print(
                f"Warning: You are running Python {version} which supports free threading, you have no explicitly set PYTHON_GIL=0/1, "
                f"re-executing with PYTHON_GIL=0 to force GIL disabled for better performance.")
            print("Restarting in 3 seconds...")
            for _ in range(3, 0, -1):
                print(f"\r{_}...", end="")
                time.sleep(1)
            print("")
            os.environ["PYTHON_GIL"] = "0"
            os.execv(sys.executable, [sys.executable] + sys.argv)
    else:
        pass
    

    commit_hx, branch = git_info()
    if commit_hx and branch:
        print(f'Git commit: {commit_hx}, branch: {branch}')

    check_env_compatibility()

def main(argv=None):
    parser = argparse.ArgumentParser(description='Argus runtime dispatcher launcher')
    parser.add_argument('target', choices=choices, help='Dispatcher to run')
    parser.add_argument('--host', dest='host', help='Listening host (if supported by dispatcher)')
    parser.add_argument('--port', dest='port', type=int, help='Listening port (if supported by dispatcher)')
    parser.add_argument('--capital-env', dest='capital_env', choices=['demo', 'live'], help='Capital.com environment (demo or live)')
    parser.add_argument('--wait-for-pong', dest='wait_for_pong', action='store_true', help='Wait for pong before starting interactive mode (if supported by dispatcher)')
    parser.add_argument('--profile-proxy', dest='profile_proxy', choices=['none', 'proxy-only', 'proxy-and-local'], help='Profile WireProxy performance for Polymarket (none, proxy-only, or proxy-and-local)')
    parser.add_argument('--log-level', dest='log_level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], default='INFO', help='Set logging level (default: INFO)')
    parser.add_argument('-v', '--verbose', dest='verbose', action='store_true', help='Enable verbose logging (same as --log-level DEBUG)')

    args = parser.parse_args(argv)

    # Configure logging based on flags
    log_level = logging.DEBUG if args.verbose else getattr(logging, args.log_level)
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    if args.target is not None:
        if not secure_load_dotenv():
            print("Warning: .env was not loaded")
        bootstrap()

    if args.target == 'ib.forecast':
        from argus.ib.forecast import FXCDispatcher
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
        from argus.ib import MKTDispatcher, IBKRModes
        ib_kwargs = {}
        if args.host:
            ib_kwargs['host'] = args.host
        if args.port is not None:
            ib_kwargs['port'] = args.port
        dispatcher = MKTDispatcher(mode=IBKRModes.PROTOCOL_2, **ib_kwargs)
        dispatcher.select_account_interactive()
        dispatcher.ws.interactive_mode()
    elif args.target == 'polymarket':
        from argus.polymarket import PolymarketDispatcher
        polymarket_kwargs = {}
        if args.host:
            polymarket_kwargs['host'] = args.host
        if args.port is not None:
            polymarket_kwargs['port'] = args.port
        if args.profile_proxy:
            profile_proxy_map = {'none': -1, 'proxy-only': 0, 'proxy-and-local': 1}
            polymarket_kwargs['profile_proxy'] = profile_proxy_map[args.profile_proxy]
        dispatcher = PolymarketDispatcher(**polymarket_kwargs)
        # dispatcher supports wait till pong
        if args.wait_for_pong:
            print('[Runtime] Waiting for first pong from Polymarket...')
            dispatcher.account_updates.wait_till_first_pong.wait()
            print('[Runtime] Received first pong, starting interactive mode.')
        dispatcher.run()
        dispatcher.interactive_mode()
        print("Exiting Polymarket dispatcher")
    elif args.target == 'capital.com':
        from argus.capital import MKTDispatcher as CapitalComDispatcher, Environment
        print('Warning: capital.com uses Unix domain socket, --host/--port are ignored')
        if args.capital_env == 'demo':
            env = Environment.DEMO
        else:
            env = Environment.LIVE
        print(f'Using Capital.com environment: {env}')
        dispatcher = CapitalComDispatcher(environment=env)
        dispatcher.start_server()
        input('Press enter to exit.')
        dispatcher.api.logout()
    elif args.target == 'binance':
        from argus.binance import BinanceMKTDispatcher
        binance_kwargs = {}
        if args.host:
            binance_kwargs['host'] = args.host
        if args.port is not None:
            binance_kwargs['port'] = args.port
        dispatcher = BinanceMKTDispatcher(**binance_kwargs)
        dispatcher.interactive_mode()
        print("Exiting Binance dispatcher")
    elif args.target == "hyperliquid":
        from argus.perpetuals.hyper import HyperLiquidDispatcher
        hyper_kwargs = {}
        if args.host:
            hyper_kwargs['host'] = args.host
        if args.port is not None:
            hyper_kwargs['port'] = args.port
        dispatcher = HyperLiquidDispatcher(**hyper_kwargs)
        dispatcher.run()
        dispatcher.interactive_mode()
        print("Exiting HyperLiquid dispatcher")
    elif args.target == "lighter":
        from argus.perpetuals.lighter import LighterDispatcher
        lighter_kwargs = {}
        if args.host:
            lighter_kwargs['host'] = args.host
        if args.port is not None:
            lighter_kwargs['port'] = args.port
        dispatcher = LighterDispatcher(**lighter_kwargs)
        dispatcher.run()
        dispatcher.interactive_mode()
        print("Exiting Lighter dispatcher")
    else:
        parser.error('Unknown target')

if __name__ == '__main__':
    platform_running = platform.system()
    print('Argus:', argus)
    print('Running on', platform_running)
    print('Arguments:', sys.argv)
    main(sys.argv[1:])
