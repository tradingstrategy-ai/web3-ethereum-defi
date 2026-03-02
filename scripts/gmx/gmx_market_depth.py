"""GMX Market Depth and Position Impact Analysis.

Checks market depth and estimates price impact before opening a position.
Helps optimise position sizing to stay within a target price impact threshold.

Environment variables:

- ``JSON_RPC_ARBITRUM`` -- Arbitrum JSON-RPC endpoint (required for on-chain price impact params)
- ``MARKET_SYMBOL`` -- Optional symbol filter, e.g. ``ETH`` (default: show all markets)
- ``POSITION_SIZE_USD`` -- Position size to evaluate, e.g. ``50000`` (default: 10000)
- ``POSITION_SIDE`` -- ``long`` or ``short`` (default: ``long``)
- ``MAX_IMPACT_BPS`` -- Max acceptable impact in basis points (default: 10)
- ``FETCH_ONCHAIN_PARAMS`` -- Set to ``1`` to fetch real price impact params from DataStore (requires RPC)

Usage::

    # Quick REST-only market overview (no RPC needed)
    poetry run python scripts/gmx/gmx_market_depth.py

    # With on-chain price impact params
    JSON_RPC_ARBITRUM=<url> FETCH_ONCHAIN_PARAMS=1 poetry run python scripts/gmx/gmx_market_depth.py

    # Filter to ETH markets, evaluate a $50 000 short with max 5 bps impact
    JSON_RPC_ARBITRUM=<url> FETCH_ONCHAIN_PARAMS=1 MARKET_SYMBOL=ETH POSITION_SIZE_USD=50000 POSITION_SIDE=short MAX_IMPACT_BPS=5 poetry run python scripts/gmx/gmx_market_depth.py
"""

import logging
import os
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from eth_defi.gmx.api import GMXAPI
from eth_defi.gmx.market_depth import (
    PriceImpactParams,
    estimate_position_price_impact,
    fetch_price_impact_params,
    find_max_position_size,
)

logger = logging.getLogger(__name__)

console = Console()

# ---------------------------------------------------------------------------
# Fallback price impact params for demonstration (approximate ETH/USD values).
# Replace with fetch_price_impact_params() for accurate on-chain values.
# ---------------------------------------------------------------------------
_DEMO_PARAMS = PriceImpactParams(
    positive_factor=2_000_000_000_000_000_000_000,
    negative_factor=4_000_000_000_000_000_000_000,
    positive_exponent=2_000_000_000_000_000_000_000_000_000_000,
    negative_exponent=2_000_000_000_000_000_000_000_000_000_000,
    max_positive_factor=4_000_000_000_000_000_000_000_000_000,
    max_negative_factor=0,
)


def _fmt_usd(v: float) -> str:
    """Format a USD value with $ prefix and thousand separators."""
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v / 1_000:.2f}K"
    return f"${v:,.2f}"


