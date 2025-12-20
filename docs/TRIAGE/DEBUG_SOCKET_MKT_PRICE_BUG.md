# Debug Socket Not Updating MKT Price on Portfolio Assets

## Issue Overview

**Bug Number:** #41  
**Severity:** Medium  
**Affected Components:** Python AccountProvider, Swift AccountProvider, Debug Socket (port 9973)  
**Status:** Confirmed - Root cause identified

## Problem Statement

Within both Argus implementations (Swift & Python), there is a bug in the Debug Socket output where the portfolio market value does not update in real-time. Only the unrealised PnL is constantly updated from new market data. The MKT Value and MKT Price remain static, reflecting only the price of the asset when the initial API call was made.

This means:
- ✅ **Unrealised PnL** updates continuously with live market data
- ❌ **MKT Price** stays fixed at initial value
- ❌ **MKT Value** stays fixed at initial value

## Root Cause Analysis

### Hypothesis (from @The-Sal)

> "Presumably just a gander is because on new market data update we mutated the unrealised pnl but we don't mutate the market price value of the object in both swift and python–not sure..."

**Verdict:** ✅ **CONFIRMED** - The hypothesis is 100% accurate.

### Technical Analysis

#### Python Implementation

**File:** `argus/ib/__init__.py`

**Initial Data Setup (lines 209-228):**
```python
def fetch_account_positions(self) -> list[STK_Position]:
    """Fetch account positions for the given IBKR Account id."""
    response = self.session.get(
        self.urls['account_positions'].format(self.trading_account_id)
    )
    data = response.json()
    portfolio = []
    for asset in data:
        # ... filtering logic ...
        portfolio.append(STK_Position.from_dict(asset))
    return portfolio
```

At this point, `STK_Position` objects are created with `mkt_price` and `mkt_value` from the API response, reflecting the market state at the time of the call.

**Market Data Update Handler (lines 707-728):**
```python
@expand_exception_decorator('AccountProvider._on_market_data', propagate=False)
def _on_market_data(self, data: IBKR_CapitalComMKTDataLive):
    """Handle market data received via FakeSocket"""
    # ... validation logic ...
    
    contract_id = int(self.ss.translate_symbol_to_conid(data.symbol))
    position: STK_Position = self._portfolio.get(contract_id)
    cost = enforce_currency(position.avg_cost)
    
    if data.last != 0:
        pnl = (enforce_currency(data.last) - cost) * float(position.position)
        position.formatted_unrealized_pnl = f"{pnl:.2f}"
        position.unrealized_pnl = pnl
        self._transmit(position)
        # ❌ MISSING: position.mkt_price = data.last
        # ❌ MISSING: position.mkt_value = data.last * position.position
```

**What's Updated:**
- ✅ `position.unrealized_pnl`
- ✅ `position.formatted_unrealized_pnl`

**What's Missing:**
- ❌ `position.mkt_price` - should be set to `data.last`
- ❌ `position.mkt_value` - should be set to `data.last * position.position`

#### Swift Implementation

**File:** `argus_swift/Sources/ArgusServer/IB/IBAccountProvider.swift`

**Market Data Update Handler (lines 187-214):**
```swift
private func onMarketData(_ data: Any) {
    // Handle market data received via FakeSocket
    guard let marketData = data as? IBKR_CapitalComMKTDataLive else {
        // ... validation logic ...
        return
    }

    let symbol = marketData.symbol
    guard let contractId = shortableSharesData.translateSymbolToConid(symbol) else {
        print("Could not translate symbol \(symbol) to contract ID")
        return
    }

    guard let position = portfolio[contractId] else {
        return
    }

    let cost = enforceCurrency(position.avgCost)
    if marketData.last != 0 {
        let pnl = (enforceCurrency(marketData.last) - cost) * position.position
        position.formattedUnrealizedPnl = String(format: "%.2f", pnl)
        position.unrealizedPnl = pnl
        transmit(position: position)
        // ❌ MISSING: position.mktPrice = marketData.last
        // ❌ MISSING: position.mktValue = marketData.last * position.position
    }
}
```

