"""End-to-end pipeline:

    face scan  ->  reverse-image / social search  ->  blockchain anchor + re-verify

Usage:
    python pipeline.py --image-url "https://.../face.jpg"
    python pipeline.py --image samples/input_face.jpg            # will upload for search
    python pipeline.py --image samples/x.jpg --image-url https://.../x.jpg
    python pipeline.py --chain rpc --image-url ...               # anchor on a real testnet

Outputs a JSON report under data/ that scripts/verify_onchain.py can re-check.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

from facechain import face_id, fingerprint as fp_mod, reverse_search
from facechain.blockchain import Chain

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # keep Windows consoles from choking on odd chars
    except Exception:  # noqa: BLE001
        pass

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SAMPLES_DIR = ROOT / "samples"


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(url, timeout=60, headers={"User-Agent": "facechain/1.0"})
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


def _print_header(step: str) -> None:
    print(f"\n{'=' * 70}\n{step}\n{'=' * 70}")


def run(args: argparse.Namespace) -> dict:
    load_dotenv(ROOT / ".env")
    DATA_DIR.mkdir(exist_ok=True)

    image_url = args.image_url or os.getenv("SOURCE_IMAGE_URL")
    local_path = Path(args.image) if args.image else None

    if not image_url and not local_path:
        sys.exit("provide --image and/or --image-url (or set SOURCE_IMAGE_URL in .env)")

    # normalise: we need BOTH a local file (for face detection) and a URL (for Lens)
    if image_url and not local_path:
        local_path = _download(image_url, SAMPLES_DIR / "input_face.jpg")
        print(f"downloaded scan -> {local_path.relative_to(ROOT)}")
    elif local_path and not image_url:
        if not local_path.exists():
            sys.exit(f"no such file: {local_path}")
        print("no --image-url given; uploading the scan so Google Lens can fetch it ...")
        image_url = reverse_search.upload_for_search(str(local_path))
        print(f"hosted scan -> {image_url}")

    # ------------------------------------------------------------------ 1
    _print_header("STEP 1/3  Face detection + encoding  (InsightFace buffalo_l)")
    faces = face_id.detect_and_encode(local_path)
    if not faces:
        sys.exit("no face detected in the scan image")
    face = faces[0]
    face_id.save_annotated(local_path, faces, DATA_DIR / "scan_annotated.jpg")
    print(f"faces found      : {len(faces)}")
    print(f"primary face     : bbox={face.bbox} det_score={face.det_score:.3f} "
          f"age~{face.age} gender={face.gender}")
    print(f"embedding        : {face.embedding.shape[0]}-d, sha256={face.embedding_sha256[:16]}...")
    print(f"annotated image  : {(DATA_DIR / 'scan_annotated.jpg').relative_to(ROOT)}")

    # ------------------------------------------------------------------ 2
    _print_header("STEP 2/3  Reverse-image / social-media search  (SerpAPI Google Lens)")
    matches = reverse_search.google_lens(image_url)
    print(f"Google Lens returned {len(matches)} page matches")
    for m in matches[:8]:
        flag = "  [social]" if m.is_social else ""
        print(f"  - {m.source:<22} {m.link}{flag}")

    match = reverse_search.best_social_match(matches)
    if match is None:
        if not matches:
            sys.exit("search returned no matches; try a different scan image")
        match = matches[0]
        print("\nno social-media domain in results; falling back to top web match")
    print(f"\nselected match   : {match.link}")
    print(f"  source         : {match.source}")
    print(f"  title          : {match.title!r}")
    print(f"  social media   : {match.is_social}")

    # cross-check: does the face on the matched page look like the scan?
    similarity = None
    try:
        if match.thumbnail:
            tmp = DATA_DIR / "match_thumb.jpg"
            _download(match.thumbnail, tmp)
            mfaces = face_id.detect_and_encode(tmp)
            if mfaces:
                similarity = face_id.cosine_similarity(face.embedding, mfaces[0].embedding)
                verdict = "MATCH" if similarity >= face_id.MATCH_THRESHOLD else "weak/no match"
                print(f"  face re-check  : cosine={similarity:.3f}  ->  {verdict}")
    except Exception as e:  # noqa: BLE001 - cross-check is best-effort
        print(f"  face re-check  : skipped ({e})")

    thumb_sha = reverse_search.fetch_thumbnail_sha256(match.thumbnail)
    discovered_at = _now_iso()

    # ------------------------------------------------------------------ 3
    _print_header("STEP 3/3  Blockchain anchor + re-verification")
    payload = fp_mod.build_payload(
        source_image_url=image_url,
        face_embedding_sha256=face.embedding_sha256,
        match_url=match.link,
        match_source=str(match.source),
        match_title=match.title or "",
        match_thumbnail_sha256=thumb_sha,
        match_image_face_similarity=similarity,
        discovered_at=discovered_at,
    )
    fp_hex = fp_mod.fingerprint_hex(payload)
    print(f"canonical payload:\n{fp_mod.canonical_json(payload).decode()}")
    print(f"\nfingerprint (keccak256): {fp_hex}")

    chain = Chain(args.chain)
    addr = chain.connect_or_deploy(force_deploy=args.redeploy)
    print(f"chain backend    : {chain.backend}  chain_id={chain.chain_id}")
    print(f"registry address : {addr}")
    if chain.explorer_address_url(addr):
        print(f"  explorer       : {chain.explorer_address_url(addr)}")

    already = chain.verify(fp_hex)
    if already.found:
        print("\nthis exact fingerprint is already anchored (idempotent) — skipping write")
        receipt = None
    else:
        print("\nanchoring fingerprint on-chain ...")
        receipt = chain.record(fp_hex, match.link)
        print(f"  tx hash        : {receipt.tx_hash}")
        print(f"  block          : #{receipt.block_number}  ({receipt.block_hash})")
        print(f"  record index   : {receipt.index}")
        if receipt.explorer_url:
            print(f"  explorer       : {receipt.explorer_url}")

    # re-verify straight from chain state
    v = chain.verify(fp_hex)
    print("\nRE-VERIFY against on-chain record:")
    print(f"  found          : {v.found}")
    print(f"  anchored at    : {dt.datetime.fromtimestamp(v.timestamp, dt.timezone.utc).isoformat()}")
    print(f"  stored uri     : {v.uri}")
    print(f"  submitter      : {v.submitter}")
    print(f"  total records  : {chain.count()}")

    # tamper check: mutate the payload, recompute, confirm it is NOT on chain
    tampered = dict(payload)
    tampered["match_url"] = payload["match_url"] + "?edited=1"
    tampered_hex = fp_mod.fingerprint_hex(tampered)
    tv = chain.verify(tampered_hex)
    print(f"\ntamper check (altered match_url):")
    print(f"  tampered fingerprint : {tampered_hex}")
    print(f"  on chain?            : {tv.found}   <- expected False (record is tamper-evident)")

    ok = v.found and not tv.found
    print(f"\nPIPELINE RESULT  : {'PASS' if ok else 'FAIL'}")

    report = {
        "created_at": _now_iso(),
        "scan": {
            "image_url": image_url,
            "local_path": str(local_path.relative_to(ROOT)),
            "sha256": hashlib.sha256(local_path.read_bytes()).hexdigest(),
            "faces_detected": len(faces),
            "primary_face": {
                "bbox": list(face.bbox),
                "det_score": face.det_score,
                "age": face.age,
                "gender": face.gender,
                "embedding_sha256": face.embedding_sha256,
            },
        },
        "match": {
            "url": match.link,
            "source": str(match.source),
            "title": match.title,
            "is_social_media": match.is_social,
            "thumbnail": match.thumbnail,
            "thumbnail_sha256": thumb_sha,
            "face_similarity_to_scan": similarity,
        },
        "fingerprint": {
            "algorithm": "keccak256(canonical_json)",
            "payload": payload,
            "value": fp_hex,
        },
        "blockchain": {
            "backend": chain.backend,
            "chain_id": chain.chain_id,
            "registry_address": addr,
            "record_tx": receipt.tx_hash if receipt else None,
            "record_block": receipt.block_number if receipt else None,
            "record_index": receipt.index if receipt else already.__dict__.get("index"),
            "explorer_tx_url": receipt.explorer_url if receipt else None,
            "reverify": {"found": v.found, "timestamp": v.timestamp, "uri": v.uri, "submitter": v.submitter},
            "tamper_check": {"tampered_fingerprint": tampered_hex, "found_on_chain": tv.found},
        },
        "result": "PASS" if ok else "FAIL",
    }
    out = DATA_DIR / f"run_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    latest = DATA_DIR / "latest_run.json"
    latest.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nreport written   : {out.relative_to(ROOT)}  (and data/latest_run.json)")
    return report


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--image", help="local path to the face-scan image")
    p.add_argument("--image-url", help="public URL of the face-scan image (used for Google Lens)")
    p.add_argument("--chain", choices=["local", "rpc"], help="blockchain backend (default: env CHAIN or 'local')")
    p.add_argument("--redeploy", action="store_true", help="force a fresh Registry deployment (rpc backend)")
    args = p.parse_args()
    report = run(args)
    sys.exit(0 if report["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
