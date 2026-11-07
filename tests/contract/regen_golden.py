"""Regenerate tests/contract/golden/*.json and golden/examples/*.json.

DANGER: goldens lock the client-visible contract consumed by the Rust SDK.
Only run this deliberately, when a contract change is intended and the SDK
has been updated accordingly.  Requires --i-mean-it.

    /home/avm/Argus/.venv/bin/python tests/contract/regen_golden.py --i-mean-it
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from contract_fixtures import build_instances  # noqa: E402
from contract_schema import infer_many  # noqa: E402

GOLDEN = Path(__file__).parent / "golden"
EXAMPLES = GOLDEN / "examples"


def main() -> int:
    if "--i-mean-it" not in sys.argv:
        print(__doc__)
        print("Refusing to run without --i-mean-it")
        return 2
    GOLDEN.mkdir(exist_ok=True)
    EXAMPLES.mkdir(exist_ok=True)
    for name, instances in build_instances().items():
        schema = infer_many(instances)
        (GOLDEN / f"{name}.json").write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
        for i, inst in enumerate(instances):
            fname = f"{name}.json" if i == 0 else f"{name}__variant{i}.json"
            (EXAMPLES / fname).write_text(json.dumps(inst, indent=2) + "\n")
        print("wrote", name, f"({len(instances)} instance(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
