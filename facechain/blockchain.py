"""Blockchain anchoring + re-verification.

Two backends, selected by the CHAIN env var / --chain flag:

  local  (default)  In-process EVM via eth-tester. No node, no funds, no network.
                    The contract is deployed fresh each run. Best for an offline,
                    self-contained demo.

  rpc               Any real EVM chain reachable over JSON-RPC (e.g. Polygon Amoy
                    or Ethereum Sepolia testnet). Needs RPC_URL and PRIVATE_KEY.
                    The deployed contract address is cached per chain-id in
                    data/deployment_<chainid>.json and reused on later runs, so
                    verification can happen in a completely separate process.

Both backends run the same compiled Registry contract and expose the same API.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from web3 import Web3

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "contracts" / "artifacts" / "Registry.json"
DATA_DIR = ROOT / "data"

EXPLORERS = {
    11155111: "https://sepolia.etherscan.io",
    80002: "https://amoy.polygonscan.com",
    137: "https://polygonscan.com",
    1: "https://etherscan.io",
}


@dataclass
class RecordReceipt:
    tx_hash: str
    block_number: int
    block_hash: str
    contract_address: str
    chain_id: int
    index: int
    fingerprint: str
    explorer_url: str | None


@dataclass
class VerifyResult:
    found: bool
    timestamp: int
    uri: str
    submitter: str
    fingerprint: str
    contract_address: str
    chain_id: int


def _load_artifact() -> dict:
    if not ARTIFACT.exists():
        raise RuntimeError(
            f"{ARTIFACT} missing. Run: python scripts/compile_contract.py"
        )
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


class Chain:
    def __init__(self, backend: str | None = None):
        self.backend = (backend or os.getenv("CHAIN") or "local").lower()
        self.artifact = _load_artifact()
        self._deployment_cache: Path | None = None

        if self.backend == "local":
            from eth_tester import EthereumTester, PyEVMBackend
            from web3 import EthereumTesterProvider

            self.tester = EthereumTester(PyEVMBackend())
            self.w3 = Web3(EthereumTesterProvider(self.tester))
            self.account = self.w3.eth.accounts[0]
            self._private_key = None
        elif self.backend == "rpc":
            rpc = os.getenv("RPC_URL")
            pk = os.getenv("PRIVATE_KEY")
            if not rpc or not pk:
                raise RuntimeError("CHAIN=rpc needs RPC_URL and PRIVATE_KEY in the environment / .env")
            self.w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 60}))
            if not self.w3.is_connected():
                raise RuntimeError(f"cannot reach RPC endpoint: {rpc}")
            acct = self.w3.eth.account.from_key(pk)
            self.account = acct.address
            self._private_key = pk
        else:
            raise ValueError(f"unknown chain backend: {self.backend!r} (use 'local' or 'rpc')")

        self.chain_id = self.w3.eth.chain_id
        self.contract = None

    # ---- internal helpers -------------------------------------------------
    def _send(self, func):
        if self._private_key is None:  # eth-tester: unlocked account
            tx_hash = func.transact({"from": self.account})
        else:
            tx = func.build_transaction(
                {
                    "from": self.account,
                    "nonce": self.w3.eth.get_transaction_count(self.account),
                    "chainId": self.chain_id,
                }
            )
            if "gas" not in tx:
                tx["gas"] = int(self.w3.eth.estimate_gas(tx) * 1.3)
            signed = self.w3.eth.account.sign_transaction(tx, self._private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def _deployment_file(self) -> Path:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return DATA_DIR / f"deployment_{self.chain_id}.json"

    def explorer_tx_url(self, tx_hash: str) -> str | None:
        base = EXPLORERS.get(self.chain_id)
        return f"{base}/tx/{tx_hash}" if base else None

    def explorer_address_url(self, address: str) -> str | None:
        base = EXPLORERS.get(self.chain_id)
        return f"{base}/address/{address}" if base else None

    # ---- public API -----------------------------------------------------
    def connect_or_deploy(self, *, force_deploy: bool = False) -> str:
        """Return the Registry address, deploying (and caching) it if needed."""
        abi, bytecode = self.artifact["abi"], self.artifact["bytecode"]

        if self.backend == "rpc" and not force_deploy:
            f = self._deployment_file()
            if f.exists():
                addr = json.loads(f.read_text())["address"]
                self.contract = self.w3.eth.contract(address=addr, abi=abi)
                return addr

        factory = self.w3.eth.contract(abi=abi, bytecode=bytecode)
        rcpt = self._send(factory.constructor())
        addr = rcpt.contractAddress
        self.contract = self.w3.eth.contract(address=addr, abi=abi)

        if self.backend == "rpc":
            self._deployment_file().write_text(
                json.dumps({"address": addr, "chain_id": self.chain_id, "deploy_tx": rcpt.transactionHash.hex()}, indent=2)
            )
        return addr

    def record(self, fingerprint_hex: str, uri: str) -> RecordReceipt:
        assert self.contract is not None, "call connect_or_deploy() first"
        fp = _to_bytes32(fingerprint_hex)
        rcpt = self._send(self.contract.functions.record(fp, uri))
        # pull the index out of the emitted event
        logs = self.contract.events.Recorded().process_receipt(rcpt)
        index = int(logs[0]["args"]["index"]) if logs else self.contract.functions.count().call() - 1
        tx_hash = rcpt.transactionHash.hex()
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        return RecordReceipt(
            tx_hash=tx_hash,
            block_number=rcpt.blockNumber,
            block_hash=rcpt.blockHash.hex() if hasattr(rcpt.blockHash, "hex") else str(rcpt.blockHash),
            contract_address=self.contract.address,
            chain_id=self.chain_id,
            index=index,
            fingerprint=_normalize_hex(fingerprint_hex),
            explorer_url=self.explorer_tx_url(tx_hash),
        )

    def verify(self, fingerprint_hex: str) -> VerifyResult:
        assert self.contract is not None, "call connect_or_deploy() first"
        fp = _to_bytes32(fingerprint_hex)
        found, ts, uri, submitter = self.contract.functions.verify(fp).call()
        return VerifyResult(
            found=bool(found),
            timestamp=int(ts),
            uri=uri,
            submitter=submitter,
            fingerprint=_normalize_hex(fingerprint_hex),
            contract_address=self.contract.address,
            chain_id=self.chain_id,
        )

    def count(self) -> int:
        assert self.contract is not None
        return self.contract.functions.count().call()


def _normalize_hex(h: str) -> str:
    return h if h.startswith("0x") else "0x" + h


def _to_bytes32(h: str) -> bytes:
    b = bytes.fromhex(_normalize_hex(h)[2:])
    if len(b) != 32:
        raise ValueError(f"fingerprint must be 32 bytes, got {len(b)}")
    return b
