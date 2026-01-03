import os
import sys
from decimal import Decimal
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.gas_utils import calculate_execution_fee, get_gas_limits
from eth_defi.gas import estimate_gas_fees
from rich.console import Console

console = Console()


def main():
    rpc_url = os.environ.get("ARBITRUM_SEPOLIA_RPC_URL")
    if not rpc_url:
        console.print("[red]Error: ARBITRUM_SEPOLIA_RPC_URL not set[/red]")
        return

    web3 = create_multi_provider_web3(rpc_url)
    config = GMXConfig(web3)

    console.print(f"Connected to chain ID: {web3.eth.chain_id}")

    # 1. Check Gas Price
    gas_fees = estimate_gas_fees(web3)
    console.print(f"Gas Fees: {gas_fees}")
    gas_price = gas_fees.max_fee_per_gas if gas_fees.max_fee_per_gas else web3.eth.gas_price
    console.print(f"Used Gas Price: {gas_price} wei ({gas_price / 1e9} gwei)")

    # 2. Check Gas Limits from Datastore
    datastore = config.get_contract("datastore")
    gas_limits = get_gas_limits(datastore)
    console.print("\nGas Limits from Datastore:")
    for k, v in gas_limits.items():
        console.print(f"  {k}: {v}")

    # 3. Calculate Base Execution Fee
    base_fee = calculate_execution_fee(gas_limits=gas_limits, gas_price=gas_price, order_type="decrease_order", oracle_price_count=2)
    console.print(f"\nBase Execution Fee (decrease_order): {base_fee} wei ({base_fee / 1e18:.6f} ETH)")

    # 4. Simulate Total Fee with Buffers
    execution_buffer = 3  # As set by user
    sltp_buffer = 3.0  # Internal buffer

    final_fee = int(base_fee * execution_buffer * sltp_buffer)
    console.print(f"\nSimulated Fee with Buffers:")
    console.print(f"  Execution Buffer: {execution_buffer}")
    console.print(f"  SLTP Buffer: {sltp_buffer}")
    console.print(f"  Final Fee: {final_fee} wei ({final_fee / 1e18:.6f} ETH)")

    # 5. Check if gas price is absurdly high on testnet
    if gas_price > 100 * 10**9:  # > 100 gwei
        console.print("[red]WARNING: Gas price is extremely high![/red]")


if __name__ == "__main__":
    main()
