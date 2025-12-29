#!/bin/bash
#
# Fetch Hyperliquid vault metadata using the public API
#
# This script fetches vault data from Hyperliquid's stats endpoint
# and outputs them in a stable sorted order for testing purposes.
#
# API Endpoints:
# - https://stats-data.hyperliquid.xyz/Mainnet/vaults (all vaults with APR and PNL history)
# - https://api.hyperliquid.xyz/info with type "vaultDetails" (individual vault details)
#
# Documentation:
# - https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
#
# Usage:
#   ./fetch-vault-metadata.sh [options]
#
# Options:
#   -n, --limit NUM      Limit output to NUM vaults (default: 100)
#   -s, --sort FIELD     Sort by field: tvl, name, created, apr (default: tvl)
#   -r, --reverse        Reverse sort order
#   -o, --output FORMAT  Output format: json, csv, tsv, summary (default: json)
#   -f, --full           Output full response without limit
#   --open-only          Only show vaults that are accepting deposits
#   --min-tvl NUM        Minimum TVL filter in USD (default: 0)
#   --testnet            Use testnet API instead of mainnet
#   -h, --help           Show this help message
#
# Examples:
#   # Get top 10 vaults by TVL
#   ./fetch-vault-metadata.sh -n 10
#
#   # Get all open vaults with at least $10,000 TVL, sorted by APR
#   ./fetch-vault-metadata.sh --open-only --min-tvl 10000 -s apr -f
#
#   # Export top 50 vaults as CSV
#   ./fetch-vault-metadata.sh -n 50 -o csv > vaults.csv
#

set -euo pipefail

# Default values
LIMIT=100
SORT_FIELD="tvl"
REVERSE=""
OUTPUT_FORMAT="json"
FULL_OUTPUT=false
OPEN_ONLY=false
MIN_TVL=0
API_URL="https://stats-data.hyperliquid.xyz/Mainnet/vaults"

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -n|--limit)
            LIMIT="$2"
            shift 2
            ;;
        -s|--sort)
            SORT_FIELD="$2"
            shift 2
            ;;
        -r|--reverse)
            REVERSE="reverse"
            shift
            ;;
        -o|--output)
            OUTPUT_FORMAT="$2"
            shift 2
            ;;
        -f|--full)
            FULL_OUTPUT=true
            shift
            ;;
        --open-only)
            OPEN_ONLY=true
            shift
            ;;
        --min-tvl)
            MIN_TVL="$2"
            shift 2
            ;;
        --testnet)
            API_URL="https://stats-data.hyperliquid-testnet.xyz/Mainnet/vaults"
            shift
            ;;
        -h|--help)
            sed -n '3,37p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

# Check for required tools
for cmd in curl jq; do
    if ! command -v "$cmd" &> /dev/null; then
        echo "Error: $cmd is required but not installed." >&2
        exit 1
    fi
done

# Fetch vaults from Hyperliquid stats API
fetch_vaults() {
    curl -s "$API_URL"
}

# Build jq sort expression based on sort field
get_sort_expression() {
    local field="$1"
    local reverse="$2"

    case "$field" in
        tvl)
            # Sort by TVL numerically (descending by default - highest TVL first)
            if [[ -n "$reverse" ]]; then
                echo 'sort_by(.summary.tvl | tonumber)'
            else
                echo 'sort_by(-(.summary.tvl | tonumber))'
            fi
            ;;
        name)
            # Sort by name alphabetically (case-insensitive)
            if [[ -n "$reverse" ]]; then
                echo 'sort_by(.summary.name | ascii_downcase) | reverse'
            else
                echo 'sort_by(.summary.name | ascii_downcase)'
            fi
            ;;
        created)
            # Sort by creation time (newest first by default)
            if [[ -n "$reverse" ]]; then
                echo 'sort_by(.summary.createTimeMillis)'
            else
                echo 'sort_by(-(.summary.createTimeMillis))'
            fi
            ;;
        apr)
            # Sort by APR (highest first by default)
            if [[ -n "$reverse" ]]; then
                echo 'sort_by(.apr)'
            else
                echo 'sort_by(-.apr)'
            fi
            ;;
        *)
            echo "Unknown sort field: $field (valid: tvl, name, created, apr)" >&2
            exit 1
            ;;
    esac
}

# Build jq filter expression
get_filter_expression() {
    local open_only="$1"
    local min_tvl="$2"
    local filters=()

    if [[ "$open_only" == true ]]; then
        filters+=('(.summary.isClosed == false)')
    fi

    if [[ "$min_tvl" != "0" ]]; then
        filters+=("((.summary.tvl | tonumber) >= $min_tvl)")
    fi

    if [[ ${#filters[@]} -eq 0 ]]; then
        echo '.'
    else
        # Join filters with " and "
        local filter_str="${filters[0]}"
        for ((i=1; i<${#filters[@]}; i++)); do
            filter_str="$filter_str and ${filters[$i]}"
        done
        echo "map(select($filter_str))"
    fi
}

# Format output based on requested format
format_output() {
    local format="$1"

    case "$format" in
        json)
            jq '.'
            ;;
        csv)
            jq -r '["name","vaultAddress","leader","tvl","apr","isClosed","createTimeMillis"], (.[] | [.summary.name, .summary.vaultAddress, .summary.leader, .summary.tvl, .apr, .summary.isClosed, .summary.createTimeMillis]) | @csv'
            ;;
        tsv)
            jq -r '["name","vaultAddress","leader","tvl","apr","isClosed","createTimeMillis"], (.[] | [.summary.name, .summary.vaultAddress, .summary.leader, .summary.tvl, .apr, .summary.isClosed, .summary.createTimeMillis]) | @tsv'
            ;;
        summary)
            # Human-readable summary format
            jq -r '.[] | "[\(.summary.name)] TVL: $\(.summary.tvl | tonumber | floor) | APR: \((.apr * 100) | . * 100 | floor / 100)% | \(if .summary.isClosed then "CLOSED" else "OPEN" end) | \(.summary.vaultAddress)"'
            ;;
        *)
            echo "Unknown output format: $format (valid: json, csv, tsv, summary)" >&2
            exit 1
            ;;
    esac
}

# Main execution
main() {
    local sort_expr filter_expr
    sort_expr=$(get_sort_expression "$SORT_FIELD" "$REVERSE")
    filter_expr=$(get_filter_expression "$OPEN_ONLY" "$MIN_TVL")

    # Fetch vaults
    local result
    result=$(fetch_vaults)

    # Check for errors (non-array response)
    if ! echo "$result" | jq -e 'type == "array"' > /dev/null 2>&1; then
        echo "API Error: Unexpected response format" >&2
        echo "$result" | head -c 500 >&2
        exit 1
    fi

    # Apply filters
    if [[ "$filter_expr" != "." ]]; then
        result=$(echo "$result" | jq "$filter_expr")
    fi

    # Apply sorting
    result=$(echo "$result" | jq "$sort_expr")

    # Apply limit unless full output is requested
    if [[ "$FULL_OUTPUT" == false ]]; then
        result=$(echo "$result" | jq ".[:$LIMIT]")
    fi

    # Output stats to stderr
    local total_count
    total_count=$(echo "$result" | jq 'length')
    echo "# Fetched $total_count vaults (sorted by $SORT_FIELD)" >&2

    # Format and output
    echo "$result" | format_output "$OUTPUT_FORMAT"
}

main
