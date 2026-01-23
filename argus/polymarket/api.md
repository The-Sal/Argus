# Polymarket Dispatcher API

# Features
* Full Market Data (top of book, and full order book)
* Account Updates (order fill, order cancel, balance updates, etc..)
* Account Ledger (trade history, capital, etc..)
* Order Execution (place, modify, cancel orders)


# Message Formats
There are multiple types of messages that the server will respond with, these follow into the 
market data category and account data category.

### Market Data 
* Market Data Updates (Level 1) which are P2 Encoded
* Market Data Updates (Full Order Book) which are encoded using P2 encoding
### Account Data
* Order Execution, Acknowledgements, Fills, Cancellations which are JSON Encoded
* Account Updates (Balance, Positions, etc..) which are JSON Encoded
* Account Ledger Entries (Trade History, Capital Changes, etc..) which are JSON Encoded