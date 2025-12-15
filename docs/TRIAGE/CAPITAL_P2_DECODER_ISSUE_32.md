# Triage: Capital.com P2 Decoder Usage in Client Message Handler (Issue #32)

**Issue Number:** #32  
**Repository:** The-Sal/Argus  
**Component:** `argus_swift/Sources/ArgusServer/Capital/CapitalComDispatcher.swift`  
**Line:** 377 (in `handleClientData` function)  
**Date:** 2025-12-15  
**Status:** ⚠️ CONFIRMED CRITICAL BUG  

---

## Executive Summary

**VERDICT: This is a REAL and CRITICAL bug.**

The Swift Capital.com dispatcher incorrectly uses `decodeMultiplePackets()` (a Protocol 2 decoder) to parse incoming client control messages that are sent using Protocol 1 (JSON format). This creates a complete protocol mismatch that prevents ANY client control message from being processed successfully.

**Severity:** 🔴 **CRITICAL** - Total breakage of bidirectional communication for Capital.com control messages  
**Impact:** No client can send control requests (subscribe, resolve symbols, etc.) to the Capital.com dispatcher  
**Workaround:** None - `fatalError()` prevents execution  

---

## Technical Analysis

### 1. The Problematic Code

**File:** `CapitalComDispatcher.swift`  
**Lines:** 374-393

```swift
private func handleClientData(_ data: Data, from client: ArgusSocket) {
    do {
        // Decode packets (may be multiple)
        let packets = try decodeMultiplePackets(data)  // ⚠️ WRONG: P2 decoder for P1 messages
        fatalError("DO NOT USE THIS MODULE, THERE IS A FATAL ERROR IN PACKET DECODING")
        for packetData in packets {
            // Parse JSON request
            guard let json = try? JSONSerialization.jsonObject(with: packetData) as? [String: Any],
                  let action = json["action"] as? String else {
                sendErrorResponse(to: client, message: "Invalid request format")
                continue
            }

            handleClientRequest(action: action, request: json, client: client)
        }
    } catch {
        print("Error decoding packet: \(error)")
        sendErrorResponse(to: client, message: "Invalid packet format")
    }
}
```

### 2. What `decodeMultiplePackets()` Does (Protocol 2)

**Source:** `Protocol2Utils.swift` lines 85-132

```swift
func decodeMultiplePackets(_ data: Data) throws -> [Data] {
    var packets: [Data] = []
    var position = 0

    while position < data.count {
        guard position + 6 <= data.count else {
            throw Protocol2Error.packetTooShort
        }

        let remaining = data[position...]

        // Check start marker
        guard remaining.first == 0x7E else {  // '~'
            throw Protocol2Error.missingStartMarker
        }

        // Parse length
        let lengthStart = position + 1
        let lengthEnd = lengthStart + 4
        let lengthBytes = data[lengthStart..<lengthEnd]

        guard let lengthStr = String(data: lengthBytes, encoding: .ascii),
              let length = Int(lengthStr) else {
            throw Protocol2Error.invalidLength
        }

        // Check pipe
        guard data[lengthEnd] == 0x7C else {  // '|'
            throw Protocol2Error.missingPipeSeparator
        }
        
        // ... continues to extract P2 packet
    }
    return packets
}
```

**Protocol 2 Format:**
```
~<4-digit-length><4-digit-symbol-length>|<symbol><csv-data>L
Example: ~00210004|BTCUSD50000.0,1.0,50001.0,1.0,50000.5,0.0,1234567890,1234567891L
```

This decoder expects:
- Position 0: `~` (0x7E)
- Position 1-4: 4 ASCII digits (packet length)
- Position 5: `|` (0x7C) **OR** 4 more digits (symbol length) for full P2
- Additional Protocol 2-specific structure

### 3. What Clients Actually Send (Protocol 1)

**Source:** Python `client.py` lines 64-69

