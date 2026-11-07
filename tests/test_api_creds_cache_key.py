"""The disk-cached CLOB API creds must be keyed per account (signer, funder, signature type)."""
import inspect

from argus.cache_sys import DomainCache
from argus.polymarket_direct import rest


def test_cache_key_differs_per_account():
    a = DomainCache.generate_key("_create_or_derive_api_key", object(), "0xA:0xF1:3")
    b = DomainCache.generate_key("_create_or_derive_api_key", object(), "0xB:0xF2:3")
    c = DomainCache.generate_key("_create_or_derive_api_key", object(), "0xA:0xF1:1")
    assert len({a, b, c}) == 3


def test_creds_derivation_is_called_with_account_identity():
    # the cache decorator hides the real signature, so check the call sites instead
    src = inspect.getsource(rest.PolyRestAPI)
    assert "self._create_or_derive_api_key()" not in src
    assert src.count("self._create_or_derive_api_key(self._api_creds_identity)") == 2
