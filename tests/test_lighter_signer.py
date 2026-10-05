"""
Tests for the vendored native signer wrapper (argus/perpetuals/lighter/_signer.py).

These load the bundled shared library and sign LOCALLY -- no network, no orders submitted.
They skip on a platform/architecture with no vendored binary. The ABI is exercised end-to-end
(GenerateAPIKey -> CreateClient -> SignCreateOrder / SignCancelOrder / SignUpdateLeverage ->
CreateAuthToken) so a binary/wrapper mismatch fails here rather than at the first live order.
"""
import pytest

from argus.perpetuals.lighter import _signer as s


@pytest.fixture(autouse=True)
def _require_signer():
    try:
        s.get_signer()
    except RuntimeError as e:
        pytest.skip(str(e))


def test_generate_key_and_client():
    private_key, public_key = s.generate_api_key()
    assert private_key and public_key
    # A generated key must register cleanly (this validates the key format locally).
    s.create_client("https://mainnet.zklighter.elliot.ai", private_key, 304, 0, 1)


def test_sign_create_order_returns_tx14():
    private_key, _ = s.generate_api_key()
    s.create_client("https://mainnet.zklighter.elliot.ai", private_key, 304, 0, 1)
    tx_type, tx_info, tx_hash = s.sign_create_order(
        market_index=0, client_order_index=1, base_amount=1000, price=100000, is_ask=False,
        order_type=0, time_in_force=1, reduce_only=False, trigger_price=0, order_expiry=-1,
        nonce=1, api_key_index=0, account_index=1,
    )
    assert tx_type == 14
    assert tx_info.startswith("{") and tx_hash


def test_sign_leverage_and_cancel():
    private_key, _ = s.generate_api_key()
    s.create_client("https://mainnet.zklighter.elliot.ai", private_key, 304, 0, 1)
    assert s.sign_update_leverage(0, 1000, 0, nonce=2, api_key_index=0, account_index=1)[0] == 20
    assert s.sign_cancel_order(0, 7, nonce=3, api_key_index=0, account_index=1)[0] == 15
    # Immediate cancel-all must carry a NIL timestamp (0); a non-zero time is rejected by the signer.
    assert s.sign_cancel_all_orders(0, 0, 255, nonce=4, api_key_index=0, account_index=1)[0] == 16


def test_create_auth_token_shape():
    private_key, _ = s.generate_api_key()
    s.create_client("https://mainnet.zklighter.elliot.ai", private_key, 304, 0, 1)
    token = s.create_auth_token(3600, 0, 1, now=1_700_000_000)
    parts = token.split(":")
    assert len(parts) == 4
    assert parts[1] == "1" and parts[2] == "0"
    assert int(parts[0]) == 1_700_000_000 + 3600