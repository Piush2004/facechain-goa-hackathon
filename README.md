# FaceChain — Face Identification & Blockchain Verification

**HH Goa 2026 · Shortlisting Task 3**

A pipeline that takes a face scan, finds a matching post on social media / the web
via a real reverse-image search, then anchors a fingerprint of that discovery on a
blockchain so it can be independently re-verified and shown to be tamper-evident.

```
  face scan image
        │
        ▼
┌───────────────────────┐   InsightFace buffalo_l (ArcFace, ONNX / CPU)
│ 1. Face detection +   │   → bounding box, det score, age/gender
│    512-d encoding     │   → SHA-256 of the embedding
└───────────┬───────────┘
            ▼
┌───────────────────────┐   SerpAPI · Google Lens engine
│ 2. Reverse-image /    │   → real pages that contain this image
│    social search      │   → filter to social-media domains, pick best
└───────────┬───────────┘   → re-check: does the face on that page match?
            ▼
┌───────────────────────┐   keccak256(canonical JSON of the discovery)
│ 3. Blockchain anchor  │   → Registry.record(fingerprint, url)  → tx hash
│    + re-verification  │   → Registry.verify(fingerprint)       → found ✓
└───────────────────────┘   → tampered fingerprint → not found ✗
```

## What each step does

### 1. Face identification — `facechain/face_id.py`
Uses **InsightFace** (`buffalo_l` pack: SCRFD detector + ArcFace `w600k_r50`
recogniser, run on CPU via `onnxruntime`). For each face it produces a
512-dimensional L2-normalised embedding. Faces are compared with cosine
similarity (`≥ 0.35` plausible, `≥ 0.5` strong). A stable SHA-256 of the
quantised embedding goes into the fingerprint.

### 2. Social media / web search — `facechain/reverse_search.py`
A **genuine search**, not a hard-coded result. The face-scan image URL is sent to
**SerpAPI's Google Lens engine**, which returns the actual web pages Google finds
containing that image (`exact_matches` + `visual_matches`). Results are filtered
to social-media domains (instagram, x/twitter, facebook, linkedin, tiktok,
youtube, reddit, threads, …) and the best one is selected. The pipeline then
downloads the matched page's thumbnail and re-runs face recognition on it to
confirm the same person appears there.

If you pass a local image with no public URL, the tool uploads it to a temporary
host (`tmpfiles.org`) so Lens can fetch it; passing `--image-url` is preferred.

### 3. Blockchain verification — `facechain/blockchain.py` + `contracts/Registry.sol`
The discovery (scan URL, embedding hash, match URL, match source/title, thumbnail
hash, face-similarity score, discovery timestamp) is serialised to **canonical
JSON** (sorted keys, no whitespace) and hashed with **keccak256** to a 32-byte
fingerprint. That fingerprint + the match URL are written on-chain via the
`Registry` smart contract:

```solidity
function record(bytes32 fingerprint, string uri) external returns (uint256);
function verify(bytes32 fingerprint) external view
    returns (bool found, uint256 timestamp, string uri, address submitter);
```

Re-verification recomputes the fingerprint from the stored payload and calls
`verify()`. Because any change to the discovery data changes the keccak256
output, the on-chain record is tamper-evident — the pipeline demonstrates this by
mutating one field and showing the altered fingerprint is **not** on chain.

## Which blockchain

| Backend | What it is | When to use |
|---|---|---|
| **`local`** (default) | In-process EVM via **`eth-tester` / py-evm**. The real `Registry` contract is compiled and deployed, real transactions are mined into blocks with real block hashes — just no external node, no funds, no network. | Self-contained offline demo. Record → verify → tamper-check all happen in one run. |
| **`rpc`** | Any real EVM chain over JSON-RPC — e.g. **Polygon Amoy** or **Ethereum Sepolia** testnet. The contract is deployed once and its address cached in `data/deployment_<chainid>.json`, so `scripts/verify_onchain.py` can re-verify from a separate process and you get a public block-explorer link. | Showing a permanent, publicly auditable record. Needs a funded **test** wallet. |

The demo recording uses `local`. Everything also works against a public testnet by
setting `CHAIN=rpc`, `RPC_URL`, and `PRIVATE_KEY` in `.env`.

## Setup

Requires **Python 3.11 or 3.12** (InsightFace / onnxruntime wheels).

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env        # then edit .env
```

Put your SerpAPI key in `.env`:

```
SERPAPI_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
SOURCE_IMAGE_URL=https://upload.wikimedia.org/wikipedia/commons/c/c3/Sundar_Pichai_-_2023_%28cropped%29.jpg
```

First run downloads the InsightFace `buffalo_l` model pack (~280 MB) to
`~/.insightface/`.

## Run

End-to-end pipeline:

```bash
# use the SOURCE_IMAGE_URL from .env
python pipeline.py

# or an explicit scan image
python pipeline.py --image-url "https://.../some-face.jpg"
python pipeline.py --image samples/input_face.jpg          # local file, auto-hosted for search

# anchor on a real testnet instead of the in-process chain
python pipeline.py --chain rpc
```

Independent re-verification of the last run:

```bash
python scripts/verify_onchain.py                # reads data/latest_run.json
python scripts/verify_onchain.py --chain rpc    # query the deployed testnet contract
```

Recompile the contract (only if you edit the `.sol`):

```bash
python scripts/compile_contract.py
```

## Output

Each run writes a JSON report to `data/run_<timestamp>.json` (and
`data/latest_run.json`) containing the face data, the selected match, the full
canonical payload, the fingerprint, the transaction hash, and the re-verify /
tamper-check results. `data/scan_annotated.jpg` shows the detected face box.

## Repo layout

```
pipeline.py                     end-to-end orchestrator
facechain/
  face_id.py                    InsightFace detection + encoding + comparison
  reverse_search.py             SerpAPI Google Lens + social-domain filtering
  fingerprint.py                canonical JSON + keccak256 fingerprint
  blockchain.py                 local (eth-tester) + rpc (testnet) backends
contracts/
  Registry.sol                  the smart contract
  artifacts/Registry.json       committed ABI + bytecode (no solc needed at runtime)
scripts/
  compile_contract.py           Registry.sol -> artifacts/Registry.json
  verify_onchain.py             standalone re-verification
```

## Known limitations

- **Search recall depends on Google's index.** Reverse image search only finds a
  social post if Google Lens has indexed that image. Well-known people work
  reliably; a private person with few indexed photos may return no social match,
  in which case the pipeline falls back to the top web result and says so.
- **SerpAPI free tier** is ~100 searches/month; the Lens engine occasionally
  returns sparse `visual_matches` and a re-query helps.
- **Face re-check is best-effort.** It runs on the match's *thumbnail*, which can
  be low-resolution or show a different crop, so a low cosine score doesn't always
  mean a wrong match. It's a sanity signal, not a gate.
- **`local` chain is ephemeral.** State does not survive the process. Use
  `CHAIN=rpc` against a testnet for a persistent, publicly verifiable record.
- **No key management / gas strategy** for the `rpc` backend beyond a single
  private key and a 1.3× gas-estimate buffer — it targets free testnets, not
  mainnet.
- **Not a biometric identity system.** This is a hackathon pipeline; using face
  search to identify real people has serious privacy and consent implications.
  Use only on images you are authorised to process.

## License

MIT
