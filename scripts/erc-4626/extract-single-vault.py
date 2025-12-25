"""Extract single chain price data out of the bundled data file.

- Resample to any timeframe
"""

import pandas as pd
from pathlib import Path
from eth_defi.chain import get_chain_id_by_name
from eth_defi.vault.base import VaultSpec

chain_id = get_chain_id_by_name("ethereum")
address = "0xd63070114470f685b75b74d60eec7c1113d33a3d"
resample_period = None

chain_name = "hemi"
chain_id = get_chain_id_by_name(chain_name)

path = Path.home() / ".tradingstrategy" / "vaults" / "cleaned-vault-prices-1h.parquet"
price_df = pd.read_parquet(path, filters=[("chain", "==", chain_id)])

print(f"price_df is")
#  0   chain                  0 non-null      uint32
#  1   address                0 non-null      object
#  2   block_number           0 non-null      uint32
#  3   share_price            0 non-null      float64
#  4   total_assets           0 non-null      float64
#  5   total_supply           0 non-null      float64
#  6   performance_fee        0 non-null      float32
#  7   management_fee         0 non-null      float32
#  8   errors                 0 non-null      object
#  9   id                     0 non-null      object
#  10  name                   0 non-null      object
#  11  event_count            0 non-null      int64
#  12  protocol               0 non-null      object
#  13  raw_share_price        0 non-null      float64
#  14  pct_change_prev        0 non-null      float64
#  15  pct_change_next        0 non-null      float64
#  16  returns_1h             0 non-null      float64
#  17  avg_assets_by_vault    0 non-null      float64
#  18  dynamic_tvl_threshold  0 non-null      float64
#  19  tvl_filtering_mask     0 non-null      bool
price_df.info()

assert len(price_df) > 0, f"No data found: {id} in {path}"

if resample_period:
    price_df = price_df.resample(resample_period).last()

print(f"Data is {price_df.index.min()} - {price_df.index.max()}, {len(price_df)} rows")

price_df.to_parquet(Path.home() / "Downloads" / f"chain-{chain_name}-prices-{resample_period}.parquet")
