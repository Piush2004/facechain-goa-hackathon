"""Independently re-verify a pipeline run against the blockchain record.

    python scripts/verify_onchain.py                      # uses data/latest_run.json
    python scripts/verify_onchain.py data/run_XXXX.json
    python scripts/verify_onchain.py --chain rpc          # query a real testnet

What it does:
  1. Re-computes the keccak256 fingerprint from the payload stored in the report
     and checks it matches the value recorded at pipeline time (determinism).
  2. Calls Registry.verify() on-chain and prints whether the fingerprint is
     anchored, when, and by whom.
  3. Re-computes a deliberately tampered fingerprint and shows it is absent.

Note: with the `local` (eth-tester) backend the chain is in-process and does not
persist between runs, so this script re-deploys + re-anchors from the report to
demonstrate the round-trip. With `--chain rpc` it queries the already-deployed
contract with no writes.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from facechain import fingerprint as fp_mod  # noqa: E402
from facechain.blockchain import Chain  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("report", nargs="?", default=str(ROOT / "data" / "latest_run.json"))
    p.add_argument("--chain", choices=["local", "rpc"])
    args = p.parse_args()

    load_dotenv(ROOT / ".env")
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    payload = report["fingerprint"]["payload"]
    recorded_fp = report["fingerprint"]["value"]

    print(f"report            : {args.report}")
    print(f"match url          : {report['match']['url']}")
    print(f"recorded fingerprint: {recorded_fp}")

    # 1. determinism
    recomputed = fp_mod.fingerprint_hex(payload)
    print(f"recomputed         : {recomputed}")
    if recomputed.lower() != recorded_fp.lower():
        sys.exit("FAIL: fingerprint is not reproducible from the stored payload")
    print("  -> fingerprint reproduces exactly from the stored payload  [OK]")

    # 2. on-chain
    backend = args.chain or report["blockchain"]["backend"]
    chain = Chain(backend)
    print(f"\nchain backend      : {chain.backend}  chain_id={chain.chain_id}")

    if chain.backend == "rpc":
        addr = chain.connect_or_deploy()
        print(f"registry address   : {addr}  (reused, no writes)")
    else:
        addr = chain.connect_or_deploy()
        print(f"registry address   : {addr}  (fresh in-process deploy)")
        chain.record(recomputed, report["match"]["url"])
        print("re-anchored the fingerprint from the report into the in-process chain")

    v = chain.verify(recomputed)
    print("\nRegistry.verify():")
    print(f"  found            : {v.found}")
    if v.found:
        print(f"  anchored at      : {dt.datetime.fromtimestamp(v.timestamp, dt.timezone.utc).isoformat()}")
        print(f"  stored uri       : {v.uri}")
        print(f"  submitter        : {v.submitter}")

    # 3. tamper
    tampered = dict(payload)
    tampered["match_url"] = payload["match_url"] + "?edited=1"
    tv = chain.verify(fp_mod.fingerprint_hex(tampered))
    print(f"\ntampered fingerprint on chain? : {tv.found}   (expected False)")

    ok = v.found and not tv.found and recomputed.lower() == recorded_fp.lower()
    print(f"\nVERIFICATION RESULT : {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
