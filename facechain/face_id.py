"""Face detection + encoding using InsightFace (buffalo_l, ONNX / CPU).

Detects faces in an image and produces a 512-d normalised embedding per face.
Embeddings are compared with cosine similarity; ~0.35+ is a plausible match,
~0.5+ is a strong match for the ArcFace w600k_r50 model used here.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

MATCH_THRESHOLD = 0.35


@dataclass
class Face:
    bbox: tuple[int, int, int, int]          # x1, y1, x2, y2
    det_score: float
    embedding: np.ndarray = field(repr=False)  # 512-d, L2-normalised
    age: int | None = None
    gender: str | None = None

    @property
    def embedding_sha256(self) -> str:
        # hash the quantised bytes so it is stable across runs/machines
        q = np.round(self.embedding.astype(np.float64), 5)
        return hashlib.sha256(q.tobytes()).hexdigest()


@lru_cache(maxsize=1)
def _app():
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640))
    return app


def _load_bgr(image_path: str | Path) -> np.ndarray:
    img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"could not read image: {image_path}")
    return img


def detect_and_encode(image_path: str | Path) -> list[Face]:
    """Return all faces found in the image, largest (most prominent) first."""
    img = _load_bgr(image_path)
    faces = []
    for f in _app().get(img):
        x1, y1, x2, y2 = (int(v) for v in f.bbox)
        emb = np.asarray(f.normed_embedding, dtype=np.float32)
        faces.append(
            Face(
                bbox=(x1, y1, x2, y2),
                det_score=float(f.det_score),
                embedding=emb,
                age=int(f.age) if getattr(f, "age", None) is not None else None,
                gender=("M" if getattr(f, "gender", None) == 1 else "F") if getattr(f, "gender", None) is not None else None,
            )
        )
    faces.sort(key=lambda fc: (fc.bbox[2] - fc.bbox[0]) * (fc.bbox[3] - fc.bbox[1]), reverse=True)
    return faces


def primary_face(image_path: str | Path) -> Face:
    faces = detect_and_encode(image_path)
    if not faces:
        raise ValueError(f"no face detected in {image_path}")
    return faces[0]


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    a = a / (np.linalg.norm(a) + 1e-10)
    b = b / (np.linalg.norm(b) + 1e-10)
    return float(np.dot(a, b))


def save_annotated(image_path: str | Path, faces: list[Face], out_path: str | Path) -> None:
    img = _load_bgr(image_path)
    for i, f in enumerate(faces):
        x1, y1, x2, y2 = f.bbox
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 200, 0), 2)
        cv2.putText(img, f"#{i} {f.det_score:.2f}", (x1, max(0, y1 - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 0), 2)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), img)
