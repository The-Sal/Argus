#!/usr/bin/env python3
"""
Real-time AccountProvider Debug Client
Connects to localhost:9973 and displays live portfolio updates
"""
import os
import sys
import json
import socket
from datetime import datetime
from typing import Dict, Optional
from dataclasses import dataclass


@dataclass
class Position:
    """Represents a stock position"""
    symbol: str = ""
    position: float = 0.0
    avg_cost: float = 0.0
    avg_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    mkt_price: float = 0.0
    mkt_value: float = 0.0
    currency: str = "USD"

    @classmethod
    def from_dict(cls, data: dict) -> 'Position':
        return cls(
            symbol=data.get('contract_desc', ''),
            position=float(data.get('position', 0)),
            avg_cost=float(data.get('avg_cost', 0)),
            avg_price=float(data.get('avg_price', 0)),
            unrealized_pnl=float(data.get('unrealized_pnl', 0)),
            realized_pnl=float(data.get('realized_pnl', 0)),
            mkt_price=float(data.get('mkt_price', 0)),
            mkt_value=float(data.get('mkt_value', 0)),
            currency=data.get('currency', 'USD')
        )


@dataclass
class AccountBalances:
    """Represents account balance information"""
    account_id: str = ""
    daily_pnl: float = 0.0
    pnl: float = 0.0
    market_value: float = 0.0
    net_liquidation: float = 0.0
    excess_liquidity: float = 0.0

    @classmethod
    def from_dict(cls, data: dict) -> 'AccountBalances':
        return cls(
            account_id=data.get('account_id', ''),
            daily_pnl=float(data.get('daily_pnl', 0)),
            pnl=float(data.get('pnl', 0)),
            market_value=float(data.get('market_value', 0)),
            net_liquidation=float(data.get('net_liquidation', 0)),
            excess_liquidity=float(data.get('excess_liquidity', 0))
        )


