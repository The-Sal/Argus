import os
import time
from dotenv import load_dotenv
from argus.capital import (
    CapitalComAPI, Environment, WebsocketDataType,
    CapitalComAPIError, WebSocketStatus
)

load_dotenv()
API_KEY = os.environ['CAPITAL_DOTCOM_API_KEY']
IDENTIFIER = "56j2pynp9d@privaterelay.appleid.com"  # e.g., your email
PASSWORD = os.environ['CAPITAL_DOT_CUSTOM_PW']  # Your account password
ENVIRONMENT = Environment.DEMO  # Or Environment.LIVE


# --- WebSocket Callbacks (Example) ---
def market_data_handler(data):
    print(f"LIVE QUOTE ({data['epic']}): Bid={data.get('bid')} Offer={data.get('offer')}")


def ohlc_data_handler(data):
    print(f"LIVE OHLC ({data['epic']}/{data['resolution']}): C={data.get('c')}")


if __name__ == "__main__":
    try:
        # Use context manager for automatic login/logout
        print('API KEY:', API_KEY)
        with CapitalComAPI(API_KEY, IDENTIFIER, PASSWORD, ENVIRONMENT) as api:
            print(f"Successfully logged into {api.environment.value} environment.")
            print(f"Active Account ID: {api.active_account_id}")

            # Get account balance
            balance = api.get_balance()
            if balance is not None:
                print(f"Account Balance: {balance}")

            # # Get market details for EUR/USD
            eurusd_details = api.get_market_details(epic="EURUSD")
            if eurusd_details:
                print(f"EURUSD Min Trade Size: {eurusd_details['dealingRules']['minDealSize']['value']}")

            # Subscribe to EURUSD live quotes
            api.subscribe_to_epic_data("EURUSD", WebsocketDataType.MARKET, market_data_handler)

            # Subscribe to US500 (S&P 500 CFD example) 1-minute OHLC
            # Ensure the epic 'US500' is correct for your broker
            # api.subscribe_to_epic_data(
            #     "US500",  # Example epic
            #     WebsocketDataType.OHLC,
            #     ohlc_data_handler,
            #     resolution=HistoricalPriceResolution.MINUTE
            # )
            #
            # search for BTCUSD and ETHUSD markets
            # btc_market = api.search_markets("BTCUSD")
            # eth_market = api.search_markets("ETHUSD")


            print("Subscribed to WebSocket data. Waiting for updates (Ctrl+C to stop)...")
            # Keep the main thread alive to receive WebSocket messages
            # WebSocket runs in a daemon thread.
            try:
                while api.ws_status == WebSocketStatus.CONNECTED or api.ws_status == WebSocketStatus.CONNECTING:
                    time.sleep(5)  # Check status periodically
                    if not api._ws_subscriptions:  # Check if all subscriptions were removed elsewhere
                        print("No active subscriptions, main loop will exit.")
                        break
            except KeyboardInterrupt:
                print("\nInterrupted by user.")
            finally:
                print("Stopping all WebSocket subscriptions...")
                api.stop_all_websocket_subscriptions()
                print("WebSocket stopped.")

    except CapitalComAPIError as e:
        print(f"API Error: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        print("Program finished.")
