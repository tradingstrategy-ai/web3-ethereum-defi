from decimal import Decimal

# Simulation of the bug
execution_buffer = 30  # As seen in the user's script
sltp_execution_fee_buffer = 3.0  # Default value in SLTPParams class
base_fee = 0.001 * 10**18  # Assume 0.001 ETH base fee

# Calculation logic from sltp_order.py
# sl_fee = int(sl_fee * execution_buffer * sltp_params.execution_fee_buffer)

calculated_fee = int(base_fee * execution_buffer * sltp_execution_fee_buffer)

print(f"Base Fee: {base_fee / 10**18} ETH")
print(f"Execution Buffer: {execution_buffer}")
print(f"SLTP Buffer: {sltp_execution_fee_buffer}")
print(f"Calculated SL Fee: {calculated_fee / 10**18} ETH")

total_fee = calculated_fee * 2 + (base_fee * execution_buffer)  # SL + TP + Main
print(f"Total Fee: {total_fee / 10**18} ETH")