class PortfolioMonitor:
    """Monitors and displays portfolio data in real-time"""

    def __init__(self, host: str = 'localhost', port: int = 9973):
        self.host = host
        self.port = port
        self.positions: Dict[str, Position] = {}
        self.balances: Optional[AccountBalances] = None
        self.buffer = ""
        self.last_update = None

    def connect(self) -> socket.socket:
        """Establish connection to debug server"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((self.host, self.port))
        return sock

    def clear_screen(self):
        """Clear the terminal screen"""
        os.system('clear')

    def parse_message(self, message: str):
        """Parse incoming JSON message"""
        try:
            data = json.loads(message)
            msg_type = data.get('type')

            if msg_type == 'position':
                pos_data = data.get('data', {})
                symbol = pos_data.get('contract_desc', '')
                if symbol:
                    self.positions[symbol] = Position.from_dict(pos_data)

            elif msg_type == 'account_balances':
                balance_data = data.get('data', {})
                self.balances = AccountBalances.from_dict(balance_data)

            self.last_update = datetime.now()

        except json.JSONDecodeError as e:
            print(f"Failed to parse message: {e}")
        except Exception as e:
            print(f"Error processing message: {e}")

    def format_currency(self, value: float) -> str:
        """Format value as currency"""
        return f"${value:,.2f}"

    def format_pnl(self, value: float) -> str:
        """Format PnL with color coding"""
        color = '\033[92m' if value >= 0 else '\033[91m'  # Green or Red
        reset = '\033[0m'
        sign = '+' if value >= 0 else ''
        return f"{color}{sign}{value:,.2f}{reset}"

    def draw_header(self):
        """Draw the header section"""
        print("=" * 110)
        print(f"{'ACCOUNT PORTFOLIO MONITOR':^110}")
        print("=" * 110)
        if self.last_update:
            print(f"Last Update: {self.last_update.strftime('%Y-%m-%d %H:%M:%S')}")
        if self.balances:
            print(f"Account: {self.balances.account_id}")
        print()

    def draw_positions_table(self):
        """Draw the positions table"""
        if not self.positions:
            print("No positions to display")
            return

        # Table header
        headers = ["Symbol", "Qty", "Avg Cost", "Mkt Price", "Mkt Value", "Unrealized P&L", "Realized P&L"]
        col_widths = [10, 10, 12, 12, 15, 18, 18]

        print("POSITIONS:")
        print("-" * 110)

        # Print headers
        header_row = ""
        for header, width in zip(headers, col_widths):
            header_row += f"{header:^{width}}"
        print(header_row)
        print("-" * 110)

        # Print positions
        total_unrealized = 0.0
        total_realized = 0.0
        total_market_value = 0.0

        for symbol in sorted(self.positions.keys()):
            pos = self.positions[symbol]
            total_unrealized += pos.unrealized_pnl
            total_realized += pos.realized_pnl
            total_market_value += pos.mkt_value

            row = f"{symbol:<{col_widths[0]}}"
            row += f"{pos.position:>{col_widths[1]}.2f}"
            row += f"{self.format_currency(pos.avg_price):>{col_widths[2]}}"
            row += f"{self.format_currency(pos.mkt_price):>{col_widths[3]}}"
            row += f"{self.format_currency(pos.mkt_value):>{col_widths[4]}}"
            row += f"{self.format_pnl(pos.unrealized_pnl):>{col_widths[5]}}"
            row += f"{self.format_pnl(pos.realized_pnl):>{col_widths[6]}}"
            print(row)

        # Print totals
        print("-" * 110)
        row = f"{'TOTAL':<{col_widths[0]}}"
        row += f"{'':<{col_widths[1]}}"
        row += f"{'':<{col_widths[2]}}"
        row += f"{'':<{col_widths[3]}}"
        row += f"{self.format_currency(total_market_value):>{col_widths[4]}}"
        row += f"{self.format_pnl(total_unrealized):>{col_widths[5]}}"
        row += f"{self.format_pnl(total_realized):>{col_widths[6]}}"
        print(row)
        print()

    def draw_account_balances(self):
        """Draw account balance information"""
        if not self.balances:
            return

        print("ACCOUNT SUMMARY:")
        print("-" * 110)

        balance_items = [
            ("Daily P&L", self.balances.daily_pnl, True),
            ("Total P&L", self.balances.pnl, True),
            ("Market Value", self.balances.market_value, False),
            ("Net Liquidation", self.balances.net_liquidation, False),
            ("Excess Liquidity", self.balances.excess_liquidity, False),
        ]

        for label, value, is_pnl in balance_items:
            if is_pnl:
                # P&L values - format with color and sign
                formatted_val = self.format_pnl(value)
                print(f"{label:<30} {formatted_val:>20}")
            else:
                # Currency values - format as dollars
                formatted_val = self.format_currency(value)
                print(f"{label:<30} {formatted_val:>20}")

        print("=" * 110)

    def draw_screen(self):
        """Redraw the entire screen"""
        self.clear_screen()
        self.draw_header()
        self.draw_positions_table()
        self.draw_account_balances()

    def process_buffer(self):
        """Process buffered data and extract complete messages"""
        while '~' in self.buffer and 'L' in self.buffer:
            start = self.buffer.find('~')
            end = self.buffer.find('L', start)

            if start != -1 and end != -1:
                message = self.buffer[start + 1:end]
                self.buffer = self.buffer[end + 1:]
                self.parse_message(message)
            else:
                break

    def run(self):
        """Main run loop"""
        print(f"Connecting to {self.host}:{self.port}...")

        try:
            sock = self.connect()
            print("Connected! Waiting for data...")

            while True:
                try:
                    data = sock.recv(4096)
                    if not data:
                        print("Connection closed by server")
                        break

                    self.buffer += data.decode('utf-8')
                    self.process_buffer()
                    self.draw_screen()

                except KeyboardInterrupt:
                    print("\n\nShutting down...")
                    break
                except Exception as e:
                    print(f"Error receiving data: {e}")
                    break

        except ConnectionRefusedError:
            print(f"Could not connect to {self.host}:{self.port}")
            print("Make sure the AccountProvider debug server is running.")
        except Exception as e:
            print(f"Error: {e}")
        finally:
            print("Monitor stopped.")


if __name__ == "__main__":
    monitor = PortfolioMonitor()
    monitor.run()