```python
def send_dict(self, data: Dict[str, Any]):
    """Send a dictionary to the server using Protocol 1 framing."""
    if self.sock is None:
        raise RuntimeError("Socket is not connected. Call connect() first.")
    payload = json.dumps(data).encode('ascii')
    packet = encode_packet(payload)  # ⚠️ Protocol 1 encoding
    self.sock.sendall(packet)
```

**Protocol 1 Format (from `_svr_utils.py` lines 27-52):**
```
~<4-digit-length>|{json-data}
Example: ~0046|{"action":"resolve/stream","symbol":"BTCUSD"}
```

**Key Difference:**
- Protocol 1: Position 5 is ALWAYS `|` followed by JSON payload
- Protocol 2: Position 5 is a digit (part of symbol-length), and includes `L` terminator

### 4. Protocol Mismatch Illustration

**Client sends (P1):**
```
~0046|{"action":"resolve/stream","symbol":"BTCUSD"}
      ^
      Position 5: '|' (0x7C)
```

**Swift decoder expects (P2):**
```
~00210004|BTCUSD50000.0,1.0,50001.0,1.0L
      ^^^^
      Position 5-8: Should be 4 digits for symbol length
```

**What happens:**
1. Decoder reads position 5 and finds `|` (0x7C)
2. In Protocol 2 mode, it expects position 5-8 to be 4 ASCII digits
3. It reads `|{"}` which cannot be parsed as an integer
4. Throws `Protocol2Error.invalidLength`
5. Client gets "Invalid packet format" error response

---

## Evidence of Correct Implementation

### Python Implementation (CORRECT)

**File:** `argus/capital/__init__.py` lines 300-316

```python
def _on_recv(self, client: socket.socket, address: tuple, data: bytes):
    """Handles incoming data from a client."""
    logger.info(f"Received data from {address}: {data}")
    super()._on_recv(client, address, data)
    decoded_datas = decode_multiple_packets(data)  # ✅ P1 decoder
    logger.info(f"Decoded {len(decoded_datas)} packets from {address}.")
    for decoded_data in decoded_datas:
        if not decoded_data:
            print(f"Received empty or invalid packet from {address}.")
            return

        # Sample decoded_data structure:
        # {'action': 'resolve_symbol', 'symbol': 'BTCUSD'}
        data = json.loads(decoded_data.decode('ascii'))  # ✅ Parse JSON
        self.handle_client_request(data, client)
```

**Key:** Uses `decode_multiple_packets()` from `_svr_utils.py` which handles **BASIC Protocol 1** packets (lines 99-154), NOT Protocol 2 packets.

### Binance Swift Implementation (DIFFERENT BUT CORRECT)

**File:** `argus_swift/Sources/ArgusServer/Binance/MKTDispatcher.swift` lines 143-166

```swift
while true {
    do {
        guard let data = try realSocket.receive() else {
            break
        }

        guard let message = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
              !message.isEmpty else {
            continue
        }

        // Parse client commands
        if message.hasPrefix("add=") {
            let symbol = String(message.dropFirst(4)).uppercased()
            self.subscribeToSymbol(symbol: symbol, client: client)
        } else if message.hasPrefix("remove=") {
            let symbol = String(message.dropFirst(7)).uppercased()
            self.unsubscribeSymbol(symbol: symbol, client: client)
        }
    }
}
```

**Note:** Binance uses simple string commands (`add=SYMBOL`, `remove=SYMBOL`), not JSON packets. This is a different design but functionally correct.

### IB Swift Implementation (NO CLIENT COMMANDS)

**File:** `argus_swift/Sources/ArgusServer/IB/IBDispatcher.swift`

IB dispatcher does **NOT** accept client commands at all - clients can only receive Protocol 2 market data. There is no `handleClientData` equivalent.

---

## Protocol Usage Matrix

