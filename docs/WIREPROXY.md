# WireProxy System Architecture

## Overview

WireProxy is Argus's integration layer for running dispatchers through WireGuard VPN connections. It manages a userspace WireGuard implementation ([wireproxy](https://github.com/whyvl/wireproxy)) that provides SOCKS5 proxy functionality.

## System Components

```
┌─────────────────────────────────────────────────────────────────┐
│                         User Layer                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  CLI Commands:                                                  │
│  • python -m argus.wireproxy --add-conf <path>                  │
│  • python -m argus.wireproxy --start-server <config>            │
│  • python -m argus.wireproxy --run-server-daemon                │
│  • python -m argus.wireproxy (interactive mode)                 │
│                                                                 │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                    WireProxy Class                              │
│                 (Configuration Manager)                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  • add_conf(path) - Add WG config                               │
│  • remove_conf(name) - Remove config                            │
│  • bulk_import(dir) - Import multiple configs                   │
│  • list_confs() - List available configs                        │
│                                                                 │
│  Delegates server ops to daemon via socket                      │
│                                                                 │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         │ Socket communication (TCP)
                         │ 127.0.0.1:23888
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                WireProxyServer Daemon                           │
│                 (Background Service)                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Socket API (CMD:ARG1,ARG2\n):                                  │
│  • spin_up:<conf_name> - Start WireProxy process                │
│  • spin_down: - Stop WireProxy process                          │
│  • state: - Get current status                                  │
│  • available_confs: - List available configs                    │
│                                                                 │
│  Manages:                                                       │
│  • subprocess.Popen for WireProxy binary                        │
│  • Log file handles                                             │
│  • Process lifecycle                                            │
│                                                                 │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         │ subprocess.Popen
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│              WireProxy Binary Process                           │
│          (Userspace WireGuard + SOCKS5)                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Runs: wireproxy -c <config.conf>                               │
│  Creates SOCKS5 proxy: 127.0.0.1:25344                          │
│                                                                 │
│  Logs to: ~/.argus/wp-server-logs/<timestamp>_<conf>.log        │
│                                                                 │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         │ WireGuard VPN tunnel
                         ▼
                   [Remote WG Server]
```

## Directory Structure

All WireProxy assets are stored in `~/.argus/`:

```
~/.argus/
├── wireproxy/
│   └── wireproxy              # Binary executable
├── wireproxy_confs/
│   ├── config1.conf           # WG configs with [Socks5] section
│   ├── config2.conf
│   └── ...
└── wp-server-logs/
    ├── 1234567890_config1.log # Timestamped logs
    └── ...
```

## Daemon vs Server: Key Distinction

### `--run-server-daemon`
Starts the **WireProxyServer daemon** itself. This is the control plane.

```
┌──────────────────────────────────────────────────────────────┐
│  python -m argus.wireproxy --run-server-daemon               │
└─────────────────────┬────────────────────────────────────────┘
                      │
                      ▼
              ┌───────────────┐
              │  Daemon runs  │
              │  in FOREGROUND│
              │  (Ctrl+C to   │
              │   stop)       │
              └───────────────┘
                      │
                      │ Listens on 127.0.0.1:23888
                      │ Accepts socket commands
                      │ Does NOT start WireProxy yet
```

The daemon is the persistent background service that:
- Listens on TCP port 23888
- Accepts commands via socket protocol
- Manages WireProxy process lifecycle
- Runs until explicitly killed

### `--start-server <config>`
Sends a command TO the daemon to spin up WireProxy. This is a client operation.

```
┌──────────────────────────────────────────────────────────────┐
│  python -m argus.wireproxy --start-server myconfig           │
└─────────────────────┬────────────────────────────────────────┘
                      │
                      │ 1. Check if daemon running
                      │    (connect to 127.0.0.1:23888)
                      │
                      ▼
              ┌───────────────┐
              │  If not       │
              │  running,     │
              │  auto-start   │
              │  daemon       │──┐
              └───────────────┘  │
                      │          │ subprocess.Popen detached
                      │          │ (start_new_session=True)
                      │          ▼
                      │    ┌─────────────┐
                      │    │   Daemon    │
                      │    │   starts    │
                      │    │   in BG     │
                      │    └─────────────┘
                      │           │
                      └───────────┘
                      │
                      │ 2. Send: "spin_up:myconfig\n"
                      │    via socket to daemon
                      ▼
              ┌───────────────┐
              │  Daemon spawns│
              │  WireProxy    │
              │  subprocess   │
              └───────────────┘
                      │
                      ▼
            [WireProxy running]
            SOCKS5: 127.0.0.1:25344
```

