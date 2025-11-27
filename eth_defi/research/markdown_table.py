"""Format tables as Markdown to be copy-pasted into a blog post."""

import pandas as pd

from eth_defi.chain import get_chain_homepage


def get_address_link(
    chain: str,
    address: str,
) -> str:
    return f"https://routescan.io/address/{address}"


def _move_columns_to_front(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    # Move specific columns to the front
    df = df[cols + [col for col in df.columns if col not in cols]]
    return df


def format_markdown_table(
    df: pd.DataFrame,
    columns: list[str] | None = None,
    preferred_columns: list[str] | None = None,
    current_tvl_column: str | None = "TVL USD (current / peak)",
    hide_index: bool = False,
) -> pd.DataFrame:
    """Format a DataFrame as a Markdown table.

    :return:
        Vaults table DataFrame for which is safe to call to_markdown() on.
    """

    if df.empty:
        raise RuntimeError("No data available.")

    def _format_links(row: pd.Series) -> pd.Series:
        if "name" in row:
            vault_name = row["name"]
        else:
            index = row.name
            vault_name = index

        if not vault_name:
            vault_name = "<unnamed>"

        vault_id = row["id"]
        chain_id, address = vault_id.split("-")
        vault_link = get_address_link(chain_id, address)
        return f"[{vault_name.strip()}]({vault_link})"

    def _format_chain_name(row: pd.Series) -> pd.Series:
        index = row.name
        vault_id = row["id"]
        chain_id, address = vault_id.split("-")
        name, link = get_chain_homepage(int(chain_id))
        return f"[{name}]({link})"

    def _format_tvl(row: pd.Series) -> pd.Series:
        # return f"${row[current_tvl_column] / 1_000_000:,.3f}M"
        # Preformatted
        return row[current_tvl_column]

    df = df.copy()

    # Remove newlines in column names
    df.columns = [col.replace("\n", " ") if isinstance(col, str) else col for col in df.columns]

    # Remove newlines in text strings,
    # because Markdown cannot handle them
    df = df.map(lambda x: x.replace("\n", " ") if isinstance(x, str) else x)

    # Fix "<Unknown protocol" breaking HTML tags
    df = df.map(lambda x: "" if isinstance(x, str) and "<unknown" in x.lower() else x)

    # Format TVL
    if current_tvl_column:
        df[current_tvl_column] = df.apply(_format_tvl, axis=1)

    # Format all float values to 2 decimal places
    df = df.map(lambda x: f"{x:.2f}" if isinstance(x, float) else x)

    df["Vault"] = df.apply(_format_links, axis=1)
    df["Chain"] = df.apply(_format_chain_name, axis=1)

    df = df.reset_index(drop=True)
    df.index = df.index + 1  # Start index at 1 for Markdown table

    df = _move_columns_to_front(df, ["Vault", "Chain"])

    if preferred_columns:
        df = _move_columns_to_front(df, ["Vault", "Chain"])

    if columns:
        df = df[columns]

    if hide_index:
        # Just do a new index
        # df = df.reset_index(drop=True)
        pass

    return df
