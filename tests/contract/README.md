# Argus client-contract tests

These offline tests lock the client-visible contract of the Polymarket
dispatcher (`argus/polymarket/__init__.py`, P1 JSON and P2 binary) as consumed
by the Rust SDK (`argus-polymarket`: `src/socket.rs`, `src/data_and_encoders.rs`).
The contract must stay identical across the Protocol V2 work: same keys, same
JSON types (str / int / float / bool / list / object), same nullability.

No network, wallet or daemon is needed. Handlers are called on a dispatcher
built with `object.__new__` and stubbed attributes.

Run: `/home/avm/Argus/.venv/bin/python -m pytest tests/contract -q`

## Layout

- `contract_fixtures.py` - realistic upstream payloads, driven through the real converters/handlers.
- `contract_schema.py` - instance -> schema inference and checking (int and float are distinct).
- `golden/*.json` - key -> type/nullable schema, one per response type the SDK parses.
- `golden/examples/*.json` - actual JSON instances (envelope for `account_update_*`
  and `envelope_*`, bare `data` payload otherwise) for the Rust SDK to deserialize
  in its own tests. `*__variantN.json` are nullability variants (e.g. `clob_info` with an unresolvable outcome).
- `test_golden_contract.py` - current output vs goldens (key sets and types).
- `test_p2_roundtrip.py` - Python P2 encoder -> Python port of the Rust decoder.
- `regen_golden.py` - regenerates goldens and examples.

## Goldens must only be regenerated deliberately

A failing golden test means the wire contract changed. Fix the code, not the
golden. Only if the contract change is intended AND the Rust SDK is updated in
lockstep, regenerate:

    /home/avm/Argus/.venv/bin/python tests/contract/regen_golden.py --i-mean-it

and review the `git diff` of `tests/contract/golden/` line by line.

## Known issues recorded

Baseline violations of the Rust SDK contract, fixed on `feat/protocol-v2` (goldens regenerated deliberately):

- `fetch_clob_id_information` returned `outcome: null`; Rust `CLOBInfo.outcome` is a non-optional
  `String`. Now `""` when unresolvable.
- `account_update` order events forwarded `associate_trades: null`; Rust `#[serde(default)] Vec<Value>`
  rejects null. Now `[]`.
- An empty market-data `timestamp` produced `,,` in the P2 body, which panics the Rust decoder.
  Now falls back to receive time (the old `xfail` test now passes).

Note: the repo .gitignore ignores *.json, so new/changed goldens must be staged with `git add -f tests/contract/golden`.
