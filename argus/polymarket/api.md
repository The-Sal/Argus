# Polymarket Dispatcher API

# Features
* Full Market Data (top of book, and full order book)
* Account Updates (order fill, order cancel, balance updates, etc..)
* Account Ledger (trade history, capital, etc..)
* Order Execution (place, modify, cancel orders)


# Message Formats (Outgoing)
The following data types will be sent from the server to the client continuously, as updates
occur:

### Market Data 
* Market Data Updates (Level 1)
* Market Data Updates (Full Order Book)
### Account Data
* Order Execution, Acknowledgements, Fills, Cancellation
* Account Updates (Balance, Positions, etc..)
* Account Ledger Entries (Trade History, Capital Changes, etc..)
### Encodings
All market data will be encoded using [Protocol 2](../../argus/capital/_svr_utils.py) \
All account data will be encoded using JSON and then wrapped in the [Basic Packet Protocol](../../argus/capital/_svr_utils.py) 

# Message Formats (Incoming)
All incoming messages to the server must be JSON-encoded and wrapped in [Basic Packet Protocol](../../argus/capital/_svr_utils.py).
All JSON follows the exact same structure:

```
{
    "action": <action_name> | str,
    "data": <action-specific parameters, can be of type Any of none>,
    "auth": <required only for execution related actions> | str
}
```

Where `action` is a string representing the type of action to be performed, and `data` is an object containing the relevant parameters for that action.
For every incoming message a response message will be sent back to the client indicating the status of this action. This follows the format

```
{
    "action": <action_name> | str,
    "status": <"success" | "error"> | str,
    "data": <action-specific response data, can be of type Any or none>,
    "error": <error message if status is "error", otherwise null> | str | null,
    "_kind":  see _kind below
}
```


# Parsing Incoming Messages
Due to the heterogeneous nature of incoming messages, it is recommended to parse them using the [Argus Parsers Repository](https://github.com/the-sal/argus-parse).
This repository contains pre-defined parsers for all incoming messages, types, and structures. It also contains utilities for encoding outbound messages in the required format.
Moreover, it supports automatic distinction between a response message, account update message, etc.

## _kind Field
The `_kind` field in the response message indicates the type of message received. It only exists in JSON-wrapped messages.
The possible values for `_kind` are:
* "response": Indicates that the message is a response to an action performed by the client.
* "account_update": Indicates that the message is an account update (e.g., balance changes, position updates).
* "order_update": Indicates that the message is related to order execution (e.g., order fills, cancellations).
