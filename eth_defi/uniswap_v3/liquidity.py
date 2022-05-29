"""Uniswap v3 liquidity events"""
import csv
from functools import reduce
from typing import Iterable, TypedDict

import pandas as pd


class TickDelta(TypedDict):
    """A dictionary of a tick delta, where liquidity of a tick changes"""

    # block number when tick delta happens
    block_number: int

    # timestamp when tick delta happens
    timestamp: str

    # pool which contains the tick
    pool_contract_address: str

    # tick number
    tick_id: int

    # delta of liquidity gross
    liquidity_gross_delta: int

    # delta of liquidity net
    liquidity_net_delta: int


def handle_mint_event(event: dict) -> Iterable[TickDelta]:
    """Construct tick deltas from mint event

    :param event: Mint event
    :return: Tick deltas for lower tick and upper tick
    """
    block_number = event["block_number"]
    timestamp = event["timestamp"]
    pool_contract_address = event["pool_contract_address"]
    amount = int(event["amount"])
    lower_tick_id = event["tick_lower"]
    upper_tick_id = event["tick_upper"]

    yield TickDelta(
        block_number=block_number,
        timestamp=timestamp,
        pool_contract_address=pool_contract_address,
        tick_id=lower_tick_id,
        liquidity_gross_delta=amount,
        liquidity_net_delta=amount,
    )
    yield TickDelta(
        block_number=block_number,
        timestamp=timestamp,
        pool_contract_address=pool_contract_address,
        tick_id=upper_tick_id,
        liquidity_gross_delta=amount,
        liquidity_net_delta=-amount,
    )


def handle_burn_event(event: dict) -> Iterable[TickDelta]:
    """Construct tick deltas from burn event

    :param event: Mint event
    :return: Tick deltas for lower tick and upper tick
    """
    block_number = event["block_number"]
    timestamp = event["timestamp"]
    pool_contract_address = event["pool_contract_address"]
    amount = int(event["amount"])
    lower_tick_id = event["tick_lower"]
    upper_tick_id = event["tick_upper"]

    yield TickDelta(
        block_number=block_number,
        timestamp=timestamp,
        pool_contract_address=pool_contract_address,
        tick_id=lower_tick_id,
        liquidity_gross_delta=-amount,
        liquidity_net_delta=-amount,
    )
    yield TickDelta(
        block_number=block_number,
        timestamp=timestamp,
        pool_contract_address=pool_contract_address,
        tick_id=upper_tick_id,
        liquidity_gross_delta=-amount,
        liquidity_net_delta=amount,
    )


def create_tick_delta_csv(
    mints_csv: str,
    burns_csv: str,
    output_folder: str = "/tmp",
) -> str:
    """Create intermediate tick delta csv based on mint and burn events

    :param mints_csv: Path to mint events CSV
    :param burns_csv: Path to burn events CSV
    :param output_folder: Folder to contain output CSV files, default is /tmp folder
    :return: output CSV path
    """
    mints_df = pd.read_csv(mints_csv)
    burns_df = pd.read_csv(burns_csv)

    # filter out duplicates
    mints_df = mints_df.drop_duplicates(
        subset=["pool_contract_address", "tx_hash", "log_index", "tick_lower", "tick_upper", "amount"],
        keep="first",
    )
    burns_df = burns_df.drop_duplicates(
        subset=["pool_contract_address", "tx_hash", "log_index", "tick_lower", "tick_upper", "amount"],
        keep="first",
    )

    file_path = f"{output_folder}/uniswapv3-tickdeltas.csv"
    with open(file_path, "w") as fh:
        writer = csv.DictWriter(fh, fieldnames=TickDelta.__annotations__.keys())
        writer.writeheader()

        for _, event in mints_df.iterrows():
            for tick_delta in handle_mint_event(event):
                writer.writerow(tick_delta)

        for _, event in burns_df.iterrows():
            for tick_delta in handle_burn_event(event):
                writer.writerow(tick_delta)

    return file_path


def create_tick_csv(
    tick_delta_csv: str,
    output_folder: str = "/tmp",
) -> str:
    """Create tick csv based on tick delta

    :param tick_delta_csv: Path to tick delta CSV
    :param output_folder: Folder to contain output CSV files, default is /tmp folder
    :return: output CSV path
    """
    deltas_df = pd.read_csv(tick_delta_csv)

    # we don't need to use block number and timestamp here
    deltas_df = deltas_df[["pool_contract_address", "tick_id", "liquidity_gross_delta", "liquidity_net_delta"]]

    def sum_int(series: pd.Series) -> int:
        """Cast series to int then sum

        Since liquidity data is loaded from csv, it has type object and the data (uint128)
        is too big to fit to any pandas datatype
        """
        return reduce(lambda x, y: int(x) + int(y), series)

    ticks_df = (
        deltas_df.groupby(["pool_contract_address", "tick_id"])
        .agg(
            {
                "liquidity_gross_delta": sum_int,
                "liquidity_net_delta": sum_int,
            }
        )
        .reset_index()
    )

    file_path = f"{output_folder}/uniswapv3-ticks.csv"
    return ticks_df.to_csv(file_path)