| Module | Client → Server (Control) | Server → Client (Data) | Notes |
|--------|---------------------------|------------------------|-------|
| **Python Capital** | Protocol 1 (JSON) | Protocol 2 (CSV) | ✅ Correct |
| **Swift Capital** | ❌ Uses P2 decoder on P1 | Protocol 2 (CSV) | 🔴 BUG |
| **Python Binance** | Simple text commands | Protocol 2 (CSV) | ✅ Different design |
| **Swift Binance** | Simple text commands | Protocol 2 (CSV) | ✅ Matches Python |
| **Python IB** | N/A (no commands) | Protocol 2 (CSV) | ✅ Correct |
| **Swift IB** | N/A (no commands) | Protocol 2 (CSV) | ✅ Matches Python |

---

## Why This Bug Exists

### Root Cause Analysis

1. **Copy-Paste from Wrong Module:** The Swift developer likely copied packet handling code from a module that receives Protocol 2 packets (market data from exchange)

2. **Confusion Between Protocols:**
   - Protocol 1: Client control messages (JSON) → Server
   - Protocol 2: Market data (CSV) → Client
   
3. **Misunderstanding of Capital.com Architecture:** Capital.com uses **dual protocols**:
   - **Inbound (client → server):** Protocol 1 JSON packets for control
   - **Outbound (server → client):** Protocol 2 CSV packets for market data

4. **No Testing:** This code has never been executed because of the `fatalError()` guard, indicating it was caught during development but never fixed

---

## Impact Assessment

### What Breaks

1. **All client control actions fail:**
   - `resolve_symbol` - Cannot look up symbols
   - `stream_epic` - Cannot subscribe to market data
   - `resolve/stream` - Cannot resolve and subscribe in one call
   - `unsubscribe` - Cannot unsubscribe from feeds

2. **Client-Server Communication:**
   - Clients cannot request any actions
   - Server cannot parse any client requests
   - All requests throw protocol parsing errors

3. **Market Data Streaming:**
   - Even though the server can authenticate and connect to Capital.com WebSocket
   - Clients cannot request subscriptions
   - No market data ever flows because subscriptions cannot be initiated

### What Still Works

1. **Server Initialization:** ✅ Server starts, authenticates with Capital.com API
2. **WebSocket Connection:** ✅ Server connects to Capital.com streaming API
3. **Interactive CLI:** ✅ Manual `add <epic>` commands work (bypass client handler)
4. **Market Data Transmission:** ✅ Once subscribed, server can send P2 packets to clients

---

## Comparison with Python Reference

### Python's Protocol 1 Decoder

**File:** `argus/capital/_svr_utils.py` lines 99-154

```python
def decode_multiple_packets(data: bytes) -> List[bytes]:
    """
    Decode multiple packets from a byte stream.
    
    Format: ~<data-length>|{data}
    """
    packets = []
    position = 0

    while position < len(data):
        remaining_data = data[position:]

        if not remaining_data.startswith(b"~"):
            raise ValueError(f"Invalid packet format at position {position}")

        if len(remaining_data) < 6:  # Minimum: ~0000|
            raise ValueError(f"Invalid packet format at position {position}: packet too short")

        # Parse length (P1: just 4 digits)
        try:
            length_str = int(remaining_data[1:5].decode('ascii'))
        except (ValueError, UnicodeDecodeError):
            raise ValueError(f"Invalid data length format at position {position}")

        # Check for pipe separator at position 5
        if remaining_data[5:6] != b"|":
            raise ValueError(f"Invalid packet format at position {position}: missing pipe separator")

        # Extract packet data
        packet_end = 6 + length_str
        packet_data = remaining_data[6:packet_end]
        packets.append(packet_data)

        position += packet_end

    return packets
```

**Key:** This is a **basic Protocol 1 decoder** that ONLY handles `~LLLL|{data}` format, NOT Protocol 2's `~LLLL<NNNN|>...L` format.

---

## Recommended Fix

### Option 1: Use Basic Packet Decoder (Recommended)

Create a Protocol 1 decoder in `Protocol2Utils.swift` or use JSON parsing directly:

