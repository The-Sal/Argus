"""
Unit tests for HyperLiquidRest's handling of bare `null` info responses.

Hyperliquid uses `null` for two different things: `perpAnnotation` legitimately returns
`null` for coins that have no annotation (all default-dex coins, e.g. BTC), while for every
other info request `null` usually means the request was rate-limited. `_post` must only
tolerate it for the annotation endpoint. These tests stub the HTTP session, so they run
offline.

Run with: pytest tests/test_hyper_rest.py
"""

import unittest
from typing import Any

from argus.perpetuals.hyper import rest as hyper_rest


class _StubResponse:
    def __init__(self, payload: Any):
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _StubSession:
    def __init__(self, payload: Any):
        self.payload = payload
        self.requests = []

    def post(self, url: str, json: dict) -> _StubResponse:
        self.requests.append(json)
        return _StubResponse(self.payload)


def _rest_returning(payload: Any) -> hyper_rest.HyperLiquidRest:
    rest = hyper_rest.HyperLiquidRest(wallet_address="0x0", private_key="0x0")
    rest.session = _StubSession(payload)
    return rest


class PerpAnnotationNullTest(unittest.TestCase):
    def test_null_annotation_returns_none(self):
        rest = _rest_returning(None)
        self.assertIsNone(rest.get_perp_annotation("BTC"))
        self.assertEqual(rest.session.requests, [{"type": "perpAnnotation", "coin": "BTC"}])

    def test_annotation_payload_parses(self):
        rest = _rest_returning({"category": "stocks", "description": "AAPL references 1 share."})
        annotation = rest.get_perp_annotation("xyz:AAPL")
        self.assertEqual(annotation.category, "stocks")
        self.assertEqual(annotation.description, "AAPL references 1 share.")


class OtherEndpointsStillRejectNullTest(unittest.TestCase):
    def test_get_dexs_raises_on_null(self):
        rest = _rest_returning(None)
        with self.assertRaises(RuntimeError):
            rest.get_dexs()

    def test_predicted_fundings_raises_on_null(self):
        rest = _rest_returning(None)
        with self.assertRaises(RuntimeError):
            rest.get_predicted_fundings()


if __name__ == "__main__":
    unittest.main()
