# Polymarket DISPATCHER

* [Pull Request](https://github.com/The-Sal/Argus/pull/52)
* [polymarket_direct](../docs/POLYMARKET.md)


# Overview
This dispatcher is the re-write of the original Polymarket Dispatcher that's been appreciated way back in `01cbe34`, and now only available on
`legacy/polymarket-dispatcher` branch. Unlike the old dispatcher which was built on top of `py_clob_client` and it's APIs, this
new one is built completely on top of `polymarket_direct` module and actually [works](../argus/polymarket_direct/__init__.py).

# API
## Features
The API will work completely with TCP and have the following features:
* Multiplexing Connections
* P2 Protocol* (kinda. see more below)
* Socket Reconnection and Lifecycle Management
* Account Status (Balances, Positions, etc)
* Market Data
* Account Management
* Automatic Symbol Management (symbol lifecycle management)
* Order Management (cancellation, modification, placing, etc...)
* Error Handling and Reporting
* Full Order Book Data (*not available on the first release, expected later. With up to 20 tranch support both sides)

Polymarket Dispatcher will be the first Argus Dispatcher to implement order management functions. Allowing
you to place live orders to polymarket from Argus. For this reason, this dispatcher will also be the first 
dispatcher that requires authentication from the client. For all non-trade information there is no
authentication required, but for order management an 'authentication' step is required. Moreover, you
will be locked to ONE (configurable via .env) 'authenticated' connection at a time, this, of course, 
is for security reasons and to avoid multiple connections trying to place orders. Moreover, 'authenticated' clients
will be ordered within the multiplexer array at position 0, meaning it will always have priority when market data
arrives. This is an EXPENSIVE process (depending on how many clients there are), and during this process the entire
dispatcher will be thread locked to avoid race conditions. It's some-what intentional design to avoid rapid login/logout
attempts as each attempt locks the entire dispatcher for a brief moment. This dispatcher is also fully integrated with
[WIREPROXY](../docs/WIREPROXY.md). 


_WORK IN PROGRESS DOCUMENTATION_
