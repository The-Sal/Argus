# Concurrent Request Processing Enhancement

## Problem Statement

The original dispatcher architecture had a significant limitation:
- Each client connection had a dedicated thread
- Within each thread, requests were processed synchronously
- This meant **only 1 request could be in flight per client** at any time
- The thread would block waiting for a request to complete before reading the next buffer

This effectively limited throughput per client, even though the underlying infrastructure (REST APIs, database queries, etc.) could handle concurrent operations.

## Solution Overview

We've implemented asynchronous packet processing to allow **multiple requests in flight from the same client simultaneously**. The key changes are:

1. **Per-socket write locks**: Each socket has its own `threading.Lock` to ensure thread-safe writing
2. **Asynchronous packet processing**: Each packet is processed in its own thread via `@runAsThread`
3. **Thread-safe socket management**: All socket operations are protected by appropriate locks

## Implementation Details

### Polymarket Dispatcher (`argus/polymarket/__init__.py`)

**Changes to `RoutingHelper` class:**
```python
# Added per-socket write locks
self._socket_write_locks: dict[socket.socket, threading.Lock] = {}

# Modified add_socket to create locks
def add_socket(self, sock: socket.socket):
    with self._lock:
        self._sockets.add(sock)
        if sock not in self._socket_write_locks:
            self._socket_write_locks[sock] = threading.Lock()

# Modified remove_socket to clean up locks
def remove_socket(self, sock: socket.socket):
    with self._lock:
        self._sockets.discard(sock)
        self._socket_write_locks.pop(sock, None)
        # ... rest of cleanup

# Added helper method
def get_socket_write_lock(self, sock: socket.socket) -> threading.Lock:
    with self._lock:
        if sock not in self._socket_write_locks:
            self._socket_write_locks[sock] = threading.Lock()
        return self._socket_write_locks[sock]
```

**Changes to packet processing:**
```python
def _handle_incoming_packets(self, client_socket: socket.socket, address, data: bytes):
    """
    Handle incoming packets from a client. Each packet is processed asynchronously
    in its own thread to allow multiple requests in flight from the same client.
    """
    packets = decode_multiple_packets(data)
    for packet in packets:
        # Process each packet in a separate thread to allow concurrent requests
        self._process_single_packet_async(client_socket, address, packet)

@runAsThread
def _process_single_packet_async(self, client_socket: socket.socket, address, packet: bytes):
    """
    Process a single packet asynchronously. This allows multiple requests from the
    same client to be in flight simultaneously.
    """
    try:
        content = json.loads(packet.decode('utf-8'))
        logging.debug("Received data from Polymarket client: %s", content)
        # ... process request ...
        
        response_bytes = encode_packet(json.dumps(msg).encode('utf-8'))
        
        # Use per-socket write lock to ensure thread-safe writing
        write_lock = self.get_socket_write_lock(client_socket)
        with write_lock:
            client_socket.sendall(response_bytes)
    except Exception as e:
        logging.error("Error processing packet: %s", e)
```

**All socket writes now use locks:**
- `_process_single_packet_async`: Response to client request
- `_on_fatal_error`: Broadcasting fatal errors
- `_order_book_update_callback`: Broadcasting market data
- `_account_update_callback`: Broadcasting account updates

### Capital.com Dispatcher (`argus/capital/__init__.py`)

**Changes to `SvrExport` class:**
```python
# Added per-client write locks and client list lock
self._client_write_locks = {}
self._client_list_lock = threading.Lock()

# Modified _on_recv to safely register clients
def _on_recv(self, client: socket.socket, address: tuple, data: bytes):
    self.packets_read += 1
    with self._client_list_lock:
        if (client, address) not in self.client_list:
            self.client_list.append((client, address))
        if client not in self._client_write_locks:
            self._client_write_locks[client] = threading.Lock()
    return

# Added helper method
def get_client_write_lock(self, client: socket.socket) -> threading.Lock:
    with self._client_list_lock:
        if client not in self._client_write_locks:
            self._client_write_locks[client] = threading.Lock()
        return self._client_write_locks[client]
```