def main() -> None:
    """Run the market depth analysis script."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    # -- Config from environment --
    rpc_url = os.environ.get("JSON_RPC_ARBITRUM", "")
    market_filter = os.environ.get("MARKET_SYMBOL", "")
    position_size_usd = float(os.environ.get("POSITION_SIZE_USD", "10000"))
    position_side = os.environ.get("POSITION_SIDE", "long").lower()
    max_impact_bps = float(os.environ.get("MAX_IMPACT_BPS", "10"))
    fetch_onchain = os.environ.get("FETCH_ONCHAIN_PARAMS", "0") == "1"

    if position_side not in ("long", "short"):
        console.print(f"[bold red]ERROR:[/] POSITION_SIDE must be 'long' or 'short', got {position_side!r}")
        sys.exit(1)

    is_long = position_side == "long"

    # -- Fetch market depth via REST (no RPC needed) --
    api = GMXAPI(chain="arbitrum")
    markets = api.get_market_depth(market_symbol=market_filter or None, use_cache=False)

    if not markets:
        console.print(f"No listed markets found{' matching ' + market_filter if market_filter else ''}.")
        sys.exit(0)

    scope = "all markets" if not market_filter else market_filter
    console.print(Panel(f"[bold cyan]GMX Market Depth — Arbitrum[/] [dim]({scope})[/]", expand=False))

    # -- Table 1: Market depth overview --
    depth_table = Table(show_header=True, header_style="bold magenta", border_style="dim")
    depth_table.add_column("Market", style="bold", no_wrap=True)
    depth_table.add_column("Long OI", justify="right", style="green")
    depth_table.add_column("Short OI", justify="right", style="red")
    depth_table.add_column("Long%", justify="right")
    depth_table.add_column("Avail Long Cap", justify="right", style="green")
    depth_table.add_column("Avail Short Cap", justify="right", style="red")
    depth_table.add_column("Cap% L", justify="right")
    depth_table.add_column("Cap% S", justify="right")

    for m in sorted(markets, key=lambda x: x.long_open_interest_usd + x.short_open_interest_usd, reverse=True):
        total_oi = m.long_open_interest_usd + m.short_open_interest_usd
        if total_oi == 0:
            continue
        long_pct = m.long_open_interest_usd / total_oi * 100 if total_oi > 0 else 0
        cap_used_long = (
            m.long_open_interest_usd / m.max_long_open_interest_usd * 100
            if m.max_long_open_interest_usd > 0
            else 0
        )
        cap_used_short = (
            m.short_open_interest_usd / m.max_short_open_interest_usd * 100
            if m.max_short_open_interest_usd > 0
            else 0
        )
        # Colour cap utilisation: red when >80%, yellow >60%, green otherwise
        cap_l_colour = "red" if cap_used_long > 80 else ("yellow" if cap_used_long > 60 else "green")
        cap_s_colour = "red" if cap_used_short > 80 else ("yellow" if cap_used_short > 60 else "green")

        depth_table.add_row(
            m.market_symbol,
            _fmt_usd(m.long_open_interest_usd),
            _fmt_usd(m.short_open_interest_usd),
            f"{long_pct:.1f}%",
            _fmt_usd(m.available_long_oi_usd),
            _fmt_usd(m.available_short_oi_usd),
            f"[{cap_l_colour}]{cap_used_long:.1f}%[/]",
            f"[{cap_s_colour}]{cap_used_short:.1f}%[/]",
        )

    console.print(depth_table)

    # -- Table 2: Price impact analysis --
    side_label = "Long" if is_long else "Short"
    console.print(
        Panel(
            f"[bold cyan]Price Impact Analysis[/] — [yellow]{position_side.upper()}[/] [bold]${position_size_usd:,.0f}[/]",
            expand=False,
        )
    )

    if fetch_onchain and not rpc_url:
        console.print("[bold yellow]WARNING:[/] FETCH_ONCHAIN_PARAMS=1 but JSON_RPC_ARBITRUM not set. Using demo params.")
        fetch_onchain = False

    gmx_config = None
    if fetch_onchain:
        from eth_defi.gmx.config import GMXConfig
        from eth_defi.provider.multi_provider import create_multi_provider_web3

        web3 = create_multi_provider_web3(rpc_url)
        gmx_config = GMXConfig(web3)
        console.print(f"[dim]Connected to Arbitrum (block #{web3.eth.block_number:,})[/]\n")

    impact_table = Table(show_header=True, header_style="bold magenta", border_style="dim")
    impact_table.add_column("Market", style="bold", no_wrap=True)
    impact_table.add_column(f"Avail {side_label} Cap", justify="right")
    impact_table.add_column(f"Impact ({position_side} ${position_size_usd:,.0f})", justify="right")
    impact_table.add_column("Impact bps", justify="right")
    impact_table.add_column(f"≤{max_impact_bps:.0f} bps?", justify="center")
    impact_table.add_column(f"Max size ≤{max_impact_bps:.0f} bps", justify="right")
    impact_table.add_column("Cap OK?", justify="center")

    for m in sorted(markets, key=lambda x: x.long_open_interest_usd + x.short_open_interest_usd, reverse=True):
        total_oi = m.long_open_interest_usd + m.short_open_interest_usd
        if total_oi == 0:
            continue

        # Get price impact params
        if fetch_onchain and gmx_config is not None:
            try:
                params = fetch_price_impact_params(gmx_config, m.market_token_address)
            except Exception as e:
                logger.warning("Could not fetch params for %s: %s", m.market_symbol, e)
                params = _DEMO_PARAMS
        else:
            params = _DEMO_PARAMS

        # Estimate impact for requested position size
        avail_cap = m.available_long_oi_usd if is_long else m.available_short_oi_usd
        impact_usd = estimate_position_price_impact(
            long_open_interest_usd=m.long_open_interest_usd,
            short_open_interest_usd=m.short_open_interest_usd,
            size_delta_usd=min(position_size_usd, avail_cap) if avail_cap > 0 else position_size_usd,
            is_long=is_long,
            params=params,
        )
        effective_size = min(position_size_usd, avail_cap) if avail_cap > 0 else position_size_usd
        impact_bps = abs(impact_usd) / effective_size * 10_000 if effective_size > 0 else 0

        # Find max size under threshold
        max_size = find_max_position_size(
            long_open_interest_usd=m.long_open_interest_usd,
            short_open_interest_usd=m.short_open_interest_usd,
            is_long=is_long,
            max_price_impact_bps=max_impact_bps,
            params=params,
            max_oi_available_usd=avail_cap,
        )

        cap_ok = position_size_usd <= avail_cap
        impact_ok = impact_bps <= max_impact_bps

        cap_cell = Text("✓", style="green") if cap_ok else Text("✗ exceeds cap", style="red")
        impact_cell = Text("✓", style="green") if impact_ok else Text(f"✗ {impact_bps:.1f} bps", style="red")
        impact_usd_style = "green" if impact_usd >= 0 else "red"

        impact_table.add_row(
            m.market_symbol,
            _fmt_usd(avail_cap),
            f"[{impact_usd_style}]${impact_usd:+,.2f}[/]",
            f"{impact_bps:.2f} bps",
            impact_cell,
            _fmt_usd(max_size),
            cap_cell,
        )

    console.print(impact_table)

    if not fetch_onchain:
        console.print(
            "\n[dim]NOTE: Price impact uses approximate demo parameters.\n"
            "      Set FETCH_ONCHAIN_PARAMS=1 and JSON_RPC_ARBITRUM=<url> for accurate values.[/]"
        )


if __name__ == "__main__":
    main()
