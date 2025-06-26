"""Vault metrics calculations."""


import numpy as np
import pandas as pd

from eth_defi.chain import get_chain_name


def calculate_lifetime_metrics(
    df: pd.DataFrame,
    vaults_by_id: dict,
):
    """Calculate lifetime metrics for each vault in the provided DataFrame.

    - All-time returns
    - 3M returns
    - 1M returns
    - Volatility (3M)
    
    :param df:
        See notebooks

    """
    results = []

    month_ago = df.index.max() - pd.Timedelta(days=30)
    three_months_ago = df.index.max() - pd.Timedelta(days=90)
    
    for id_val, group in df.groupby('id'):
        # Sort by timestamp just to be safe
        group = group.sort_index()
        name = vaults_by_id[id_val]['Name'] if id_val in vaults_by_id else None
        
        # Calculate lifetime return using cumulative product approach
        lifetime_return = (1 + group['daily_returns']).prod() - 1

        last_three_months = group['daily_returns'].loc[three_months_ago:]
        three_month_returns = (1 + last_three_months).prod() - 1

        last_month = group['daily_returns'].loc[month_ago:]
        one_month_returns = (1 + last_month).prod() - 1

        # Calculate volatility so we can separate actively trading vaults (market making, such) from passive vaults (lending optimisaiton)
        three_months_volatility = last_three_months.std()

        max_nav = group['total_assets'].max()
        current_nav = group['total_assets'].iloc[-1]
        chain_id = group['chain'].iloc[-1]
        mgmt_fee = group['management_fee'].iloc[-1]
        perf_fee = group['performance_fee'].iloc[-1]
        event_count = group['event_count'].iloc[-1]
        protocol = group['protocol'].iloc[-1]
        
        # Calculate CAGR
        # Get the first and last date
        start_date = group.index.min()
        end_date = group.index.max()
        years = (end_date - start_date).days / 365.25        
        cagr = (1 + lifetime_return) ** (1 / years) - 1 if years > 0 else np.nan

        # Calculate 3 months CAGR
        # Get the first and last date
        start_date = last_three_months.index.min()
        end_date = last_three_months.index.max()
        years = (end_date - start_date).days / 365.25        
        three_months_cagr = (1 + three_month_returns) ** (1 / years) - 1 if years > 0 else np.nan

        start_date = last_month.index.min()
        end_date = last_month.index.max()
        years = (end_date - start_date).days / 365.25        
        one_month_cagr = (1 + one_month_returns) ** (1 / years) - 1 if years > 0 else np.nan
        
        results.append({            
            'name': name,
            'cagr': cagr,
            'lifetime_return': lifetime_return,            
            'three_months_cagr': three_months_cagr,
            'one_month_cagr': one_month_cagr,
            "three_months_volatility": three_months_volatility,
            'denomination': vaults_by_id[id_val]['Denomination'] if id_val in vaults_by_id else None,
            'chain': get_chain_name(chain_id),            
            'peak_nav': max_nav,            
            'current_nav': current_nav,
            'years': years, 
            "mgmt_fee": mgmt_fee,
            "perf_fee": perf_fee,
            "event_count": event_count,
            "protocol": protocol,
            'id': id_val,
            'three_months_returns': three_month_returns,            
            'one_month_returns': one_month_returns,            
            'start_date': start_date,
            'end_date': end_date,

        })
    
    return pd.DataFrame(results)


def format_lifetime_table(df: pd.DataFrame) -> pd.DataFrame:
    """Format table for human readable output.
    
    See :py:func:`calculate_lifetime_metrics`
    """

    df = df.copy()
    df["cagr"] = df["cagr"].apply(lambda x: f"{x:.2%}")
    df["lifetime_return"] = df["lifetime_return"].apply(lambda x: f"{x:.2%}")
    df["three_months_cagr"] = df["three_months_cagr"].apply(lambda x: f"{x:.2%}")
    df["three_months_returns"] = df["three_months_returns"].apply(lambda x: f"{x:.2%}")
    df["one_month_cagr"] = df["one_month_cagr"].apply(lambda x: f"{x:.2%}")
    df["one_month_returns"] = df["one_month_returns"].apply(lambda x: f"{x:.2%}")
    df["three_months_volatility"] = df["three_months_volatility"].apply(lambda x: f"{x:.4f}")
    df["event_count"] = df["event_count"].apply(lambda x: f"{x:,}")
    df["mgmt_fee"] = df["mgmt_fee"].apply(lambda x: f"{x:.2%}" if pd.notna(x) else 'unknown')
    df["perf_fee"] = df["perf_fee"].apply(lambda x: f"{x:.2%}" if pd.notna(x) else 'unknown')
    
    df = df.rename(columns={
        "cagr": "Annualised lifetime return",
        "lifetime_return": "Lifetime return",
        "three_months_cagr": "Last 3M return annualised",
        "three_months_volatility": "Last 3M months volatility",
        "one_month_cagr": "Last 1M return annualised",
        "three_months_volatility": "Last 3M months volatility",
        "three_months_returns": "Last 3M return",
        "event_count": "Deposit/redeem count",
        "peak_nav": "Peak TVL USD",
        "current_nav": "Current TVL USD",
        "years": "Age (years)",
        "mgmt_fee": "Management fee",
        "perf_fee": "Performance fee",
        "denomination": "Denomination",
        "chain": "Chain",
        "protocol": "Protocol",
        "start_date": "First deposit",
        "end_date": "Last deposit",
        
    })
    return df