**Same Issue:**
- ✅ Updates `unrealizedPnl` and `formattedUnrealizedPnl`
- ❌ Does NOT update `mktPrice` or `mktValue`

## Impact Assessment

### User Experience Impact

**Severity: Medium**

1. **Debug Socket Clients** (port 9973):
   - Cannot see real-time market prices for portfolio holdings
   - Cannot see real-time market value of positions
   - Dashboard/monitoring tools show stale prices
   - **Workaround:** Can still see unrealised PnL which indirectly reflects price changes

2. **Data Accuracy:**
   - Unrealised PnL calculations ARE correct (they use live `data.last`)
   - But displayed `mkt_price` and `mkt_value` are stale
   - Creates confusion: PnL changes but price doesn't?

3. **Testing/Development:**
   - Hard to debug market data issues
   - Cannot verify if live prices are being received correctly
   - Monitoring tools show misleading information

### Architectural Impact

**None** - This is a localized bug in the market data handling logic. The fix is straightforward and doesn't require architectural changes.

## Reproduction Steps

1. Start Argus with Debug Socket enabled (port 9973)
2. Connect a debug client: `python tests/debug_socket.py`
3. Wait for initial portfolio data to load
4. Observe the portfolio display
5. Wait for market to move (or wait for price updates)
6. **Expected:** `Mkt Price` and `Mkt Value` columns update continuously
7. **Actual:** Only `Unrealized P&L` column updates; prices remain static

## Solution Design

### Proposed Fix

#### Python (`argus/ib/__init__.py`, line ~724-728)

**Current Code:**
```python
if data.last != 0:
    pnl = (enforce_currency(data.last) - cost) * float(position.position)
    position.formatted_unrealized_pnl = f"{pnl:.2f}"
    position.unrealized_pnl = pnl
    self._transmit(position)
```

**Fixed Code:**
```python
if data.last != 0:
    # Update market price from live data
    position.mkt_price = enforce_currency(data.last)
    
    # Calculate market value
    position.mkt_value = position.mkt_price * float(position.position)
    
    # Calculate and update unrealized PnL
    pnl = (position.mkt_price - cost) * float(position.position)
    position.formatted_unrealized_pnl = f"{pnl:.2f}"
    position.unrealized_pnl = pnl
    
    self._transmit(position)
```

#### Swift (`argus_swift/Sources/ArgusServer/IB/IBAccountProvider.swift`, line ~208-213)

**Current Code:**
```swift
if marketData.last != 0 {
    let pnl = (enforceCurrency(marketData.last) - cost) * position.position
    position.formattedUnrealizedPnl = String(format: "%.2f", pnl)
    position.unrealizedPnl = pnl
    transmit(position: position)
}
```

**Fixed Code:**
```swift
if marketData.last != 0 {
    // Update market price from live data
    position.mktPrice = enforceCurrency(marketData.last)
    
    // Calculate market value
    position.mktValue = position.mktPrice * position.position
    
    // Calculate and update unrealized PnL
    let pnl = (position.mktPrice - cost) * position.position
    position.formattedUnrealizedPnl = String(format: "%.2f", pnl)
    position.unrealizedPnl = pnl
    
    transmit(position: position)
}
```

### Implementation Considerations

1. **Thread Safety:**
   - Python: `STK_Position` mutations happen within `_on_market_data()` which is already protected
   - Swift: `STKPosition` mutations happen within `onMarketData()` which is called from a single thread

2. **Data Consistency:**
   - Update all three values (`mkt_price`, `mkt_value`, `unrealized_pnl`) atomically before transmission
   - This ensures debug socket clients receive consistent snapshots

3. **Backward Compatibility:**
   - ✅ Changes are additive (fixing missing updates)
   - ✅ No API changes required
   - ✅ Existing functionality preserved

