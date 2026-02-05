# Polymarket REST API Protections

This document outlines the multi-layered protection system implemented in the Polymarket Direct REST API client to ensure safe and compliant access to Polymarket services.

## Overview

The `PolyRestAPI` class implements protections that safeguard the user from geo-blocked access, compliance violations, and orphaned/floating orders. 
These protections are designed to prevent orders from being placed or API credentials from being exposed from restricted jurisdictions, 
and crucially, to allow graceful cleanup of active orders when critical failures occur. Critical as this class will handle
**real** funds on Polymarket which does not have a testnet environment.

All connections are WireProxy-aware with the ID `POLYMARKET`, ensuring traffic routes through your configured proxy when enabled.

## WireProxy Integration

Before any protection stages, all HTTP/WebSocket traffic is configured to route through WireProxy if enabled:

1. **Request Session Proxy**: `update_request_session_proxy(idx='POLYMARKET', session=self.session)` configures SOCKS5 proxy settings
2. **ClobClient HTTPX Patching**: `_make_httpx_clob_client()` patches the internal httpx client to use WireProxy SOCKS5 binding (if configured)
3. **WebSocket Proxy Awareness**: `start_proxy_aware_ws('POLYMARKET', websocket)` routes WebSocket connections through proxy

This ensures that **your actual IP address is never exposed to Polymarket** if you're using WireProxy for location protection.
HIGHLY RECOMMENDED TO integrate WireProxy see [WIREPROXY Documentation](../docs/WIREPROXY.md).

## Protections

### Pre-Connection IP Safety Check

**Location**: `IPSafety` class, lines 34-50

**Purpose**: This is the first line of defense that operates **before any connection to Polymarket is made**.

**How it works**:
1. Uses `ipinfo.io/json` to fetch current IP information
2. Checks the country code against a hardcoded list of known geo-blocked regions
3. **Important**: This check never exposes your IP to Polymarket - only to ipinfo.io

**Known Bad Regions**:
```python
KNOWN_BAD_REGIONS = ["US", "GB", "FR", "DE", "IT", "BE", "PL", "AU", "SG", "TW",
                     "TH", "RU", "BY", "CU", "IR", "IQ", "KP", "SY", "VE", "MM", 
                     "LY", "SD", "SS", "SO", "YE", "ZW", "LB", "ET", "NI", "BI", 
                     "CF", "CD", "UM", "AE"]
```

**Environmental Variable**: `POLYMARKET_PARANOID`
- **Default**: `false`
- **When `true`**: If IP is in bad region, immediately terminates with RuntimeError
- **When `false`**: Shows warning but proceeds to Stage 2 for verification

**Why this exists**: This provides an early warning system without exposing your IP address to Polymarket's systems, 
maintaining privacy while checking against known restrictions. It also prevents leaking polymarket DNS to ISPs in blocked regions.

### Direct Polymarket Geo-Block Verification

**Location**: `check_geo_blocked()` method, lines 146-155

**Purpose**: This is the **first actual connection to Polymarket** to verify if their systems specifically block your IP.

**How it works**:
1. Makes a request to `https://polymarket.com/api/geoblock`
2. Polymarket's servers determine if your IP should be blocked
3. Returns a boolean indicating blocked status

**Environmental Variable**: `POLYMARKET_PROTECTION`
- **Default**: `true`
- **When `true`**: Performs this check and raises RuntimeError if blocked
- **When `false`**: **DANGEROUS** - Skips this protection entirely

**Warning when disabled**: The system provides a 30-second countdown with multiple warnings (visual and auditory) before proceeding, as this step exposes your IP to Polymarket and bypasses critical safety checks.

**Why this exists**: This provides the definitive answer from Polymarket's own systems about whether your specific IP is blocked, going beyond hardcoded region lists. If your IP 
is blocked here, YOU CANNOT TRADE IT WILL BE REJECTED FROM POLYMARKET. The only reason `POLYMARKET_PROTECTION` allows you to continue is if you are only accessing market data
whicgh _MAY_ be allowed from blocked regions. All order placement attempts will fail and raise errors.


### Fatal Callback System (Order Cleanup)

**Location**: `fatal_decorator` decorator (lines 58-77) and `fatal_callback` parameter (line 99)

**Purpose**: Gracefully handle critical failures in decorated methods by executing a user-defined callback before raising the exception. This prevents "floating orders" — orders left open on Polymarket when your trading bot crashes.

**How it works**:
1. Decorates critical methods (`place_order`, `build_order`, `cancel_order`, `get_orders`, `get_trades`, `get_order_status`, `get_balance`)
2. Wraps each method in try-except
3. On any exception, calls `self.fatal_callback()` with context dict before re-raising
4. Callback receives: `self`, `function` name, `args`, `kwargs`, `exception` object, and `traceback` string

**Callback Parameters** (dict passed to fatal_callback):
```python
{
    'self': PolyRestAPI,           # Instance for accessing order_cache
    'function': str,                # Name of failed function (e.g., 'place_order')
    'args': tuple,                  # Positional arguments
    'kwargs': dict,                 # Keyword arguments  
    'exception': Exception,         # The exception object
    'traceback': str               # Formatted traceback
}
```

