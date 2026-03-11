"""GMX Market Depth and Effect on Open Interest Analysis.

Checks market depth and estimates the effect on open interest for both long and short positions.
Helps optimise position sizing to stay within a target impact threshold.

Environment variables:

- ``JSON_RPC_ARBITRUM`` -- Arbitrum JSON-RPC endpoint (required for on-chain price impact params)
- ``MARKET_SYMBOL`` -- Optional symbol filter, e.g. ``ETH`` (default: show all markets)
- ``POSITION_SIZE_USD`` -- Position size to evaluate, e.g. ``50000`` (default: 10000)
- ``MAX_IMPACT_BPS`` -- Max acceptable impact in basis points (default: 10)
- ``PORTFOLIO_USD`` -- Total portfolio value for whale-risk sizing, e.g. ``100000`` (default: 100000)
- ``MAX_OI_PCT`` -- Max share of open interest we want to represent, e.g. ``0.025`` = 2.5% (default: 0.025)
- ``FETCH_ONCHAIN_PARAMS`` -- Set to ``1`` to fetch real price impact params from DataStore (requires RPC)

Usage::

    # Quick REST-only market overview (no RPC needed)
    poetry run python scripts/gmx/gmx_market_depth.py

    # With on-chain price impact params
    JSON_RPC_ARBITRUM=<url> FETCH_ONCHAIN_PARAMS=1 poetry run python scripts/gmx/gmx_market_depth.py

    # Filter to ETH markets, evaluate $50 000 with max 5 bps impact
    JSON_RPC_ARBITRUM=<url> FETCH_ONCHAIN_PARAMS=1 MARKET_SYMBOL=ETH POSITION_SIZE_USD=50000 MAX_IMPACT_BPS=5 poetry run python scripts/gmx/gmx_market_depth.py
"""

import logging
import os
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from eth_defi.gmx.api import GMXAPI
from eth_defi.gmx.market_depth import (
    PriceImpactParams,
    calculate_max_position_whale_risk,
    estimate_position_price_impact,
    fetch_price_impact_params,
    find_max_position_size,
)

logger = logging.getLogger(__name__)