4. **Testing:**
   - Verify `mkt_price` updates with live market data
   - Verify `mkt_value` = `mkt_price * position`
   - Verify `unrealized_pnl` remains correct
   - Test with debug socket client to confirm display updates

## Alternative Approaches Considered

### Alternative 1: Keep Initial Approach, Add New Fields
**Approach:** Create `live_mkt_price` and `live_mkt_value` fields, keep original fields unchanged.

**Rejected Because:**
- Adds unnecessary complexity
- Confusing to have both "static" and "live" prices
- Doesn't solve the root problem: prices should update
- The original fields SHOULD reflect current market state

### Alternative 2: Only Update on Periodic Refresh
**Approach:** Keep live data updates only for PnL, refresh prices via periodic API polling.

**Rejected Because:**
- We already receive live price data (`data.last`)
- Wasteful to ignore live data we already have
- Defeats the purpose of websocket streaming
- Adds unnecessary API load

### Alternative 3: Transmit Prices Separately
**Approach:** Send price updates as separate messages from position updates.

**Rejected Because:**
- More complex protocol
- Doesn't solve the underlying issue
- Position data should be self-contained
- Complicates client-side logic

## Verification Plan

### Manual Testing

1. **Start Debug Socket Client:**
   ```bash
   python tests/debug_socket.py
   ```

2. **Observe Initial State:**
   - Note initial `Mkt Price` and `Mkt Value` for a position

3. **Wait for Market Movement:**
   - Watch for `Unrealized P&L` to change
   - Verify `Mkt Price` also changes (after fix)
   - Verify `Mkt Value` updates proportionally

4. **Verify Calculations:**
   - Check: `Mkt Value = Mkt Price × Qty`
   - Check: `Unrealized P&L = (Mkt Price - Avg Cost) × Qty`

### Automated Testing

If time permits, add unit tests:

```python
def test_market_data_updates_all_fields():
    """Test that market data updates price, value, and PnL."""
    position = create_test_position(
        avg_cost=100.0,
        position=10.0,
        mkt_price=100.0  # Initial
    )
    
    # Simulate market data with new price
    market_data = create_test_market_data(last=105.0)
    
    # Update position
    account_provider._on_market_data(market_data)
    
    # Verify all fields updated
    assert position.mkt_price == 105.0
    assert position.mkt_value == 1050.0  # 105 * 10
    assert position.unrealized_pnl == 50.0  # (105 - 100) * 10
```

## Timeline

- **Discovery:** 2025-12-19
- **Root Cause Confirmed:** 2025-12-20
- **Estimated Fix Time:** 1-2 hours (both Python and Swift)
- **Testing Time:** 1 hour
- **Total Estimated Time:** 2-3 hours

## References

- **Issue:** https://github.com/The-Sal/Argus/issues/41
- **Python Implementation:** `argus/ib/__init__.py` lines 707-728
- **Swift Implementation:** `argus_swift/Sources/ArgusServer/IB/IBAccountProvider.swift` lines 187-214
- **Debug Socket Client:** `tests/debug_socket.py`
- **Data Classes:**
  - Python: `STK_Position` in `argus/ib/_ib_utils.py` lines 376-509
  - Swift: `STKPosition` in `argus_swift/Sources/ArgusServer/IB/IBClasses.swift`

## Related Documentation

- **IB Architecture:** `argus_swift/ARCHITECTURE.md`
- **IB API Documentation:** `docs/IB.md`
- **AccountProvider Design:** See inline documentation in `argus/ib/__init__.py` lines 556-602

## Conclusion

This is a straightforward bug caused by incomplete field updates when processing live market data. The fix is simple: add two lines to update `mkt_price` and `mkt_value` alongside the existing `unrealized_pnl` update. The hypothesis from @The-Sal was accurate, and the solution is well-defined with minimal risk.

**Priority:** Medium - Fix should be implemented in next development cycle.

**Difficulty:** Easy - Clear root cause, simple fix, low risk.
