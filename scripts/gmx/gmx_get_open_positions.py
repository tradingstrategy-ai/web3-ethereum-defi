import os

from rich.console import Console
from rich.table import Table
from web3 import Web3

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core import GetOpenPositions

console = Console()


def get_positions(config: dict, address: str) -> dict:
    return GetOpenPositions(config, address=address).get_data()


def calculate_profit_usd(position: dict) -> float:
    return position["position_size"] * (position["percent_profit"] / 100)


def display_positions(positions: dict):
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Market")
    table.add_column("Size (USD)", justify="right")
    table.add_column("Entry Price", justify="right")
    table.add_column("Mark Price", justify="right")
    table.add_column("Profit (%)", justify="right")
    table.add_column("Profit (USD)", justify="right")

    for symbol, data in positions.items():
        profit_usd = calculate_profit_usd(data)
        table.add_row(symbol, f"${data['position_size']:.2f}", f"{data['entry_price']:.2f}", f"{data['mark_price']:.2f}", f"{data['percent_profit']:.4f}%", f"${profit_usd:.2f}")

    console.print(table)


if __name__ == "__main__":
    rpc = os.environ["ARBITRUM_JSON_RPC_URL"]
    web3 = Web3(Web3.HTTPProvider(rpc))
    config = GMXConfig(web3)

    positions_manager = GetOpenPositions(config)
    address = "0x91666112b851E33D894288A95846d14781e86cad"
    positions = positions_manager.get_data(address)
    display_positions(positions)