# Use a minimum width of 180 so Rich renders full tables without truncating columns.
# In narrow terminals the output wraps rather than hiding data.
_MIN_WIDTH = 180
_terminal_width = os.get_terminal_size(0).columns if sys.stdout.isatty() else _MIN_WIDTH
console = Console(width=max(_terminal_width, _MIN_WIDTH))

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
    max_impact_bps = float(os.environ.get("MAX_IMPACT_BPS", "10"))
    portfolio_usd = float(os.environ.get("PORTFOLIO_USD", "100000"))
    max_oi_pct = float(os.environ.get("MAX_OI_PCT", "0.025"))
    fetch_onchain = os.environ.get("FETCH_ONCHAIN_PARAMS", "0") == "1"

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
        cap_used_long = m.long_open_interest_usd / m.max_long_open_interest_usd * 100 if m.max_long_open_interest_usd > 0 else 0
        cap_used_short = m.short_open_interest_usd / m.max_short_open_interest_usd * 100 if m.max_short_open_interest_usd > 0 else 0
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

    # -- Table 2: Effect on open interest (both long and short) --
    console.print(
        Panel(
            f"[bold cyan]Effect on Open Interest[/] — [bold]${position_size_usd:,.0f}[/] [dim](≤{max_impact_bps:.0f} bps threshold)[/]",
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
    impact_table.add_column("Long OI Effect", justify="right", style="green")
    impact_table.add_column("Long bps", justify="right")
    impact_table.add_column(f"Max Long ≤{max_impact_bps:.0f}", justify="right")
    impact_table.add_column("Short OI Effect", justify="right", style="red")
    impact_table.add_column("Short bps", justify="right")
    impact_table.add_column(f"Max Short ≤{max_impact_bps:.0f}", justify="right")

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

        # Compute impact for both sides
        row_cells = []
        for is_long in (True, False):
            avail_cap = m.available_long_oi_usd if is_long else m.available_short_oi_usd
            effective_size = min(position_size_usd, avail_cap) if avail_cap > 0 else position_size_usd

            impact_usd = estimate_position_price_impact(
                long_open_interest_usd=m.long_open_interest_usd,
                short_open_interest_usd=m.short_open_interest_usd,
                size_delta_usd=effective_size,
                is_long=is_long,
                params=params,
            )
            impact_bps = abs(impact_usd) / effective_size * 10_000 if effective_size > 0 else 0

            max_size = find_max_position_size(
                long_open_interest_usd=m.long_open_interest_usd,
                short_open_interest_usd=m.short_open_interest_usd,
                is_long=is_long,
                max_price_impact_bps=max_impact_bps,
                params=params,
                max_oi_available_usd=avail_cap,
            )

            impact_colour = "green" if impact_usd >= 0 else "red"
            bps_colour = "green" if impact_bps <= max_impact_bps else "red"

            row_cells.extend(
                [
                    f"[{impact_colour}]${impact_usd:+,.2f}[/]",
                    f"[{bps_colour}]{impact_bps:.2f}[/]",
                    _fmt_usd(max_size),
                ]
            )

        impact_table.add_row(m.market_symbol, *row_cells)

    console.print(impact_table)

    if not fetch_onchain:
        console.print("\n[dim]NOTE: OI effect uses approximate demo parameters.\n      Set FETCH_ONCHAIN_PARAMS=1 and JSON_RPC_ARBITRUM=<url> for accurate values.[/]")

    # -- Table 3: Whale-risk position sizing --
    # For each market, compute the max position we'd take so we don't become
    # too large relative to OI (same logic as not being a whale in a Uniswap pool).
    # max_position = min(portfolio_usd, max_oi_pct * side_oi, available_cap)
    console.print(
        Panel(
            f"[bold cyan]Position sizing (whale risk)[/] — [bold]${portfolio_usd:,.0f}[/] portfolio, [bold]{max_oi_pct * 100:.1f}%[/] max OI share",
            expand=False,
        )
    )

    sizing_table = Table(show_header=True, header_style="bold magenta", border_style="dim")
    sizing_table.add_column("Market", style="bold", no_wrap=True)
    sizing_table.add_column("Total OI", justify="right")
    sizing_table.add_column("Max Long", justify="right", style="green")
    sizing_table.add_column("% OI", justify="right")
    sizing_table.add_column("OK?", justify="center")
    sizing_table.add_column("Max Short", justify="right", style="red")
    sizing_table.add_column("% OI", justify="right")
    sizing_table.add_column("OK?", justify="center")
    sizing_table.add_column("Fund/yr", justify="right")
    sizing_table.add_column("Borr/yr", justify="right")

    for m in sorted(markets, key=lambda x: x.long_open_interest_usd + x.short_open_interest_usd, reverse=True):
        total_oi = m.long_open_interest_usd + m.short_open_interest_usd
        if total_oi == 0:
            continue

        long_sizing = calculate_max_position_whale_risk(m, portfolio_usd, max_oi_pct, is_long=True)
        short_sizing = calculate_max_position_whale_risk(m, portfolio_usd, max_oi_pct, is_long=False)

        # Skip markets where neither side can take a meaningful position ($100+)
        if long_sizing.max_position_usd < 100 and short_sizing.max_position_usd < 100:
            continue

        # Status: ✓ if full portfolio fits, ✗ with reason otherwise
        if long_sizing.binding_constraint == "none":
            long_status = "[green]✓[/]"
        else:
            long_status = f"[red]✗{long_sizing.binding_constraint}[/]"

        if short_sizing.binding_constraint == "none":
            short_status = "[green]✓[/]"
        else:
            short_status = f"[red]✗{short_sizing.binding_constraint}[/]"

        # Annualised rates: REST API values (after /10^30) are already annualised
        l_fund_ann = m.long_funding_rate * 100
        l_borrow_ann = m.long_borrowing_rate * 100
        fund_colour = "green" if l_fund_ann >= 0 else "red"
        borrow_colour = "red" if l_borrow_ann > 50 else ("yellow" if l_borrow_ann > 20 else "dim")

        sizing_table.add_row(
            m.market_symbol,
            _fmt_usd(total_oi),
            _fmt_usd(long_sizing.max_position_usd),
            f"{long_sizing.pct_of_total_oi:.2f}%",
            long_status,
            _fmt_usd(short_sizing.max_position_usd),
            f"{short_sizing.pct_of_total_oi:.2f}%",
            short_status,
            f"[{fund_colour}]{l_fund_ann:+.1f}%[/]",
            f"[{borrow_colour}]{l_borrow_ann:.1f}%[/]",
        )

    console.print(sizing_table)
    console.print(f"\n[dim]Sizing: position ≤ min(${portfolio_usd:,.0f}, {max_oi_pct * 100:.1f}% × side OI, available cap).\nOK? ✓ = full portfolio fits. ✗whale = too large for OI. ✗cap = pool full.\nFund = long funding (+ receive, - pay). Borr = long borrowing cost. Both annualised %.[/]")


if __name__ == "__main__":
    main()
