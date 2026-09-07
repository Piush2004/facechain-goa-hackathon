"""Compile contracts/Registry.sol -> contracts/artifacts/Registry.json

Run this only when the Solidity source changes. The committed artifact lets the
pipeline deploy the contract without a Solidity toolchain on the user's machine.
"""
from __future__ import annotations

import json
from pathlib import Path

import solcx

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "contracts" / "Registry.sol"
OUT = ROOT / "contracts" / "artifacts" / "Registry.json"
SOLC_VERSION = "0.8.26"


def main() -> None:
    if SOLC_VERSION not in [str(v) for v in solcx.get_installed_solc_versions()]:
        print(f"Installing solc {SOLC_VERSION} ...")
        solcx.install_solc(SOLC_VERSION)

    compiled = solcx.compile_source(
        SRC.read_text(encoding="utf-8"),
        output_values=["abi", "bin"],
        solc_version=SOLC_VERSION,
    )
    key = next(k for k in compiled if k.endswith(":Registry"))
    artifact = {
        "contractName": "Registry",
        "solcVersion": SOLC_VERSION,
        "abi": compiled[key]["abi"],
        "bytecode": "0x" + compiled[key]["bin"],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)} ({len(artifact['bytecode'])} bytes of bytecode hex)")


if __name__ == "__main__":
    main()