**Example Implementation** (from rest.py lines 444-449):
```python
def fatal_handler(info: dict):
    print("[FATAL] Cancelling all orders...")
    api_instance = info.get('self')
    if api_instance:  # Ensure we have the API instance (see class docs why we use .get)
        # Note: order_cache is a `safe` dictionary since it does not make API calls,
        # you should design your callbacks to avoid using API calls within them or volatile functions
        for order in api_instance.order_cache['orders']:
            # this is an API call, be cautious and make sure to wrap in try-except and double check
            # these functions are being constructed properly in this case it's cancelling orders
            # known to have been opened given the order cache
            try:
                api_instance.cancel_order(order_id=order['orderID'])
            except Exception as e:
                print('Failed to cancel order {}: {}'.format(order['orderID'], e))
    

rest = PolyRestAPI(
    private_key=...,
    proxy_funder=...,
    fatal_callback=fatal_handler
)
```

**Order Cache Access**:
- `PolyRestAPI` maintains `_order_cache` with all placed orders
- Structure: `{'orders': [list of order dicts], order_id: {...order details...}}`
- Callback can iterate through `['orders']` list to cancel all active orders

**Default Behavior** (if no callback provided):
- Prints colored error message with full context
- Still re-raises the exception (prevents silent failure)
- Provides visual/auditory alerts for critical failures

**Extensions**:
- Can send Slack/Discord/email notifications
- Can trigger automatic failsafe mechanisms (e.g., forced position closure)
- Can log detailed incident information to external systems
- Can update monitoring dashboards
- Can trigger pagerduty/opsgenie incidents

The key design: **callback executes BEFORE exception propagates**, allowing cleanup before the program terminates.


## Fail-Safe Mechanisms
- **Hard Termination**: Immediate exit when definite blocks are detected
- **Warning Systems**: Visual and audible warnings when bypassing protections
- **Graceful Countdowns**: 30-second warning period when dangerous configurations are detected

## Environmental Controls
- **Fine-grained Control**: Separate controls for paranoia level and protection bypassing
- **Default-Safe**: All protections enabled by default
- **Explicit Bypass**: Requires explicit action to disable safety measures

# Important Notes

1. **IP Redaction**: All logging automatically redacts IP addresses for privacy
2. **Proxy Integration**: All checks respect the WireProxy configuration and use the same proxy settings
3. **Persistent Warnings**: System uses multiple notification methods (terminal colors, sounds, macOS notifications) for critical warnings
4. **Compliance Focus**: These protections are designed to help maintain compliance with both Polymarket's terms and regional regulations

## Error Handling & Resilience

### Protection System Diagnostics
The protection system provides detailed error messages that help diagnose:
- Which stage failed
- Why the failure occurred
- What environmental variables are in effect
- IP information (with redaction for privacy)

This ensures users can troubleshoot while understanding security implications.

### Fatal Callback Guarantees
The fatal callback system guarantees:

1. **Pre-exception execution**: Callback runs before exception propagates
2. **Always raised**: Original exception is always re-raised (no silent failures)
3. **Full context**: Callback receives all data needed for debugging and cleanup
4. **Extensible**: Can implement custom logic for different failure scenarios

### Order Recovery Flow
```
Critical method fails (e.g., place_order)
         │
         ├─► fatal_decorator catches exception
         │         │
         │         └─► Call fatal_callback(info_dict)
         │              │
         │              └─► User can iterate order_cache['orders']
         │              └─► User can cancel all orders
         │              └─► User can send alerts
         │              └─► User can log incidents
         │
         └─► Re-raise exception (calling code sees failure)
```

## Combining Protections

For maximum safety in production trading:

```python
import os
from argus.polymarket_direct import PolyRestAPI

# Stage 1-2 controls
os.environ['POLYMARKET_PARANOID'] = 'true'  # Hard-fail on bad regions
os.environ['POLYMARKET_PROTECTION'] = 'true'  # Check Polymarket's geo-block

# Stage 4: Custom fatal handler
def emergency_shutdown(info: dict):
    """Cancel all orders and alert on critical failure"""
    api = info.get('self')
    func = info.get('function')
    exc = info.get('exception')
    
    print(f"EMERGENCY: {func} failed with {exc}")
    
    if api:
        # Cancel all active orders
        for order in api.order_cache['orders']:
            try:
                api.cancel_order(order['orderID'])
            except Exception as e:
                print(f"Failed to cancel {order['orderID']}: {e}")
    
    # Send alerts
    send_pagerduty_alert(f"Polymarket API crash: {func}")

# Initialize with protection
api = PolyRestAPI(
    private_key=os.environ['POLYMARKET_PRIVATE_KEY'],
    proxy_funder=os.environ['POLYMARKET_PROXY_FUNDER'],
    fatal_callback=emergency_shutdown
)
```