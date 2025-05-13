import json
from pprint import pprint
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from datetime import datetime
import os


@dataclass
class WebSocketMessage:
    """Represents a single WebSocket message."""
    data: str
    type: str  # "send" or "receive"
    time: float
    opcode: Optional[int] = None
    timestamp: Optional[str] = None
    
    def __repr__(self) -> str:
        time_str = datetime.fromtimestamp(self.time).strftime('%Y-%m-%d %H:%M:%S.%f') if self.time else "Unknown"
        return f"{time_str} [{self.type.upper()}]: {self.data[:100]}{'...' if len(self.data) > 100 else ''}"


@dataclass
class WebSocketConnection:
    """Represents a WebSocket connection with its messages."""
    url: str
    messages: List[WebSocketMessage]
    start_time: float
    
    def __repr__(self) -> str:
        return f"WebSocket: {self.url} ({len(self.messages)} messages)"


def extract_websockets_from_har(har_file_path: str) -> List[WebSocketConnection]:
    """
    Extract all WebSocket connections and their messages from a HAR file.
    
    Args:
        har_file_path: Path to the HAR file
        
    Returns:
        List of WebSocketConnection objects containing connection details and messages
    """
    # Read and parse the HAR file
    with open(har_file_path, 'r', encoding='utf-8') as f:
        har_data = json.load(f)
    
    connections = []
    
    # Check if the HAR file has the expected structure
    if 'log' not in har_data or 'entries' not in har_data['log']:
        print(f"Warning: HAR file {har_file_path} does not have the expected structure")
        return connections
    
    # First pass: identify WebSocket connections
    ws_entries = {}
    for entry in har_data['log']['entries']:
        # Look for different indicators of WebSocket connections
        if any([
            entry.get('_resourceType') == 'websocket',
            entry.get('_type') == 'websocket',
            entry.get('request', {}).get('url', '').startswith(('ws://', 'wss://')),
            'webSocketMessages' in entry,
            '_webSocketMessages' in entry
        ]):
            url = entry.get('request', {}).get('url', 'unknown_websocket')
            start_time = entry.get('startedDateTime', '')
            
            # Convert the ISO timestamp to a float if possible
            try:
                if start_time:
                    dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                    start_time_float = dt.timestamp()
                else:
                    start_time_float = 0
            except ValueError:
                start_time_float = 0
            
            # Create a new connection entry if not exists
            if url not in ws_entries:
                ws_entries[url] = {
                    'url': url,
                    'start_time': start_time_float,
                    'entries': []
                }
            
            ws_entries[url]['entries'].append(entry)
    
    # Second pass: extract messages from each connection
    for url, conn_data in ws_entries.items():
        all_messages = []
        
        # Process each entry related to this connection
        for entry in conn_data['entries']:
            # Try different known keys where WebSocket messages might be stored
            message_arrays = [
                entry.get('_webSocketMessages', []),
                entry.get('webSocketMessages', []),
                entry.get('response', {}).get('_webSocketMessages', []),
                entry.get('response', {}).get('webSocketMessages', [])
            ]
            
            for message_array in message_arrays:
                if not message_array:
                    continue
                
                for msg in message_array:
                    # Extract message data
                    msg_data = msg.get('data', '')
                    msg_type = msg.get('type', 'unknown')  # "send" or "receive"
                    
                    # Handle time - could be timestamp or relative time
                    msg_time = 0
                    if 'time' in msg:
                        try:
                            msg_time = float(msg['time'])
                        except (ValueError, TypeError):
                            pass
                    
                    # Create WebSocketMessage object
                    ws_message = WebSocketMessage(
                        data=msg_data,
                        type=msg_type,
                        time=conn_data['start_time'] + (msg_time / 1000 if msg_time else 0),
                        opcode=msg.get('opcode'),
                        timestamp=msg.get('timestamp')
                    )
                    
                    all_messages.append(ws_message)
        
        # Sort messages by time if possible
        all_messages.sort(key=lambda m: m.time)
        
        # Create a WebSocketConnection object and add to results
        if all_messages:  # Only add connections that have messages
            connection = WebSocketConnection(
                url=url,
                messages=all_messages,
                start_time=conn_data['start_time']
            )
            connections.append(connection)
    
    return connections


def save_websocket_messages(connections: List[WebSocketConnection], output_dir: str) -> None:
    """
    Save the extracted WebSocket messages to files.
    
    Args:
        connections: List of WebSocketConnection objects
        output_dir: Directory to save the output files
    """
    os.makedirs(output_dir, exist_ok=True)
    
    # Create a summary file
    with open(os.path.join(output_dir, 'websocket_summary.txt'), 'w', encoding='utf-8') as f:
        f.write(f"Found {len(connections)} WebSocket connections\n\n")
        for i, conn in enumerate(connections, 1):
            f.write(f"{i}. {conn.url}: {len(conn.messages)} messages\n")
    
    # Save each connection's messages to a separate file
    for i, conn in enumerate(connections, 1):
        # Create a sanitized filename from the URL
        filename = f"websocket_{i}.json"
        
        # Save messages as JSON
        messages_json = []
        for msg in conn.messages:
            msg_dict = {
                'data': msg.data,
                'type': msg.type,
                'time': msg.time,
                'timestamp': datetime.fromtimestamp(msg.time).strftime('%Y-%m-%d %H:%M:%S.%f'),
                'opcode': msg.opcode
            }
            messages_json.append(msg_dict)
        
        with open(os.path.join(output_dir, filename), 'w', encoding='utf-8') as f:
            json.dump({
                'url': conn.url,
                'message_count': len(conn.messages),
                'start_time': conn.start_time,
                'start_time_formatted': datetime.fromtimestamp(conn.start_time).strftime('%Y-%m-%d %H:%M:%S.%f'),
                'messages': messages_json
            }, f, indent=2)
        
        # Save a readable text version as well
        with open(os.path.join(output_dir, f"websocket_{i}.txt"), 'w', encoding='utf-8') as f:
            f.write(f"WebSocket Connection: {conn.url}\n")
            f.write(f"Start Time: {datetime.fromtimestamp(conn.start_time).strftime('%Y-%m-%d %H:%M:%S.%f')}\n")
            f.write(f"Message Count: {len(conn.messages)}\n\n")
            f.write("--- MESSAGES ---\n\n")
            
            for j, msg in enumerate(conn.messages, 1):
                timestamp = datetime.fromtimestamp(msg.time).strftime('%Y-%m-%d %H:%M:%S.%f')
                f.write(f"[{j}] {timestamp} [{msg.type.upper()}]:\n")
                f.write(f"{msg.data}\n\n")


def extract_and_save_websockets(har_file_path: str, output_dir: str = "websocket_output") -> None:
    """
    Extract WebSocket messages from a HAR file and save them to files.
    
    Args:
        har_file_path: Path to the HAR file
        output_dir: Directory to save the output files (default: "websocket_output")
    """
    print(f"Extracting WebSocket messages from {har_file_path}...")
    connections = extract_websockets_from_har(har_file_path)
    
    if not connections:
        print("No WebSocket connections found in the HAR file.")
        return
    
    print(f"Found {len(connections)} WebSocket connections:")
    for i, conn in enumerate(connections, 1):
        print(f"{i}. {conn.url}: {len(conn.messages)} messages")
    
    save_websocket_messages(connections, output_dir)
    print(f"WebSocket messages saved to {output_dir}/")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python websocket_extractor.py <har_file_path> [output_directory]")
        sys.exit(1)
    
    har_file = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "websocket_output"
    
    extract_and_save_websockets(har_file, output_dir)