## Lifecycle Flow

### Starting WireProxy the First Time

```
User runs: python -m argus.wireproxy --start-server myconfig
     │
     ├─► Check daemon running? (port 23888 open?)
     │        │
     │        ├─► NO ─► Start daemon (detached background process)
     │        │              │
     │        │              └─► Wait 1 second, verify started
     │        │
     │        └─► YES ─► Continue
     │
     └─► Send socket command: "spin_up:myconfig\n"
              │
              ▼
         Daemon receives command
              │
              ├─► Validate config exists in wireproxy_confs/
              ├─► Create log file: <timestamp>_myconfig.log
              ├─► subprocess.Popen: wireproxy -c <path>
              ├─► Store process handle, PID, log handle
              └─► Return: {"status": "running", "pid": 12345, ...}
              │
              ▼
         WireProxy binary runs
              │
              └─► SOCKS5 proxy active on 127.0.0.1:25344
```

### Stopping WireProxy

```
User runs: python -m argus.wireproxy --stop-server
     │
     └─► Send socket command: "spin_down:\n"
              │
              ▼
         Daemon receives command
              │
              ├─► proc.terminate() (SIGTERM)
              ├─► Wait 5 seconds
              ├─► If still alive: proc.kill() (SIGKILL)
              ├─► Close log file handle
              ├─► Clear state (proc=None, current_conf=None)
              └─► Return: {"status": "stopped", ...}
```

### Checking Status

```
User runs: python -m argus.wireproxy --server-status
     │
     ├─► Check daemon running? (port 23888 open?)
     │        │
     │        ├─► NO ─► "Daemon NOT running"
     │        │
     │        └─► YES ─► Send: "state:\n"
     │                      │
     │                      ▼
     │                  Daemon checks proc.poll()
     │                      │
     │                      ├─► None (running)
     │                      │    └─► Return: {"running": true, "config": "...", "pid": ...}
     │                      │
     │                      └─► Not None (dead)
     │                           └─► Return: {"running": false, ...}
```

## Socket Protocol

The daemon uses a simple text-based protocol over TCP:

**Request Format:**
```
CMD:ARG1,ARG2,...\n
```

**Response Format:**
```json
{
  "CMD": "echo of command",
  "result": {...},
  "error": null | "error message"
}
```

**Available Commands:**

| Command | Args | Description |
|---------|------|-------------|
| `spin_up` | `conf_name` | Start WireProxy with config |
| `spin_down` | none | Stop running WireProxy |
| `state` | none | Get current WireProxy status |
| `available_confs` | none | List available configs |

**Example Session:**
```
> spin_up:myconfig.conf\n
< {"CMD": "spin_up", "result": {"status": "running", "pid": 12345}, "error": null}

> state:\n
< {"CMD": "state", "result": {"running": true, "config": "myconfig.conf"}, "error": null}

> spin_down:\n
< {"CMD": "spin_down", "result": {"status": "stopped"}, "error": null}
```

## Binary Download & Management

WireProxy binary is downloaded automatically on first use:

```
WireProxyManagement.__init__()
     │
     └─► Check: ~/.argus/wireproxy/wireproxy exists?
              │
              ├─► YES ─► Continue
              │
              └─► NO ─► update_wireproxy()
                          │
                          ├─► Detect OS: platform.system() → "Darwin", "Linux", "Windows"
                          ├─► Detect Arch: platform.machine() → "x86_64", "arm64", etc.
                          ├─► Build URL: https://github.com/whyvl/wireproxy/releases/latest/download/
                          │               wireproxy_{OS}_{ARCH}.tar.gz
                          ├─► Download: _utils.download(url) with progress bar
                          ├─► Extract: tar -xzf
                          ├─► Copy: cp wireproxy ~/.argus/wireproxy/
                          └─► Verify: wireproxy -v
```

**Download implementation** (`_utils.py:download`):
- Uses `requests` with streaming
- Shows progress bar via `tqdm`
- Extracts filename from `Content-Disposition` header or URL
- Returns `(filename, response)` tuple

## Configuration Processing

WireGuard configs are automatically modified when added:

