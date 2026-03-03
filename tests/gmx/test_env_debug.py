import os

def test_env():
    v = os.environ.get("JSON_RPC_ARBITRUM")
    print(f"\nJSON_RPC_ARBITRUM={repr(v)}")
    if not v:
        import pytest
        pytest.skip("no env var")
    assert True
