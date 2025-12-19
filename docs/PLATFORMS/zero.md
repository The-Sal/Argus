# Argus Package Installation Guide for Raspberry Pi Zero

## Overview
The Argus package cannot be installed directly on the Raspberry Pi Zero using standard methods due to the computational overhead required to build dependencies, particularly pandas. Follow these specific instructions for proper installation.

## Installation Steps

### 1. Install pandas from apt
First, install pandas using the system package manager to avoid the lengthy build process:
```bash
sudo apt-get install python3-pandas
```

### 2. Install remaining dependencies using system pip3
Install the following packages using the **system pip3** (not from requirements.txt):

```bash
pip3 install tqdm~=4.66.6
pip3 install websocket-client~=1.8.0
pip3 install python-dotenv~=1.0.1
pip3 install selenium~=4.28.1
pip3 install websockets~=12.0
pip3 install requests~=2.32.4
pip3 install setuptools~=80.9.0
pip3 install git+https://github.com/the-sal/utils3
pip3 install termcolor
```

### 3. Verify installation
Test that everything is correctly installed:
```bash
python3 run_tests.py
```

Example:
```
$ python3 runtime.py binance --host 0.0.0.0 --port 9982
2025-11-21 03:31:14,798 - INFO - utils - _init_num_threads - NumExpr defaulting to 4 threads.
Warning: .env was not loaded
Argus: <module 'argus' from '/home/pi/Argus/argus/__init__.py'>
Running on Linux
Arguments: ['runtime.py', 'binance', '--host', '0.0.0.0', '--port', '9982']
2025-11-21 03:31:17,077 - INFO - _logging - info - Websocket connected
WebSocket connection opened.
Show me charts disabled: not running on macOS
[BinanceWss] Initialized with UUID: 546fe32d-8aff-4316-86ec-f1c4976f3fd3
[BinanceMKTDispatcher] Initialized on 0.0.0.0:9982
[IMPORTANT] MODE = PROTOCOL_2
[STATISTICS] Received 0 messages in the last 10 seconds (avg: 0.00 msgs/sec)
CLIENT] New connection from ('100.122.56.36', 62986)
[CLIENT] Subscribing to BTCUSDT
[SUBSCRIBE] New subscription to BTCUSDT
[CLIENT] Added client to BTCUSDT subscription (total: 1)
Malformed message received: {'result': None, 'id': 'SUBSCRIBE', 'received_at': 1763695897.5041015}
[STATISTICS] Received 202 messages in the last 10 seconds (avg: 20.20 msgs/sec)
[AUTO-DUMP] Dumped 231 messages to binance_wss_dump_546fe32d-8aff-4316-86ec-f1c4976f3fd3-0.json
[STATISTICS] Received 395 messages in the last 10 seconds (avg: 39.50 msgs/sec)
[STATISTICS] Received 450 messages in the last 10 seconds (avg: 45.00 msgs/sec)
[STATISTICS] Received 245 messages in the last 10 seconds (avg: 24.50 msgs/sec)
[AUTO-DUMP] Dumped 1516 messages to binance_wss_dump_546fe32d-8aff-4316-86ec-f1c4976f3fd3-0.json
```

MacOS client side (using tests/test_binance_proc_2.py)
```
{'symbol': 'BTCUSDT', 'bid': 85897.42, 'bid_size': 2.32526, 'ask': 85897.43, 'ask_size': 0.84626, 'last': 85897.42, 'last_size': 0.60458, 'timestamp': 1763697566401.0, 'transmission_time': 1763697566.5187287}
Since Timestamp: 0.004332304000854492
{'symbol': 'BTCUSDT', 'bid': 85897.42, 'bid_size': 1.24067, 'ask': 85897.43, 'ask_size': 0.92384, 'last': 85897.42, 'last_size': 0.60458, 'timestamp': 1763697566414.0, 'transmission_time': 1763697566.5377285}
Since Timestamp: 0.004187345504760742
{'symbol': 'BTCUSDT', 'bid': 85897.42, 'bid_size': 1.24079, 'ask': 85897.43, 'ask_size': 0.97146, 'last': 85897.42, 'last_size': 0.60458, 'timestamp': 1763697566514.0, 'transmission_time': 1763697566.6362765}
Since Timestamp: 0.004461526870727539
{'symbol': 'BTCUSDT', 'bid': 85897.42, 'bid_size': 1.24079, 'ask': 85897.43, 'ask_size': 0.97146, 'last': 85897.43, 'last_size': 6e-05, 'timestamp': 1763697566604.0, 'transmission_time': 1763697566.7215726}
Since Timestamp: 0.0046422481536865234
{'symbol': 'BTCUSDT', 'bid': 85897.42, 'bid_size': 1.53352, 'ask': 85897.43, 'ask_size': 0.63146, 'last': 85897.43, 'last_size': 6e-05, 'timestamp': 1763697566614.0, 'transmission_time': 1763697566.7354193}
Since Timestamp: 0.00407862663269043
{'symbol': 'BTCUSDT', 'bid': 85897.42, 'bid_size': 1.63948, 'ask': 85897.43, 'ask_size': 0.92402, 'last': 85897.43, 'last_size': 6e-05, 'timestamp': 1763697566714.0, 'transmission_time': 1763697566.8336105}
Since Timestamp: 0.004607439041137695
{'symbol': 'BTCUSDT', 'bid': 85897.42, 'bid_size': 1.24067, 'ask': 85897.43, 'ask_size': 1.05389, 'last': 85897.43, 'last_size': 6e-05, 'timestamp': 1763697566814.0, 'transmission_time': 1763697566.9374554}
Since Timestamp: 0.00505375862121582
```


## Notes
### Daemon Process Interference
**Running this package may cause other daemon processes to stop working.** Depending on which module you call after `runtime.py`, the application is extremely thread-heavy and may consume system resources that interfere with background services.

### Package Manager Restrictions
**DO NOT use the following package managers:**
- `uv`
- `uvx`
- `pipenv`
- These tools attempt to build packages from source, which the Raspberry Pi Zero cannot handle in a reasonable timeframe. While technically possible, the build process would take an impractically long time.
- Always use system pip3 (`pip3`) for installation
- Do not attempt to install from `requirements.txt`
- The Pi Zero's limited processing power makes source builds prohibitively slow
- Monitor system resources when running thread-heavy modules

### Performance Considerations
- Modules such as binance have threads that often dump data to disk every few seconds this is not ideal for the Pi's SD card longevity. Consider disabling these features which can be done my modifying the config during construction
- The Raspberry Pi Zero has limited CPU and memory resources; performance may vary based on workload and system load.
- Not tested with multiple clients connected simultaneously; performance may degrade with multiple connections.
- Does work with the MacOS test client provided in `tests/test_binance_proc_2.py` connected through tailscale.
- The Pi Zero creates a significant amount of corrupted packets, these don't pass the P2-decoders integrity checks so they do not affect the client, but you will often drop 1–5 messages, it's unique bug which no one understands for some reason the code that counts the bytes of the packet sometimes miscalculates the length? which the p2-decoder then sees as a corrupted packet.