**Changes to `MKTDispatcher`:**
```python
def _on_recv(self, client: socket.socket, address: tuple, data: bytes):
    """
    Handles incoming data from a client.
    Each packet is processed asynchronously to allow multiple requests in flight.
    """
    logger.info(f"Received data from {address}: {data}")
    super()._on_recv(client, address, data)
    decoded_datas = decode_multiple_packets(data)
    logger.info(f"Decoded {len(decoded_datas)} packets from {address}.")
    for decoded_data in decoded_datas:
        # Process each packet in a separate thread to allow concurrent requests
        self._process_packet_async(client, address, decoded_data)

@runAsThread
def _process_packet_async(self, client: socket.socket, address: tuple, decoded_data: bytes):
    """
    Process a single packet asynchronously.
    """
    try:
        if not decoded_data:
            print(f"Received empty or invalid packet from {address}.")
            return
        data = json.loads(decoded_data.decode('ascii'))
        self.handle_client_request(data, client)
    except Exception as e:
        logger.error(f"Error processing packet from {address}: {e}")
```

**All socket writes now use locks:**
- `transmit_mkt_data_with_protocol_1`: Broadcasting to all clients
- `transmit_mkt_data_with_protocol_2`: Broadcasting market data
- `handle_client_request`: Sending responses to specific clients

## Performance Impact

### Before (Sequential Processing)
- Client sends 3 requests (A, B, C) that each take 100ms to process
- Total time: 300ms (A finishes, then B starts, then C starts)
- Throughput: 3.33 requests/second per client

### After (Concurrent Processing)
- Client sends 3 requests (A, B, C) that each take 100ms to process
- All three start processing immediately
- Total time: ~100ms (all three complete around the same time)
- Throughput: 30 requests/second per client (in this scenario)

**Real-world performance gains:**
- For lightweight operations (ping, cache lookups): minimal improvement since they're already fast
- For heavy operations (REST API calls, database queries, complex calculations): **significant improvement**
- Theoretical speedup: Up to N× where N is the number of concurrent requests (limited by server resources)

## Volume Considerations

The question in the problem statement was: "what level of volume would that be fine?"

**Answer:**

With `@runAsThread` (which creates a new thread per packet):
- **Light load (1-10 clients, <100 req/sec)**: Perfectly fine ✅
- **Medium load (10-50 clients, 100-1000 req/sec)**: Should be fine, but monitor thread count ✅
- **Heavy load (>50 clients, >1000 req/sec)**: May hit thread limits ⚠️

**Thread overhead considerations:**
- Each thread has ~8KB stack overhead (default on Linux)
- Thread creation/destruction has cost
- OS has limits (typically 10k-30k threads on Linux)

**When you might need async/await instead:**
- If you see thread exhaustion (lots of threads waiting)
- If you need to handle >10,000 concurrent requests
- If memory usage becomes an issue

**For typical trading/market data use cases:**
- This solution is sufficient for most scenarios
- Most clients won't send thousands of concurrent requests
- The threading overhead is acceptable for the volume

## Testing

A test file `tests/test_concurrent_requests.py` has been created to verify concurrent request handling:

```python
# Test 1: Send 10 concurrent pings from same socket
# Test 2: Send mixed requests (ping, get_balance, search_markets) concurrently
```

Run the test with:
```bash
# Start the Polymarket dispatcher first
python -m argus.polymarket

# In another terminal
pipenv run python tests/test_concurrent_requests.py
```

## Thread Safety Guarantees

All modifications ensure thread safety through:

1. **Per-socket write locks**: Prevent race conditions when multiple threads write to same socket
2. **Routing table locks**: Existing `self._lock` protects routing table modifications
3. **Client list locks**: New `_client_list_lock` protects client list in Capital dispatcher
4. **Atomic operations**: Socket operations are atomic at OS level when protected by locks

## Backward Compatibility

These changes are **100% backward compatible**:
- No API changes
- No protocol changes
- Existing clients continue to work exactly as before
- The only difference is that clients can now send multiple concurrent requests

## Future Considerations

If you need to scale beyond the threading model:

1. **Thread pool**: Replace `@runAsThread` with a thread pool (e.g., `ThreadPoolExecutor`)
   - Limits maximum concurrent threads
   - Reuses threads, reducing overhead
   - Simple drop-in replacement

2. **Async/await**: Full rewrite to use `asyncio`
   - Best for very high concurrency (10k+ connections)
   - Lower memory footprint
   - More complex to implement
   - Requires async versions of all blocking operations

3. **Process pool**: For CPU-bound operations
   - Better for heavy computation
   - Higher overhead than threads
   - Better CPU utilization on multi-core systems

For now, the threading approach is the right balance between:
- Implementation complexity (low)
- Performance improvement (high for typical use cases)
- Code maintainability (high)