```
User adds config (without [Socks5] section):
     │
     ├─► Read file: /path/to/user.conf
     │        │
     │        │   [Interface]
     │        │   PrivateKey = ...
     │        │   Address = 10.0.0.2/24
     │        │   DNS = 1.1.1.1
     │        │
     │        │   [Peer]
     │        │   PublicKey = ...
     │        │   AllowedIPs = 0.0.0.0/0
     │        │   Endpoint = vpn.example.com:51820
     │        │
     │        ▼
     │   Check for [Socks5] section
     │        │
     │        ├─► Not found ─► Append:
     │        │                    [Socks5]
     │        │                    BindAddress = 127.0.0.1:25344
     │        │
     │        └─► Already exists ─► Keep as-is
     │
     └─► Save to: ~/.argus/wireproxy_confs/<name>.conf
```

This ensures all configs create a SOCKS5 proxy on the standard port.

## Logging

Each WireProxy session generates a detailed log:

**Log File Naming:**
```
<unix_timestamp>_<config_name>.log
Example: 1704067200_myconfig.log
```

**Log File Contents:**
```
================================================================================
WireProxy Server Log
================================================================================
Start Time: 2024-01-01 00:00:00
Unix Timestamp: 1704067200
Configuration: myconfig.conf
WireProxy Version: wireproxy version X.Y.Z
Configuration File: /Users/user/.argus/wireproxy_confs/myconfig.conf

Process Output:
================================================================================
[WireProxy stdout/stderr follows...]

================================================================================
WireProxy Server Teardown
================================================================================
Stop Time: 2024-01-01 01:00:00
Unix Timestamp: 1704070800
Status: Initiating shutdown
Shutdown Method: Graceful termination
Final Status: Process terminated
================================================================================
End of log
================================================================================
```

Logs use line buffering (`buffering=1`) for real-time writes.

## Error Handling

### Daemon Not Running
When `--start-server` or `--stop-server` is called but daemon isn't running:
1. Client detects no connection on port 23888
2. Auto-starts daemon via `subprocess.Popen` with `start_new_session=True` (detaches)
3. Waits 1 second for startup
4. Retries operation

### WireProxy Fails to Start
If `wireproxy` binary exits immediately after spawn:
1. `proc.poll()` returns non-None after 0.5s sleep
2. Daemon reads log file to capture error
3. Returns error response with log path
4. Cleans up: closes log handle, sets `proc=None`

### Config Not Found
If user requests non-existent config:
```python
raise Exception(f'Configuration not found: {conf_name}')
```
Socket response: `{"error": "Configuration not found: badconfig"}`

## Integration with Argus Dispatchers

The `.env` configuration determines which config is used for specific dispatchers. Moreover,
the Argus dispatcher automatically spinup both the WireProxy Daemon (if offline) and then makes sure
you are connecting to the right config for the respective dispatcher you are running. There is no need
to manually do anything via the CLI if you are using WireProxy with the Argus Dispatchers besides 
adding the config to the library with `--add-conf` or `--bulk-import`.

## Threading Model

```
Main Process
     │
     ├─► WireProxyServer.__init__()
     │        └─► self.svr = Server(...)  # Socket server setup
     │
     └─► WireProxyServer.run_server()
              │
              └─► @runAsThread decorator
                       │
                       └─► Thread: self.svr.start()
                                    │
                                    └─► Listens on 127.0.0.1:23888
                                              │
                                              └─► For each connection:
                                                       │
                                                       └─► self._recv(client, addr, data)
                                                                │
                                                                └─► Parse command
                                                                └─► Execute: spin_up/spin_down/state/etc
```

The daemon uses `utils3.runAsThread` to run the socket server in a background thread, keeping the main thread available for blocking operations like `time.sleep()`.

## Command-line Flag Reference

| Flag                    | Requires Daemon?      | Description                         |
|-------------------------|-----------------------|-------------------------------------|
| `--run-server-daemon`   | N/A                   | Starts the daemon (foreground)      |
| `--start-server <conf>` | Auto-starts if needed | Tells daemon to spin up WireProxy   |
| `--stop-server`         | Yes                   | Tells daemon to spin down WireProxy |
| `--server-status`       | No (just checks)      | Queries daemon status               |
| `--add-conf <path>`     | No                    | Adds config to library              |
| `--bulk-import <dir>`   | No                    | Bulk adds configs                   |
| `--remove-conf <name>`  | No                    | Removes config                      |
| `--list-confs`          | No                    | Lists configs                       |
| *(no flags)*            | No                    | Interactive CLI menu                |

**Auto-daemon-start:** `--start-server`, `--stop-server` will automatically launch the daemon in the background if not already running, using `subprocess.Popen(..., start_new_session=True)` for process detachment.