```swift
private func handleClientData(_ data: Data, from client: ArgusSocket) {
    do {
        // Decode Protocol 1 packets (basic format: ~LLLL|{json})
        let packets = try decodeBasicPackets(data)  // ✅ New P1 decoder
        
        for packetData in packets {
            // Parse JSON request
            guard let json = try? JSONSerialization.jsonObject(with: packetData) as? [String: Any],
                  let action = json["action"] as? String else {
                sendErrorResponse(to: client, message: "Invalid request format")
                continue
            }

            handleClientRequest(action: action, request: json, client: client)
        }
    } catch {
        print("Error decoding packet: \(error)")
        sendErrorResponse(to: client, message: "Invalid packet format")
    }
}

// Add to Protocol2Utils.swift
func decodeBasicPackets(_ data: Data) throws -> [Data] {
    var packets: [Data] = []
    var position = 0

    while position < data.count {
        guard position + 6 <= data.count else {
            throw Protocol2Error.packetTooShort
        }

        let remaining = data[position...]

        // Check start marker
        guard remaining.first == 0x7E else {  // '~'
            throw Protocol2Error.missingStartMarker
        }

        // Parse length (4 ASCII digits)
        let lengthStart = position + 1
        let lengthEnd = lengthStart + 4
        let lengthBytes = data[lengthStart..<lengthEnd]

        guard let lengthStr = String(data: lengthBytes, encoding: .ascii),
              let length = Int(lengthStr) else {
            throw Protocol2Error.invalidLength
        }

        // Check pipe separator at position 5
        guard data[lengthEnd] == 0x7C else {  // '|'
            throw Protocol2Error.missingPipeSeparator
        }

        // Extract data payload
        let dataStart = lengthEnd + 1
        let dataEnd = dataStart + length

        guard dataEnd <= data.count else {
            throw Protocol2Error.dataLengthMismatch
        }

        let packetData = data[dataStart..<dataEnd]
        packets.append(packetData)

        // Move to next packet
        position = dataEnd
    }

    return packets
}
```

### Option 2: Use Binance-Style Simple Commands

Switch to simple text commands like Binance:

```swift
private func handleClientData(_ data: Data, from client: ArgusSocket) {
    guard let message = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
          !message.isEmpty else {
        return
    }
    
    // Parse JSON command
    guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let action = json["action"] as? String else {
        sendErrorResponse(to: client, message: "Invalid request format")
        return
    }
    
    handleClientRequest(action: action, request: json, client: client)
}
```

**Note:** This option requires changing the client protocol, which breaks compatibility with Python clients.

---

## Testing Strategy

### Unit Tests Needed

1. **Protocol 1 Packet Decoding:**
```swift
func testDecodeBasicPacket() {
    let json = "{\"action\":\"resolve/stream\",\"symbol\":\"BTCUSD\"}"
    let jsonData = json.data(using: .ascii)!
    let packet = try! encodePacket(jsonData)
    
    let decoded = try! decodeBasicPackets(packet)
    XCTAssertEqual(decoded.count, 1)
    XCTAssertEqual(decoded[0], jsonData)
}

func testDecodeMultipleBasicPackets() {
    let json1 = "{\"action\":\"resolve_symbol\",\"symbol\":\"AAPL\"}"
    let json2 = "{\"action\":\"stream_epic\",\"epic\":\"BTCUSD\"}"
    
    let packet1 = try! encodePacket(json1.data(using: .ascii)!)
    let packet2 = try! encodePacket(json2.data(using: .ascii)!)
    
    var combined = Data()
    combined.append(packet1)
    combined.append(packet2)
    
    let decoded = try! decodeBasicPackets(combined)
    XCTAssertEqual(decoded.count, 2)
}
```

2. **Client Request Handling:**
```swift
func testHandleResolveSymbolRequest() {
    let request: [String: Any] = [
        "action": "resolve_symbol",
        "symbol": "BTCUSD"
    ]
    
    let jsonData = try! JSONSerialization.data(withJSONObject: request)
    let packet = try! encodePacket(jsonData)
    
    // Send to server and verify response
    // (requires integration test with mock client)
}
```

