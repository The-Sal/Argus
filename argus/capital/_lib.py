import json
import time
import math
import logging
import requests
import threading
import websocket 
from enum import Enum
from typing import List, Dict, Optional, Callable, Any

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s - %(funcName)s - %(message)s'
)
logger = logging.getLogger(__name__)

class CapitalComAPIError(Exception):
    """Custom exception for API errors."""
    def __init__(self, message: str, status_code: Optional[int] = None, response_data: Optional[Any] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data

    def __str__(self) -> str:
        return f"{super().__str__()} (Status Code: {self.status_code}, Response: {self.response_data})"

class Environment(Enum):
    DEMO = "demo"
    LIVE = "live"

class TradeDirection(Enum):
    BUY = "BUY"
    SELL = "SELL"

class HistoricalPriceResolution(Enum):
    MINUTE = "MINUTE"
    MINUTE_5 = "MINUTE_5"
    MINUTE_10 = "MINUTE_10"
    MINUTE_15 = "MINUTE_15"
    MINUTE_30 = "MINUTE_30"
    HOUR = "HOUR"
    HOUR_2 = "HOUR_2" 
    HOUR_3 = "HOUR_3" 
    HOUR_4 = "HOUR_4"
    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"

class WebSocketStatus(Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    STOPPING = "STOPPING"

class WebsocketDataType(Enum):
    MARKET = "MARKET"  
    OHLC = "OHLC"      

class OhlcBarType(Enum):
    CLASSIC = "classic"
    HEIKIN_ASHI = "heikin-ashi"

class CapitalComAPI:
    BASE_URLS = {
        Environment.DEMO: "https://demo-api-capital.backend-capital.com/api/v1",
        Environment.LIVE: "https://api-capital.backend-capital.com/api/v1",
    }
    WS_URLS = {
        Environment.DEMO: "wss://api-streaming-capital.backend-capital.com/connect",
        Environment.LIVE: "wss://api-streaming-capital.backend-capital.com/connect",
    }

    APP_PING_INTERVAL_SECONDS = 9 * 60 

    def __init__(self, api_key: str, identifier: str, password: str, environment: Environment = Environment.DEMO):
        self.api_key = api_key
        self.identifier = identifier
        self.password = password
        self.environment = environment
        self.base_url = self.BASE_URLS[environment]
        self.ws_base_url = self.WS_URLS[environment]

        self.session = requests.Session()
        self.session.headers.update({"X-CAP-API-KEY": self.api_key, "Content-Type": "application/json"})

        self.cst: Optional[str] = None
        self.x_security_token: Optional[str] = None
        self.active_account_id: Optional[str] = None

        self.ws_thread: Optional[threading.Thread] = None
        self.ws_connection: Optional[websocket.WebSocketApp] = None
        self._ws_subscriptions: Dict[str, Dict[str, Any]] = {} 
        self._ws_stop_event = threading.Event()
        self._ws_lock = threading.Lock() 
        self._ws_reconnect_attempts = 0
        self._ws_max_reconnect_attempts = 10
        self._ws_initial_reconnect_delay = 5
        self._ws_max_reconnect_delay = 60
        self._ws_status = WebSocketStatus.DISCONNECTED

        self._ws_ping_thread: Optional[threading.Thread] = None
        self._ws_ping_stop_event = threading.Event()

        logger.info(f"CapitalComAPI initialized for {environment.value} environment.")

    @property
    def ws_status(self) -> WebSocketStatus:
        """Current status of the WebSocket connection."""
        return self._ws_status

    def _update_auth_tokens(self, response_headers):
        """Helper to update auth tokens if present in response headers."""
        new_cst = response_headers.get("CST")
        new_xst = response_headers.get("X-SECURITY-TOKEN")
        updated = False
        if new_cst and new_cst != self.cst:
            self.cst = new_cst
            logger.info("CST token updated.")
            updated = True
        if new_xst and new_xst != self.x_security_token:
            self.x_security_token = new_xst
            logger.info("X-SECURITY-TOKEN updated.")
            updated = True
        if updated:
            logger.debug(f"Current CST: {'******' if self.cst else None}, Current XST: {'******' if self.x_security_token else None}")

    def _request(self, method: str, endpoint: str, params: Optional[Dict[str, Any]] = None,
                 data: Optional[Dict[str, Any]] = None, add_auth_headers: bool = True,
                 is_login_retry: bool = False) -> Optional[Any]:
        url = f"{self.base_url}/{endpoint}"
        current_headers = self.session.headers.copy()

        if add_auth_headers:
            if not self.cst or not self.x_security_token:
                if is_login_retry:
                    raise CapitalComAPIError("Recursive login attempt failed. CST or X-SECURITY-TOKEN still missing.")
                logger.warning(f"Auth tokens missing for request to {endpoint}. Attempting re-login.")
                if not self.login():
                     raise CapitalComAPIError(f"Re-login failed. Cannot proceed with the request to {endpoint}.")
            current_headers["CST"] = self.cst
            current_headers["X-SECURITY-TOKEN"] = self.x_security_token

        try:
            response = self.session.request(method, url, params=params, json=data, headers=current_headers)
            logger.debug(f"Request: {method} {url} | Params: {params} | Data: {data}")
            response_body_for_log = response.text[:500] + ('...' if len(response.text) > 500 else '')
            logger.debug(f"Response: {response.status_code} | Headers: {response.headers} | Body (truncated): {response_body_for_log}")

            if add_auth_headers or endpoint == "session": 
                self._update_auth_tokens(response.headers)

            if response.status_code == 401 and add_auth_headers and not is_login_retry:
                logger.warning(f"Received 401 Unauthorized for {endpoint}. Session may have expired. Re-logging in and retrying request.")
                if self.login(): 
                    current_headers["CST"] = self.cst
                    current_headers["X-SECURITY-TOKEN"] = self.x_security_token
                    response = self.session.request(method, url, params=params, json=data, headers=current_headers)
                    logger.debug(f"Retry Request to {endpoint} after re-login: {method} {url}")
                    logger.debug(f"Retry Response Status: {response.status_code}")
                    self._update_auth_tokens(response.headers) 
                else:
                    raise CapitalComAPIError(f"Re-login failed during request retry for {endpoint}.", response.status_code, response.text)

            response.raise_for_status()

            if endpoint == "ping" and response.status_code == 200: 
                return response.text 

            if response.status_code == 204:
                return None
            if response.content:
                try:
                    return response.json()
                except json.JSONDecodeError:
                    logger.error(f"Failed to decode JSON from response for {endpoint}: {response.text}")
                    raise CapitalComAPIError("Failed to decode JSON response", response.status_code, response.text)
            return None 

        except requests.exceptions.HTTPError as e:
            error_data = None
            if e.response is not None and e.response.content:
                try:
                    error_data = e.response.json()
                except json.JSONDecodeError:
                    error_data = e.response.text
            else:
                error_data = "No response body"
            logger.error(f"HTTP Error {e.response.status_code if e.response else 'N/A'} on {method} {url}. Response: {error_data}")
            raise CapitalComAPIError(f"API request failed: {e}", e.response.status_code if e.response else None, error_data) from e
        except requests.exceptions.RequestException as e:
            logger.error(f"Request Exception for {method} {url}: {e}")
            raise CapitalComAPIError(f"Network or request error: {e}") from e

    def login(self) -> bool:
        logger.info("Attempting to login...")
        payload = {"identifier": self.identifier, "password": self.password, "encryptedPassword": False}

        login_headers = {"X-CAP-API-KEY": self.api_key, "Content-Type": "application/json"}
        try:
            response = self.session.post(f"{self.base_url}/session", json=payload, headers=login_headers)
            logger.debug(f"Login Response Headers: {response.headers}")
            response_body_for_log = response.text[:500] + ('...' if len(response.text) > 500 else '')
            logger.debug(f"Login Response Body (truncated): {response_body_for_log}")

            self._update_auth_tokens(response.headers) 
            response.raise_for_status() 

            if not self.cst or not self.x_security_token:
                logger.error("Login failed: CST or X-SECURITY-TOKEN not found in response headers despite successful status code.")
                return False

            logger.info("Login successful.")
            data = response.json()
            if data and data.get("currentAccountId"):
                self.active_account_id = data["currentAccountId"]
                logger.info(f"Active account ID set from login response: {self.active_account_id}")
            else:
                logger.warning("currentAccountId not found in login response. Active account may need to be set manually or fetched.")
            return True
        except requests.exceptions.HTTPError as e:
            error_body = e.response.text if e.response is not None else "No response body"
            try:
                if e.response is not None and e.response.content: error_body = e.response.json()
            except json.JSONDecodeError: pass 
            logger.error(f"Login HTTP Error: {e.response.status_code if e.response else 'N/A'}. Response: {error_body}")
            self.cst = None
            self.x_security_token = None
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"Login Request Exception: {e}")
            self.cst = None
            self.x_security_token = None
            return False
        except json.JSONDecodeError:
            logger.error(f"Failed to decode JSON from login response: {response.text}")
            self.cst = None
            self.x_security_token = None
            return False

    def logout(self) -> bool:
        if not self.cst or not self.x_security_token:
            logger.info("Not logged in (no active session tokens), no server logout needed.")

            if self.ws_thread and self.ws_thread.is_alive():
                logger.info("Stopping WebSocket connections as part of local session clearing.")
                self.stop_all_websocket_subscriptions()
            self.cst = None
            self.x_security_token = None
            self.active_account_id = None
            return True

        logger.info("Logging out from server...")
        try:
            self._request("DELETE", "session") 
            logger.info("Logout successful on server.")
        except CapitalComAPIError as e:
            logger.error(f"Logout request to server failed: {e}. Clearing local session tokens anyway.")
        finally:

            if self.ws_thread and self.ws_thread.is_alive():
                logger.info("Stopping WebSocket connections as part of logout.")
                self.stop_all_websocket_subscriptions() 
            self.cst = None
            self.x_security_token = None
            self.active_account_id = None
            logger.info("Local session tokens and active account ID cleared.")
        return True

    def ping_server(self) -> bool:
        """Pings the server to check general connectivity. This does not keep the trading session alive."""
        logger.info("Pinging server for connectivity check...")
        try:

            response_text = self._request("GET", "ping", add_auth_headers=False)
            if response_text == "pong":
                logger.info("Server ping successful (pong).")
                return True
            logger.warning(f"Server ping returned unexpected response: '{response_text}'")
            return False
        except CapitalComAPIError as e:
            logger.error(f"Server ping failed: {e}")
            return False

    def get_accounts(self) -> List[Dict[str, Any]]:
        logger.info("Fetching accounts...")
        response_data = self._request("GET", "accounts")
        return response_data.get("accounts", []) if response_data else []

    def get_active_account_details(self) -> Optional[Dict[str, Any]]:
        if not self.active_account_id:
            logger.info("No active account ID set. Attempting to fetch accounts to find a default.")
            accounts_list = self.get_accounts()
            if accounts_list and accounts_list[0].get("accountId"):
                self.active_account_id = accounts_list[0]["accountId"]
                logger.info(f"Defaulting to first account found: {self.active_account_id}")
                return accounts_list[0]
            else:
                logger.error("No active account ID set and no accounts found to default to.")
                return None

        logger.info(f"Fetching details for active account: {self.active_account_id}")
        all_accounts_data = self._request("GET", "accounts") 
        if all_accounts_data and "accounts" in all_accounts_data:
            for acc in all_accounts_data["accounts"]:
                if acc.get("accountId") == self.active_account_id:
                    return acc
        logger.error(f"Could not find details for active account ID: {self.active_account_id} in the fetched list.")
        return None

    def get_balance(self) -> Optional[float]:
        details = self.get_active_account_details()
        if not details:
            logger.error("Cannot get balance: active account details not available.")
            return None

        balance_info = details.get("balance", {})
        balance_value = balance_info.get("balance") 
        if balance_value is None:
            balance_value = balance_info.get("available") 
            if balance_value is not None:
                logger.debug("Using 'available' balance as 'balance.balance' field was not found.")

        if balance_value is not None:
            try:
                return float(balance_value)
            except ValueError:
                logger.error(f"Could not convert balance value '{balance_value}' to float.")
                return None

        logger.error("Could not retrieve a valid balance value ('balance' or 'available') from account details.")
        return None

    def switch_account(self, account_id: str) -> bool:
        logger.info(f"Attempting to switch active account to: {account_id}")
        try:

            response_data = self._request("PUT", "session", data={"accountId": account_id})

            session_details = self._request("GET", "session")
            if session_details and session_details.get("accountId") == account_id:
                self.active_account_id = account_id 
                logger.info(f"Successfully switched to account: {account_id} and confirmed via GET /session.")

                if self._ws_subscriptions: 
                    logger.warning("Account switched. WebSocket subscriptions may be invalid. Stopping all current subscriptions. Please re-subscribe if needed.")
                    self.stop_all_websocket_subscriptions()
                return True
            else:
                logger.error(f"Failed to confirm account switch to {account_id} via GET /session. Current session account: {session_details.get('accountId') if session_details else 'N/A'}")

                return False

        except CapitalComAPIError as e:
            logger.error(f"Error during account switch to {account_id}: {e}")
            return False

    def get_open_positions(self) -> List[Dict[str, Any]]:
        logger.info("Fetching open positions...")
        data = self._request("GET", "positions")
        return data.get("positions", []) if data else []

    def get_working_orders(self) -> List[Dict[str, Any]]:
        logger.info("Fetching working orders...")
        data = self._request("GET", "workingorders")
        return data.get("workingOrders", []) if data else []

    def get_transaction_history(self, transaction_type: Optional[str] = None, from_date: Optional[str] = None,
                                to_date: Optional[str] = None, detailed: bool = False,
                                last_period_seconds: Optional[int] = None) -> List[Dict[str, Any]]:

        logger.info(f"Fetching transaction history (Type: {transaction_type or 'Any'}, Detailed: {detailed})...")
        params: Dict[str, Any] = {}
        if transaction_type: params['type'] = transaction_type
        if from_date: params['from'] = from_date 
        if to_date: params['to'] = to_date       
        if last_period_seconds is not None: params['lastPeriod'] = last_period_seconds
        if detailed: params['detailed'] = detailed 

        data = self._request("GET", "history/transactions", params=params)
        return data.get("transactions", []) if data else []

    def get_closed_trades(self, from_date: Optional[str] = None, to_date: Optional[str] = None, last_period_seconds: Optional[int] = None) -> List[Dict[str, Any]]:
        logger.info("Fetching closed trades (transaction type: TRADE, detailed implied)...")

        return self.get_transaction_history(transaction_type="TRADE", from_date=from_date, to_date=to_date,
                                            detailed=True, last_period_seconds=last_period_seconds)

    def open_trade(self, epic: str, direction: TradeDirection, size: float,
                     guaranteed_stop: bool = False, stop_level: Optional[float] = None,
                     stop_distance: Optional[float] = None, profit_level: Optional[float] = None,
                     profit_distance: Optional[float] = None, trailing_stop: bool = False,
                     trailing_stop_distance: Optional[float] = None,
                     force_open: bool = True) -> Optional[Dict[str, Any]]:
        logger.info(f"Opening {direction.value} trade for {epic}, size {size}")
        payload: Dict[str, Any] = {
            "epic": epic,
            "direction": direction.value,
            "size": str(size), 
            "guaranteedStop": guaranteed_stop,
            "trailingStop": trailing_stop,

            "forceOpen": force_open,
        }
        if stop_level is not None: payload["stopLevel"] = str(stop_level)
        if stop_distance is not None: payload["stopDistance"] = str(stop_distance)
        if profit_level is not None: payload["profitLevel"] = str(profit_level) 
        if profit_distance is not None: payload["profitDistance"] = str(profit_distance) 
        if trailing_stop and trailing_stop_distance is not None:
            payload["trailingStopDistance"] = str(trailing_stop_distance)

        return self._request("POST", "positions", data=payload)

    def close_trade(self, deal_id: str, direction: Optional[TradeDirection] = None,
                      size: Optional[float] = None, order_type: str = "MARKET",
                      level: Optional[float] = None,
                      time_in_force: str = "GOOD_TILL_CANCELLED") -> Optional[Dict[str, Any]]:
        """
        Closes an OTC trade.
        Note: This method uses the DELETE /positions (plural) endpoint with a request body.
        The standard API documentation typically shows DELETE /positions/{dealId} (singular)
        without a body for closing a specific position by its ID. This implementation
        may target a specific OTC or bulk closing mechanism.
        """
        logger.info(f"Attempting to close trade {deal_id} (Size: {size if size else 'Full'}, Direction: {direction.value if direction else 'N/A'})")
        payload: Dict[str, Any] = {"dealId": deal_id} 
        if direction: payload["direction"] = direction.value
        if size is not None: payload["size"] = str(size)

        if order_type.upper() != "MARKET":
            payload["orderType"] = order_type.upper()
            if level is None:
                raise ValueError("Level must be specified for non-market order types for closing.")
            payload["level"] = str(level)
            payload["timeInForce"] = time_in_force 

        return self._request("DELETE", "positions", data=payload)

    def update_trade(self, deal_id: str, stop_level: Optional[float] = None,
                       profit_level: Optional[float] = None, 
                       trailing_stop: Optional[bool] = None,
                       trailing_stop_distance: Optional[float] = None,
                       guaranteed_stop: Optional[bool] = None) -> Optional[Dict[str, Any]]:
        logger.info(f"Updating trade {deal_id}: SL={stop_level}, TP={profit_level}, TS={trailing_stop}, TSDist={trailing_stop_distance}, GS={guaranteed_stop}")
        payload: Dict[str, Any] = {}
        if stop_level is not None: payload["stopLevel"] = str(stop_level)
        if profit_level is not None: payload["profitLevel"] = str(profit_level) 
        if trailing_stop is not None: payload["trailingStop"] = trailing_stop
        if trailing_stop_distance is not None:
            payload["trailingStopDistance"] = str(trailing_stop_distance)
        if guaranteed_stop is not None:
             payload["guaranteedStop"] = guaranteed_stop

        if not payload:
            logger.warning("Update_trade called with no parameters to update. No action taken.")
            return None
        return self._request("PUT", f"positions/{deal_id}", data=payload)

    def get_historical_prices(self, epic: str, resolution: HistoricalPriceResolution,
                                num_points: Optional[int] = None, start_date: Optional[str] = None,
                                end_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
        logger.info(f"Fetching historical prices for {epic}, resolution {resolution.value}")
        params: Dict[str, Any] = {"resolution": resolution.value}
        if num_points is not None: 
            params["max"] = num_points

        if start_date: params["from"] = start_date
        if end_date: params["to"] = end_date

        if num_points is None and not (start_date and end_date): 
            if start_date and not end_date: 
                 params["max"] = 100 
            elif end_date and not start_date: 
                 params["max"] = 100 
            elif not start_date and not end_date: 
                 params["max"] = 100 
                 logger.debug("No date range or num_points specified for historical_prices, defaulting to max=100.")

        return self._request("GET", f"prices/{epic}", params=params)

    def get_market_details(self, epic: Optional[str] = None, epics: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        if epic and epics:
            raise ValueError("Provide either a single 'epic' or a list of 'epics', not both.")

        if epic:
            logger.info(f"Fetching market details for epic: {epic}")
            return self._request("GET", f"markets/{epic}")
        elif epics:
            if not epics: 
                logger.warning("get_market_details called with empty 'epics' list. Fetching all markets instead.")
                return self._request("GET", "markets") 
            logger.info(f"Fetching market details for epics: {','.join(epics)}")
            return self._request("GET", "markets", params={"epics": ",".join(epics)})
        else: 
            logger.info("Fetching all navigable market details...")
            return self._request("GET", "markets")

    def search_market_for_security(self, search_term: str) -> List[Dict[str, Any]]:
        """
        Searches for markets based on a search term (e.g., security name, symbol).
        Returns a list of matching market details.
        """
        logger.info(f"Searching markets for term '{search_term}'")
        params = {"searchTerm": search_term}
        response_data = self._request("GET", "markets/", params=params)
        return response_data.get("markets", []) if response_data else []



    def calculate_trade_size_for_amount(self, epic: str,
                                        investment_amount_in_quote_currency: float,
                                        direction: TradeDirection) -> float:
        """
        Calculates trade size based on desired investment amount (as margin) using account-level leverage.
        """
        logger.info(f"Calculating trade size for {epic} using {investment_amount_in_quote_currency} as margin (account leverage, {direction.value})")
        if investment_amount_in_quote_currency <= 0:
            logger.warning(f"Investment amount (margin) {investment_amount_in_quote_currency} must be positive. Returning size 0.0.")
            return 0.0

        market_data = self.get_market_details(epic=epic)
        if not market_data:
            raise CapitalComAPIError(f"Could not fetch market details for epic {epic} to calculate size.")

        snapshot = market_data.get("snapshot")
        instrument_details = market_data.get("instrument") 
        dealing_rules = market_data.get("dealingRules")

        if not snapshot or not instrument_details or not dealing_rules:
            raise CapitalComAPIError(f"Market details for {epic} are incomplete (missing snapshot, instrument, or dealingRules). Cannot calculate size.")

        price = snapshot.get("offer") if direction == TradeDirection.BUY else snapshot.get("bid")
        if price is None or price <= 0:
            raise ValueError(f"Invalid or zero price ({price}) for {epic} ({direction.value}). Cannot calculate size.")

        instrument_type = instrument_details.get("type")
        if not instrument_type:
            raise ValueError(f"Instrument type not found for epic {epic}. Cannot determine account leverage.")

        account_prefs = self.get_account_preferences() 
        if not account_prefs or "leverages" not in account_prefs:
            raise CapitalComAPIError("Could not fetch account preferences or leverage information.")

        leverage_val = None

        leverages_data = account_prefs.get("leverages", {}) 
        instrument_type_leverage_info = leverages_data.get(instrument_type)

        if instrument_type_leverage_info and isinstance(instrument_type_leverage_info, dict):
            leverage_val = instrument_type_leverage_info.get("current") 
        elif isinstance(account_prefs.get("leverages"), list): 
             for lev_info in account_prefs.get("leverages", []):
                if lev_info.get("instrumentType") == instrument_type:
                    leverage_val = lev_info.get("leverage") 
                    break

        if leverage_val is None or not isinstance(leverage_val, (int, float)) or leverage_val <= 0:
            raise ValueError(f"Leverage for instrument type {instrument_type} not found or invalid ({leverage_val}) in account preferences.")
        logger.debug(f"Using account leverage {leverage_val}:1 for instrument type {instrument_type} (epic {epic}).")

        raw_size = (investment_amount_in_quote_currency*1.22 * leverage_val) / price
        logger.debug(f"Raw calculated size: {raw_size} (Margin: {investment_amount_in_quote_currency}, Price: {price}, Account Leverage: {leverage_val})")

        min_deal_size_str = dealing_rules.get("minDealSize", {}).get("value")
        deal_size_step_str = dealing_rules.get("dealSize", {}).get("step") 

        if min_deal_size_str is None or deal_size_step_str is None:

            deal_size_step_str = dealing_rules.get("minSizeIncrement", {}).get("value")
            if deal_size_step_str is None:
                raise ValueError(f"Missing dealing rules (minDealSize.value or dealSize.step/minSizeIncrement.value) for {epic}.")

        try:
            min_size_allowed = float(min_deal_size_str)
            size_step = float(deal_size_step_str)
        except ValueError:
            raise ValueError("Could not convert minDealSize or dealSize.step/minSizeIncrement to float.")

        if size_step <= 0:
            raise ValueError("Deal size step must be greater than zero.")

        if raw_size < min_size_allowed : 
            stepped_size = 0.0
        else:
            num_steps = math.floor(raw_size / size_step)
            stepped_size = num_steps * size_step

        logger.debug(f"Size after rounding to step {size_step}: {stepped_size}")

        if stepped_size < min_size_allowed:
            logger.warning(f"Calculated size {stepped_size} for {epic} is less than minimum allowed {min_size_allowed}. "
                           f"Investment amount {investment_amount_in_quote_currency} may be too small.")
            return 0.0

        logger.info(f"Calculated trade size for {epic} ({direction.value}) with margin {investment_amount_in_quote_currency} (account leverage): {stepped_size}")
        return stepped_size

    def calculate_trade_size_for_margin(self, epic: str,
                                        margin_amount_in_quote_currency: float,
                                        direction: TradeDirection) -> float:
        """
        Calculates trade size based on a specified margin amount, using the instrument's specific marginFactor.
        The marginFactor from the API (e.g., 10 for 10%) is converted to a decimal (0.10) for calculation.
        """
        logger.info(f"Calculating trade size for {epic} using margin {margin_amount_in_quote_currency} and instrument's marginFactor ({direction.value})")

        if margin_amount_in_quote_currency <= 0:
            logger.warning(f"Margin amount {margin_amount_in_quote_currency} must be positive. Returning size 0.0.")
            return 0.0

        market_data = self.get_market_details(epic=epic)
        if not market_data:
            raise CapitalComAPIError(f"Could not fetch market details for epic {epic} to calculate size.")

        snapshot = market_data.get("snapshot")
        instrument_details = market_data.get("instrument")
        dealing_rules = market_data.get("dealingRules")

        if not snapshot or not instrument_details or not dealing_rules:
            raise CapitalComAPIError(f"Market details for {epic} are incomplete. Cannot calculate size.")

        current_price = snapshot.get("offer") if direction == TradeDirection.BUY else snapshot.get("bid")
        if current_price is None or current_price <= 0:
            raise ValueError(f"Invalid or zero price ({current_price}) for {epic} ({direction.value}). Cannot calculate size.")

        margin_factor_percentage = instrument_details.get("marginFactor") 
        margin_factor_unit = instrument_details.get("marginFactorUnit", "PERCENTAGE").upper()

        if margin_factor_percentage is None or not isinstance(margin_factor_percentage, (int, float)) or margin_factor_percentage <= 0:
            raise ValueError(f"Invalid or missing marginFactor ({margin_factor_percentage}) for {epic}. Must be a positive number.")

        if margin_factor_unit == "PERCENTAGE":
            margin_factor_decimal = margin_factor_percentage / 100.0
        elif margin_factor_unit == "ABSOLUTE": 
             margin_factor_decimal = margin_factor_percentage
        else: 
            logger.warning(f"Unknown marginFactorUnit '{margin_factor_unit}' for epic {epic}. Assuming marginFactor is percentage.")
            margin_factor_decimal = margin_factor_percentage / 100.0

        if margin_factor_decimal <= 0: 
            raise ValueError(f"Resulting margin factor decimal ({margin_factor_decimal}) is invalid.")

        notional_value_in_quote_currency = margin_amount_in_quote_currency / margin_factor_decimal
        logger.debug(f"Calculated notional value: {notional_value_in_quote_currency} (Margin: {margin_amount_in_quote_currency}, MarginFactor (decimal): {margin_factor_decimal})")

        raw_size = notional_value_in_quote_currency / current_price
        logger.debug(f"Raw calculated size: {raw_size} (Notional: {notional_value_in_quote_currency}, Price: {current_price})")

        min_deal_size_str = dealing_rules.get("minDealSize", {}).get("value")

        deal_size_step_str = dealing_rules.get("minSizeIncrement", {}).get("value")
        if deal_size_step_str is None: 
             deal_size_step_str = dealing_rules.get("dealSize", {}).get("step")

        if min_deal_size_str is None or deal_size_step_str is None:
            raise ValueError(f"Missing dealing rules (minDealSize.value or minSizeIncrement.value/dealSize.step) for {epic}.")

        try:
            min_size_allowed = float(min_deal_size_str)
            size_step = float(deal_size_step_str)
        except ValueError:
            raise ValueError("Could not convert minDealSize or size step to float.")

        if size_step <= 0:
            raise ValueError("Deal size step must be greater than zero.")

        if raw_size < min_size_allowed:
            stepped_size = 0.0
        else:
            num_steps = math.floor(raw_size / size_step)
            stepped_size = num_steps * size_step

        logger.debug(f"Size after rounding to step {size_step}: {stepped_size}")

        if stepped_size < min_size_allowed:
            logger.warning(f"Calculated size {stepped_size} for {epic} is less than minimum allowed {min_size_allowed}. "
                           f"Margin amount {margin_amount_in_quote_currency} may be too small.")
            return 0.0

        logger.info(f"Calculated trade size for {epic} ({direction.value}) with margin {margin_amount_in_quote_currency} (using instrument marginFactor): {stepped_size}")
        return stepped_size

    def get_account_preferences(self) -> Optional[Dict[str, Any]]:
        logger.info("Fetching account preferences...")
        try:
            preferences = self._request("GET", "accounts/preferences")
            if preferences:
                hedging_status = preferences.get('hedgingMode', 'N/A') 
                leverages_summary = []

                leverages_dict = preferences.get('leverages', {})
                if isinstance(leverages_dict, dict):
                    for instrument_type, lev_details in leverages_dict.items():
                        leverages_summary.append((instrument_type, lev_details.get('current')))
                logger.info(f"Account preferences retrieved: Hedging: {hedging_status}, Leverages: {leverages_summary}")
            return preferences
        except CapitalComAPIError as e:
            logger.error(f"Failed to get account preferences: {e}")
            return None

    def set_account_preferences(self, hedging_enabled: Optional[bool] = None,
                                leverages: Optional[Dict[str, int]] = None) -> bool:
        """
        Sets account preferences.
        'leverages' should be a dictionary like {"CURRENCIES": 30, "SHARES": 5}.
        """
        payload: Dict[str, Any] = {}
        if hedging_enabled is not None:
            payload["hedgingMode"] = hedging_enabled 

        if leverages is not None:

            payload["leverages"] = leverages

        if not payload:
            logger.warning("set_account_preferences called with no parameters to update. No action taken.")
            return False

        logger.info(f"Attempting to set account preferences: {payload}")
        try:
            self._request("PUT", "account/preferences", data=payload)
            logger.info("Account preferences update request sent successfully. Verify changes if needed.")

            return True
        except CapitalComAPIError as e:
            logger.error(f"Failed to set account preferences: {e}")
            return False

    def _get_ws_url(self) -> str:
        if not self.cst or not self.x_security_token:
            logger.warning("CST/X-SECURITY-TOKEN missing for WebSocket. Attempting re-login.")
            if not self.login(): 
                raise CapitalComAPIError("Cannot connect to WebSocket: Re-login failed, tokens still missing.")
        return f"{self.ws_base_url}?cst={self.cst}&x-security-token={self.x_security_token}"

    def _ws_application_ping_run(self):
        """Periodically sends application-level JSON pings to keep the service session alive."""
        logger.info(f"WebSocket application ping thread started. Interval: {self.APP_PING_INTERVAL_SECONDS}s.")
        while not self._ws_ping_stop_event.wait(self.APP_PING_INTERVAL_SECONDS): 
            if self._ws_ping_stop_event.is_set(): 
                break
            if self.ws_status == WebSocketStatus.CONNECTED and self.ws_connection:
                if self.cst and self.x_security_token:
                    ping_msg = {
                        "destination": "ping", 
                        "correlationId": f"app-ping-{int(time.time())}",
                        "cst": self.cst,
                        "securityToken": self.x_security_token
                    }
                    try:
                        logger.info("Sending application-level WebSocket PING to keep service session alive.")
                        self.ws_connection.send(json.dumps(ping_msg))
                    except Exception as e:
                        logger.warning(f"Could not send application-level WebSocket PING: {e}")
                else:
                    logger.warning("Cannot send application PING: auth tokens (CST/XST) missing, though WebSocket is connected.")
            else:
                logger.debug("WebSocket not connected, skipping application PING.")
        logger.info("WebSocket application ping thread stopped.")

    def _ws_on_message(self, ws: websocket.WebSocketApp, message_str: str):
        try:
            message = json.loads(message_str)
            logger.debug(f"WebSocket received message: {message_str[:300]}") 

            if message.get("errorCode"):
                logger.error(
                    f"WebSocket error from server: {message.get('errorMessage', str(message))} "
                    f"(Code: {message.get('errorCode')}, CorrelationID: {message.get('correlationId')})"
                )
                if message.get("errorCode") == "exceptions.security.authentication-failure":
                    logger.error("WebSocket authentication failure. Signaling WebSocket stop.")
                    self._ws_stop_event.set() 
                    if self.ws_connection: self.ws_connection.close()
                return

            if "status" in message and message.get("status") == "OK" and "correlationId" in message and "payload" in message:
                subs_payload = message["payload"].get("subscriptions")
                if subs_payload and isinstance(subs_payload, dict) and list(subs_payload.values())[0] == "PROCESSED":
                    logger.info(
                        f"WebSocket control response (e.g. sub/unsub ack): Dest: {message.get('destination')}, "
                        f"Status: {message['status']}, CorrID: {message['correlationId']}, "
                        f"Subscriptions processed: {subs_payload}"
                    )
                    return

            server_destination = message.get("destination")
            payload = message.get("payload")

            if payload and "epic" in payload:
                epic = payload["epic"]
                stream_destination_key_for_lookup: Optional[str] = None

                if server_destination == "quote": 
                    stream_destination_key_for_lookup = f"/market/{epic}"
                elif server_destination == "ohlc.event": 
                    resolution = payload.get("resolution")
                    bar_type = payload.get("type", OhlcBarType.CLASSIC.value) 
                    if resolution:
                        stream_destination_key_for_lookup = f"/ohlc/{epic}/{resolution}/{bar_type}"
                    else:
                        logger.warning(f"OHLC message for {epic} missing resolution in payload: {payload}")

                if stream_destination_key_for_lookup:
                    with self._ws_lock:
                        subscription_info = self._ws_subscriptions.get(stream_destination_key_for_lookup)

                    if subscription_info:
                        if subscription_info["active"] and subscription_info["callback"]:
                            try:
                                subscription_info["callback"](payload) 
                            except Exception as e:
                                logger.error(f"Error in WebSocket callback for {stream_destination_key_for_lookup}: {e}", exc_info=True)

                    else:
                        logger.debug(f"No active subscription found for stream key {stream_destination_key_for_lookup} from message destination {server_destination}. Current sub keys: {list(self._ws_subscriptions.keys())}")
                elif server_destination == "ping": 
                    if message.get("status") == "OK":
                        logger.info(f"Application-level WebSocket PONG received: {message}")
                    else:
                        logger.warning(f"Application-level WebSocket PING response with unexpected status: {message}")

                else: 
                    logger.debug(f"Data message received for server destination '{server_destination}' with epic '{epic}', but no specific handler: {payload}")
                return

            logger.debug(f"Unprocessed WebSocket message (format/type not recognized or no handler): {message_str[:300]}")

        except json.JSONDecodeError:
            logger.warning(f"WebSocket received non-JSON message: {message_str}")
        except Exception as e:
            logger.error(f"General error processing WebSocket message: {e}\nMessage: {message_str[:300]}", exc_info=True)

    def _ws_on_error(self, ws: websocket.WebSocketApp, error: Exception):
        logger.error(f"WebSocket low-level error: {error}")

    def _ws_on_close(self, ws: websocket.WebSocketApp, close_status_code: Optional[int], close_msg: Optional[str]):
        logger.warning(f"WebSocket connection closed. Code: {close_status_code}, Msg: {close_msg}")
        self.ws_connection = None 

        self._ws_ping_stop_event.set()
        if self._ws_ping_thread and self._ws_ping_thread.is_alive():
            logger.debug("Waiting for WebSocket application ping thread to join...")
            self._ws_ping_thread.join(timeout=3) 
            if self._ws_ping_thread.is_alive():
                 logger.warning("WebSocket application ping thread did not stop in time.")
        self._ws_ping_thread = None

        if self._ws_status != WebSocketStatus.STOPPING: 
            self._ws_status = WebSocketStatus.DISCONNECTED

        else: 
            self._ws_status = WebSocketStatus.DISCONNECTED
            logger.info("WebSocket connection closed as part of a stopping procedure.")

    def _ws_on_open(self, ws: websocket.WebSocketApp):
        logger.info("WebSocket connection opened successfully.")
        self._ws_status = WebSocketStatus.CONNECTED
        self._ws_reconnect_attempts = 0 

        if not self.cst or not self.x_security_token:
            logger.error("CRITICAL: WebSocket opened but CST/X-SECURITY-TOKEN are missing. Cannot (re)subscribe. Closing WS.")
            self._ws_stop_event.set() 
            if self.ws_connection:
                 self.ws_connection.close() 
            return

        self._ws_ping_stop_event.clear()
        self._ws_ping_thread = threading.Thread(target=self._ws_application_ping_run, name="CapitalComAppPingThread", daemon=True)
        self._ws_ping_thread.start()

        with self._ws_lock:

            subscriptions_to_resubscribe = list(self._ws_subscriptions.items())

        if not subscriptions_to_resubscribe:
            logger.info("No active subscriptions to process on WebSocket open/reconnect.")
            return

        logger.info(f"Processing {len(subscriptions_to_resubscribe)} existing subscriptions on WebSocket (re)open.")
        for stream_dest, sub_info in subscriptions_to_resubscribe:
            if sub_info["active"]:
                epic = sub_info["epic"]
                data_type: WebsocketDataType = sub_info["data_type"]
                resolution: Optional[HistoricalPriceResolution] = sub_info.get("resolution")
                bar_type_enum: Optional[OhlcBarType] = sub_info.get("bar_type") 
                correlation_id = f"reopen-{epic}-{data_type.value}-{int(time.time())}"

                payload_data: Dict[str, Any] = {"epics": [epic]}
                control_destination: str

                if data_type == WebsocketDataType.MARKET:
                    control_destination = "marketData.subscribe"
                elif data_type == WebsocketDataType.OHLC:
                    control_destination = "OHLCMarketData.subscribe" 
                    if not resolution or not bar_type_enum:
                        logger.error(f"Cannot resubscribe to OHLC for {epic}: Resolution or bar_type missing. Skipping. Sub info: {sub_info}")
                        continue
                    payload_data["resolutions"] = [resolution.value] 
                    payload_data["type"] = bar_type_enum.value       
                else:
                    logger.error(f"Unknown data type {data_type} for {epic} during resubscription. Skipping.")
                    continue

                subscribe_msg = {
                    "destination": control_destination,
                    "correlationId": correlation_id,
                    "cst": self.cst,
                    "securityToken": self.x_security_token,
                    "payload": payload_data
                }
                try:
                    logger.info(f"Resubscribing to {stream_dest} (Control: {control_destination}, Epic: {epic}, Payload: {payload_data})")
                    ws.send(json.dumps(subscribe_msg))
                except Exception as e:
                    logger.error(f"Failed to send resubscription for {stream_dest} (Epic: {epic}): {e}")
            else: 
                logger.debug(f"Skipping inactive subscription found in list for {stream_dest} during on_open processing.")

    def _ws_run(self):
        self._ws_status = WebSocketStatus.CONNECTING 
        while not self._ws_stop_event.is_set():
            try:
                ws_url = self._get_ws_url() 
                logger.info(f"Attempting to connect to WebSocket: {self.ws_base_url.split('?')[0]}...") 
                self._ws_status = WebSocketStatus.CONNECTING

                self.ws_connection = websocket.WebSocketApp(ws_url,
                                                            on_message=self._ws_on_message,
                                                            on_error=self._ws_on_error,
                                                            on_close=self._ws_on_close,
                                                            on_open=self._ws_on_open)

                self.ws_connection.run_forever(ping_interval=30, ping_timeout=10, sslopt={"check_hostname": True})

            except CapitalComAPIError as e: 
                logger.error(f"Cannot start or maintain WebSocket due to API error: {e}. Stopping WebSocket attempts.")
                self._ws_stop_event.set() 
                break 
            except websocket.WebSocketException as e: 
                logger.error(f"WebSocket connection/setup exception: {e}")
            except Exception as e: 
                logger.error(f"Unexpected error in WebSocket run loop (connection attempt phase): {e}", exc_info=True)

            if self._ws_stop_event.is_set():
                logger.info("WebSocket stop event is set. Exiting run loop.")
                break 

            if self._ws_reconnect_attempts < self._ws_max_reconnect_attempts:
                delay = min(self._ws_initial_reconnect_delay * (2 ** self._ws_reconnect_attempts), self._ws_max_reconnect_delay)
                logger.info(f"WebSocket disconnected. Reconnect attempt {self._ws_reconnect_attempts + 1}/{self._ws_max_reconnect_attempts} in {delay} seconds...")
                self._ws_status = WebSocketStatus.CONNECTING 

                wait_interval = 1 
                slept_time = 0
                while slept_time < delay and not self._ws_stop_event.is_set():
                    time.sleep(wait_interval)
                    slept_time += wait_interval

                if self._ws_stop_event.is_set(): 
                    logger.info("WebSocket stop event received during reconnect delay. Aborting reconnect.")
                    break 

                self._ws_reconnect_attempts += 1
            else:
                logger.error(f"WebSocket max reconnect attempts ({self._ws_max_reconnect_attempts}) reached. Stopping WebSocket thread.")
                self._ws_stop_event.set() 
                break 

        if self.ws_connection: 
            try:
                logger.debug("Ensuring WebSocket connection is closed in _ws_run exit path.")
                self.ws_connection.close()
            except Exception as e:
                logger.warning(f"Exception while trying to close WebSocket in _ws_run exit: {e}")
        self.ws_connection = None

        self._ws_ping_stop_event.set()
        if self._ws_ping_thread and self._ws_ping_thread.is_alive():
            self._ws_ping_thread.join(timeout=3)
        self._ws_ping_thread = None

        if self._ws_status != WebSocketStatus.STOPPING: 
             self._ws_status = WebSocketStatus.DISCONNECTED
        logger.info("WebSocket thread run loop has finished.")

    def _start_websocket_thread(self) -> bool:
        if self.ws_thread and self.ws_thread.is_alive():
            logger.info("WebSocket thread already running.")
            if self.ws_status == WebSocketStatus.CONNECTED:
                return True 

        if not self.cst or not self.x_security_token:
            logger.info("CST/XST tokens not immediately available for WS start. _get_ws_url in run loop will attempt login.")

        self._ws_stop_event.clear()       
        self._ws_ping_stop_event.clear()  
        self._ws_reconnect_attempts = 0   
        self._ws_status = WebSocketStatus.CONNECTING 

        self.ws_thread = threading.Thread(target=self._ws_run, name="CapitalComWSThread", daemon=True)
        self.ws_thread.start()
        logger.info("WebSocket thread started/starting.")

        return True

    def _stop_websocket_thread(self):
        if self._ws_status == WebSocketStatus.DISCONNECTED and not (self.ws_thread and self.ws_thread.is_alive()):
            logger.info("WebSocket thread already stopped or was never meaningfully started.")

            if self._ws_ping_thread and self._ws_ping_thread.is_alive():
                self._ws_ping_stop_event.set()
                self._ws_ping_thread.join(timeout=3)
            self._ws_ping_thread = None
            return

        logger.info("Attempting to stop WebSocket thread and application ping thread...")
        original_status = self._ws_status 
        self._ws_status = WebSocketStatus.STOPPING 

        self._ws_stop_event.set()
        self._ws_ping_stop_event.set()

        ws_conn_to_close = self.ws_connection 
        if ws_conn_to_close:
            try:
                logger.debug("Actively closing WebSocket connection from _stop_websocket_thread.")
                ws_conn_to_close.close() 
            except Exception as e:
                logger.warning(f"Exception during WebSocket active close in _stop_websocket_thread: {e}")

        if self.ws_thread and self.ws_thread.is_alive():
            logger.debug(f"Waiting for WebSocket main thread ({self.ws_thread.name}) to join...")
            self.ws_thread.join(timeout=15) 
            if self.ws_thread.is_alive():
                logger.warning(f"WebSocket main thread ({self.ws_thread.name}) did not stop in the allocated time. Status was {original_status}.")
            else:
                logger.info(f"WebSocket main thread ({self.ws_thread.name}) joined successfully.")

        if self._ws_ping_thread and self._ws_ping_thread.is_alive():
            logger.debug(f"Waiting for WebSocket application ping thread ({self._ws_ping_thread.name}) to join...")
            self._ws_ping_thread.join(timeout=5) 
            if self._ws_ping_thread.is_alive():
                logger.warning(f"WebSocket application ping thread ({self._ws_ping_thread.name}) did not stop.")
            else:
                logger.info(f"WebSocket application ping thread ({self._ws_ping_thread.name}) joined successfully.")

        self.ws_thread = None
        self.ws_connection = None 
        self._ws_ping_thread = None
        self._ws_status = WebSocketStatus.DISCONNECTED 
        logger.info("WebSocket thread and related processes stopped.")

    def subscribe_to_epic_data(self,
                               epic: str,
                               data_type: WebsocketDataType,
                               callback: Callable[[Dict[str, Any]], None],
                               resolution: Optional[HistoricalPriceResolution] = None,
                               bar_type: OhlcBarType = OhlcBarType.CLASSIC):
        if not self.cst or not self.x_security_token:
            logger.error(f"Cannot subscribe to {epic} ({data_type.value}): API not logged in (CST/XST missing). Please login first.")

            return

        stream_destination_key: str 
        control_destination_api: str 
        payload_data: Dict[str, Any] = {"epics": [epic]}

        if data_type == WebsocketDataType.MARKET:
            stream_destination_key = f"/market/{epic}"
            control_destination_api = "marketData.subscribe"
        elif data_type == WebsocketDataType.OHLC:
            if resolution is None:
                raise ValueError("Resolution must be provided for OHLC data type subscription.")
            stream_destination_key = f"/ohlc/{epic}/{resolution.value}/{bar_type.value}"
            control_destination_api = "OHLCMarketData.subscribe" 
            payload_data["resolutions"] = [resolution.value]     
            payload_data["type"] = bar_type.value                
        else:
            raise ValueError(f"Unsupported WebSocket data_type: {data_type}")

        with self._ws_lock:
            if stream_destination_key in self._ws_subscriptions and self._ws_subscriptions[stream_destination_key]["active"]:
                logger.warning(f"Already subscribed or subscription pending for {stream_destination_key}. Updating callback if different.")
            self._ws_subscriptions[stream_destination_key] = {
                "callback": callback,
                "epic": epic,
                "data_type": data_type,
                "resolution": resolution, 
                "bar_type": bar_type,     
                "active": True            
            }
        logger.info(f"Subscription for {stream_destination_key} queued/updated.")

        if not self._start_websocket_thread(): 
            logger.error(f"WebSocket thread could not be started. Subscription to {stream_destination_key} is queued but won't be sent yet.")
            return

        if self.ws_status == WebSocketStatus.CONNECTED and self.ws_connection:
            correlation_id = f"sub-{epic}-{data_type.value}-{resolution.value if resolution else ''}-{bar_type.value if data_type == WebsocketDataType.OHLC else ''}-{int(time.time())}"
            subscribe_msg = {
                "destination": control_destination_api,
                "correlationId": correlation_id,
                "cst": self.cst,
                "securityToken": self.x_security_token,
                "payload": payload_data
            }
            try:
                logger.info(f"Sending subscription request for {stream_destination_key} (API Dest: {control_destination_api}, Payload: {payload_data})")
                self.ws_connection.send(json.dumps(subscribe_msg))
            except Exception as e: 
                logger.error(f"Failed to send subscription message for {stream_destination_key} (WS might be closing/connected): {e}. It may be picked up by reconnect/on_open logic if WS restarts.")
        elif self.ws_status == WebSocketStatus.CONNECTING:
            logger.info(f"WebSocket is currently connecting. Subscription for {stream_destination_key} is queued and will be handled by on_open handler upon connection.")
        else: 
             logger.warning(f"WebSocket status is {self.ws_status}. Subscription for {stream_destination_key} is queued. Ensure WebSocket connects/restarts for it to be processed.")

    def unsubscribe_from_epic_data(self,
                                   epic: str,
                                   data_type: WebsocketDataType,
                                   resolution: Optional[HistoricalPriceResolution] = None,
                                   bar_type: OhlcBarType = OhlcBarType.CLASSIC):
        stream_destination_key: str
        control_destination_api: str
        unsubscribe_payload: Dict[str, Any] = {"epics": [epic]} 

        if data_type == WebsocketDataType.MARKET:
            stream_destination_key = f"/market/{epic}"
            control_destination_api = "marketData.unsubscribe"
        elif data_type == WebsocketDataType.OHLC:
            if resolution is None:
                raise ValueError("Resolution must be provided for OHLC data type unsubscription to identify the correct stream.")
            stream_destination_key = f"/ohlc/{epic}/{resolution.value}/{bar_type.value}"
            control_destination_api = "OHLCMarketData.unsubscribe" 

            unsubscribe_payload["resolutions"] = [resolution.value]
            unsubscribe_payload["types"] = [bar_type.value]

        else:
            raise ValueError(f"Unsupported WebSocket data_type: {data_type}")

        sub_info_popped = None
        with self._ws_lock:

            sub_info_popped = self._ws_subscriptions.pop(stream_destination_key, None)

        if sub_info_popped:
            logger.info(f"Locally removed/marked inactive subscription tracking for {stream_destination_key}.")

            if self.ws_status == WebSocketStatus.CONNECTED and self.ws_connection and self.cst and self.x_security_token:
                correlation_id = f"unsub-{epic}-{data_type.value}-{resolution.value if resolution else ''}-{bar_type.value if data_type == WebsocketDataType.OHLC else ''}-{int(time.time())}"
                unsubscribe_msg = {
                    "destination": control_destination_api,
                    "correlationId": correlation_id,
                    "cst": self.cst,
                    "securityToken": self.x_security_token,
                    "payload": unsubscribe_payload
                }
                try:
                    logger.info(f"Sending unsubscribe request for {stream_destination_key} (API Dest: {control_destination_api}, Payload: {unsubscribe_payload}) to server.")
                    self.ws_connection.send(json.dumps(unsubscribe_msg))
                except Exception as e:
                    logger.error(f"Failed to send unsubscribe message for {stream_destination_key} to server: {e}")
            else:
                logger.info(f"Unsubscribed {stream_destination_key} locally. WebSocket not in a state to send server message (Status: {self.ws_status}, Tokens Present: {bool(self.cst and self.x_security_token)}).")
        else:
            logger.warning(f"No active subscription found locally for {stream_destination_key} to unsubscribe.")

        with self._ws_lock:
            no_active_subscriptions = not bool(self._ws_subscriptions)

        if no_active_subscriptions and \
           self.ws_status != WebSocketStatus.DISCONNECTED and \
           self.ws_status != WebSocketStatus.STOPPING: 
            logger.info("No active WebSocket subscriptions remaining. Stopping WebSocket thread.")
            self._stop_websocket_thread() 

    def stop_all_websocket_subscriptions(self):
        logger.info("Stopping all WebSocket subscriptions and initiating WebSocket thread shutdown...")

        destinations_to_unsubscribe_server: List[Dict[str, Any]] = []
        with self._ws_lock:
            if not self._ws_subscriptions:
                logger.info("No local WebSocket subscriptions to clear or send unsubscribe messages for.")
            else:
                for stream_dest, sub_info in self._ws_subscriptions.items():

                    destinations_to_unsubscribe_server.append({
                        "stream_destination_key": stream_dest, 
                        "epic": sub_info["epic"],
                        "data_type": sub_info["data_type"],
                        "resolution": sub_info.get("resolution"), 
                        "bar_type": sub_info.get("bar_type")      
                    })
                self._ws_subscriptions.clear() 
                logger.debug(f"Cleared all {len(destinations_to_unsubscribe_server)} local WebSocket subscription tracking entries.")

        if self.ws_status == WebSocketStatus.CONNECTED and self.ws_connection and self.cst and self.x_security_token:
            if destinations_to_unsubscribe_server:
                logger.info(f"Sending server unsubscribe messages for {len(destinations_to_unsubscribe_server)} streams.")
                for item in destinations_to_unsubscribe_server:
                    epic = item["epic"]
                    data_type: WebsocketDataType = item["data_type"]
                    unsubscribe_payload = {"epics": [epic]}
                    control_dest_api: str

                    if data_type == WebsocketDataType.MARKET:
                        control_dest_api = "marketData.unsubscribe"
                    elif data_type == WebsocketDataType.OHLC:
                        control_dest_api = "OHLCMarketData.unsubscribe"
                        resolution: Optional[HistoricalPriceResolution] = item.get("resolution")
                        bar_type: Optional[OhlcBarType] = item.get("bar_type")
                        if resolution and bar_type: 
                            unsubscribe_payload["resolutions"] = [resolution.value]
                            unsubscribe_payload["types"] = [bar_type.value]
                        else: 
                            logger.warning(f"OHLC resolution/type missing for {epic} during stop_all; sending generic epic unsubscribe.")
                    else:
                        logger.warning(f"Unknown data_type {data_type} for epic {epic} during stop_all server unsubscribe. Skipping.")
                        continue

                    unsubscribe_msg = {
                        "destination": control_dest_api,
                        "correlationId": f"unsub-all-{epic}-{data_type.value}-{int(time.time())}",
                        "cst": self.cst,
                        "securityToken": self.x_security_token,
                        "payload": unsubscribe_payload
                    }
                    try:
                        self.ws_connection.send(json.dumps(unsubscribe_msg))
                        logger.debug(f"Sent server unsubscribe for {item['stream_destination_key']} (API Dest: {control_dest_api}, Epic: {epic}, Payload: {unsubscribe_payload})")
                    except Exception as e:
                        logger.warning(f"Failed to send server unsubscribe for {item['stream_destination_key']} during stop_all: {e}")
            else:
                logger.info("No subscriptions were active to send unsubscribe messages for during stop_all.")
        else:
            if destinations_to_unsubscribe_server: 
                logger.info(f"WebSocket not connected or tokens missing; server unsubscribe messages for {len(destinations_to_unsubscribe_server)} streams not sent during stop_all. Local subscriptions cleared.")

        self._stop_websocket_thread() 
        logger.info("All WebSocket subscriptions processed for stopping, and WebSocket thread shutdown initiated/confirmed.")

    def __enter__(self):
        if not self.login(): 
            raise CapitalComAPIError("Failed to login upon entering context manager.")

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        logger.info("Exiting context manager, ensuring logout and WebSocket shutdown...")
        self.logout() 
        logger.info("Context manager exited.")