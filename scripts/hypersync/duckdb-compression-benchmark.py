"""Benchmark DuckDB compression codecs for block timestamp storage and query performance."""

import duckdb
import pandas as pd
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import List, Dict, Any

from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FILE

# Define the table schema and name
TABLE_NAME = "block_timestamps"

# Path to the existing DuckDB file containing the block_timestamps table
# This is the assumed default location for the BlockTimestampDatabase from the previous context.
SOURCE_DB_PATH = DEFAULT_TIMESTAMP_CACHE_FILE

# List of compression codecs to test
COMPRESSION_OPTIONS = [
    # Baseline: No compression
    "UNCOMPRESSED",
    # General-purpose, high ratio
    "ZSTD",
    # Fast, decent ratio
    "LZ4",
    # Very fast, good ratio
    "SNAPPY",
    # Higher compression ratio, but slower (less common for OLAP)
    "GZIP",
    # Special DuckDB dictionary encoding
    "ZSTD_DICTIONARY"
]


def load_existing_data(db_path: Path) -> pd.DataFrame:
    """
    Loads all data from the block_timestamps table in an existing DuckDB file.

    :param db_path: Path to the existing DuckDB file.
    :return: Pandas DataFrame containing the data.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"Source database not found at: {db_path}")

    print(f"Loading data from existing database: {db_path}...")

    conn = None
    try:
        # Connect to the existing database file
        conn = duckdb.connect(str(db_path), read_only=True)

        # Use a simple SELECT * to pull all required columns into a pandas DataFrame
        df = conn.execute(f"SELECT chain_id, block_number, timestamp FROM {TABLE_NAME}").df()

        if df.empty:
            raise ValueError(f"Table '{TABLE_NAME}' in '{db_path}' is empty.")

        # Ensure the timestamp is in millisecond precision for consistent re-insertion
        df["timestamp"] = df["timestamp"].astype("datetime64[ms]")

        print(f"Successfully loaded {len(df):,} rows.")
        return df
    finally:
        if conn:
            conn.close()


def run_test(db_path: Path, compression: str, data: pd.DataFrame) -> Dict[str, Any]:
    """
    Creates a new DuckDB file, configures compression, inserts data, and runs a test query.

    :param db_path: Path to the temporary DuckDB file.
    :param compression: The compression codec to use.
    :param data: The pandas DataFrame to insert (the loaded source data).
    :return: Dictionary containing the results.
    """
    conn = None
    results = {"compression": compression, "size_mb": 0.0, "query_time_s": 0.0}

    try:
        # 1. Connect and set configuration
        conn = duckdb.connect(str(db_path))

        # Set the compression for new columns created within this session
        conn.execute(f"PRAGMA default_compression='{compression}';")

        # 2. Define the schema (must match the original problem)
        conn.execute(f"""
            CREATE TABLE {TABLE_NAME} (
                chain_id UINTEGER,
                block_number UINTEGER,
                timestamp TIMESTAMP_MS,
                PRIMARY KEY (chain_id, block_number)
            );
        """)

        # 3. Insert data from DataFrame
        start_insert = time.time()
        conn.register("df_view", data)
        conn.execute(f"""
            INSERT INTO {TABLE_NAME} (chain_id, block_number, timestamp)
            SELECT chain_id, block_number, timestamp FROM df_view;
        """)
        conn.unregister("df_view")
        insert_time = time.time() - start_insert
        results["insert_time_s"] = insert_time

        # 4. Force a checkpoint (flush to disk) and close to ensure final size is measured
        conn.execute("CHECKPOINT;")
        conn.close()

        # 5. Measure file size
        results["size_mb"] = db_path.stat().st_size / (1024 * 1024)

        # 6. Re-connect for read test
        conn = duckdb.connect(str(db_path), read_only=True)

        # 7. Measure query time (a simple aggregate query forcing a scan)
        start_query = time.time()
        # Use a random chain_id from the data for the query, if available
        test_chain_id = data["chain_id"].sample(1).iloc[0] if not data.empty else 1

        conn.execute(f"""
            SELECT MAX(timestamp) 
            FROM {TABLE_NAME} 
            WHERE chain_id = {test_chain_id};
        """).fetchone()

        results["query_time_s"] = time.time() - start_query

    except Exception as e:
        results["error"] = str(e)
        print(f"Error during test for {compression}: {e}")

    finally:
        if conn:
            conn.close()

    return results


def main():
    """Main execution function to run all compression tests."""

    try:
        # Load data from the existing source database
        source_data = load_existing_data(SOURCE_DB_PATH)
    except (FileNotFoundError, ValueError) as e:
        print(f"\n--- ERROR ---")
        print(f"Failed to load data: {e}")
        print(f"Please ensure the source file exists at {SOURCE_DB_PATH} and contains the '{TABLE_NAME}' table.")
        print(f"If the file path is different, please update the 'SOURCE_DB_PATH' constant in the script.")
        return

    all_results: List[Dict[str, Any]] = []

    # Use a temporary directory to store all test files safely
    with TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        print(f"\nStoring temporary DuckDB files in: {temp_dir}")

        for compression in COMPRESSION_OPTIONS:
            db_filename = f"block_timestamps_{compression.lower()}.duckdb"
            db_path = temp_dir / db_filename

            print(f"\n--- Testing: {compression} ---")
            results = run_test(db_path, compression, source_data)
            all_results.append(results)

            print(f"  File Size: {results['size_mb']:.2f} MB")
            print(f"  Insert Time: {results.get('insert_time_s', 'N/A'):.3f} s")
            print(f"  Query Time: {results['query_time_s']:.3f} s")

    # 8. Print final summary
    print("\n" * 2)
    print("-" * 50)
    print("FINAL COMPRESSION TEST SUMMARY")
    print("-" * 50)

    # Convert results to DataFrame for nice formatting
    summary_df = pd.DataFrame(all_results)

    # Rename columns for presentation
    summary_df.columns = [
        "Compression",
        "File Size (MB)",
        "Query Time (s)",
        "Insert Time (s)",
        "Error" if "error" in summary_df.columns else "Placeholder"
    ]
    if "Placeholder" in summary_df.columns:
        summary_df = summary_df.drop(columns=["Placeholder"])

    # Format numeric columns
    for col in ["File Size (MB)", "Query Time (s)", "Insert Time (s)"]:
        if col in summary_df.columns:
            summary_df[col] = pd.to_numeric(summary_df[col], errors='coerce').map('{:.3f}'.format)

    print(summary_df.to_markdown(index=False))
    print("-" * 50)


if __name__ == "__main__":
    # Ensure you have the required libraries installed:
    # pip install duckdb pandas
    main()