3. **Protocol Differentiation:**
```swift
func testDifferentiateP1FromP2() {
    // P1 packet: ~LLLL|{json}
    let p1 = "~0010|{\"test\":1}".data(using: .ascii)!
    
    // P2 packet: ~LLLL<NNNN|>...L
    let p2 = "~00210004|BTCUSD50000.0,1.0L".data(using: .ascii)!
    
    // Verify P1 decoder works on P1
    XCTAssertNoThrow(try decodeBasicPackets(p1))
    
    // Verify P2 decoder works on P2
    XCTAssertNoThrow(try decodeMultiplePackets(p2))
    
    // Verify P1 decoder fails on P2
    XCTAssertThrowsError(try decodeBasicPackets(p2))
    
    // Verify P2 decoder fails on P1
    XCTAssertThrowsError(try decodeMultiplePackets(p1))
}
```

### Integration Tests

1. Connect Python `CapitalComClient` to Swift server
2. Send `resolve/stream` request
3. Verify subscription succeeds and market data flows
4. Send `unsubscribe` request
5. Verify market data stops

---

## Related Code References

### Python Files
- `argus/capital/__init__.py` lines 300-432 - MKTDispatcher with correct P1 handling
- `argus/capital/_svr_utils.py` lines 27-154 - Protocol 1 encoding/decoding
- `argus/capital/_svr_utils.py` lines 161-401 - Protocol 2 encoding/decoding
- `argus/capital/client.py` lines 64-69 - Client sends P1 packets
- `argus/capital/client.py` lines 141-148 - Client receives mixed P1/P2 packets

### Swift Files
- `argus_swift/Sources/ArgusServer/Capital/CapitalComDispatcher.swift` lines 374-393 - **THE BUG**
- `argus_swift/Sources/ArgusServer/Utils/Protocol2Utils.swift` lines 29-82 - Basic packet encoder/decoder
- `argus_swift/Sources/ArgusServer/Utils/Protocol2Utils.swift` lines 85-132 - Protocol 2 decoder (wrong one used)

### Documentation
- `docs/CAPITAL.md` lines 100-178 - Documents P1 for control, P2 for data
- `docs/CAPITAL.md` lines 422-519 - Protocol comparison table
- `argus_swift/ARCHITECTURE.md` lines 941-944 - Capital.com uses Protocol 2

---

## Conclusion

**This is NOT gibberish. This is a REAL, CRITICAL bug.**

### Confirmed Facts:

1. ✅ Swift code uses Protocol 2 decoder (`decodeMultiplePackets`) for client messages
2. ✅ Python reference implementation uses Protocol 1 decoder (`decode_multiple_packets`) for client messages
3. ✅ Python clients send Protocol 1 JSON packets for control messages
4. ✅ Protocol 1 and Protocol 2 have incompatible framing
5. ✅ The code has a `fatalError()` preventing execution, proving the developer knew it was broken
6. ✅ No other dispatcher (Binance, IB) uses this pattern

### Severity Justification:

- **Critical:** Complete breakage of client-server control communication
- **High Impact:** No client can interact with the Capital.com dispatcher
- **Zero Workaround:** The `fatalError()` makes the code unusable
- **Design Flaw:** Fundamental misunderstanding of the dual-protocol architecture

### Recommended Actions:

1. **Immediate:** Remove `fatalError()` only after implementing fix
2. **Priority 1:** Implement Protocol 1 basic packet decoder
3. **Priority 2:** Add comprehensive unit tests for both protocols
4. **Priority 3:** Add integration test with Python client
5. **Documentation:** Update Swift architecture docs to clarify protocol usage

### Estimated Fix Complexity:

- **Code Changes:** Low (add one function, change one line)
- **Testing Required:** Medium (needs integration testing with Python client)
- **Risk:** Low (fix is straightforward, pattern is proven in Python)

---

**Status:** ⚠️ **CONFIRMED - AWAITING FIX**  
**Next Step:** Implement Protocol 1 decoder and update `handleClientData` to use it  
**Blockers:** None  
**Dependencies:** None
