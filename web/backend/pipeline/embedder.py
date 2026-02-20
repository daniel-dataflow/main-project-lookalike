from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

# import numpy as np
# import pandas as pd
# from PIL import Image
# from fashion_clip.fashion_clip import FashionCLIP


def _make_id(product_id: Any, crop_path: str) -> str:
    raw = f"{product_id}|{crop_path}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def run_embedding(
        detections: list[dict[str, Any]],
        brand: str | None = None,
        out_root_dir: str | None = None,
        batch_size: int = 16,
) -> dict[str, Any]:
    # 지연 import
    import numpy as np
    import pandas as pd
    from PIL import Image
    from fashion_clip.fashion_clip import FashionCLIP

    if not detections:
        raise RuntimeError("No detections to embed.")

    # EMBEDDER_OUT_DIR 환경변수로 자동 결정
    out_root_dir = out_root_dir or os.getenv("EMBEDDER_OUT_DIR", "/opt/airflow/data/embeddings")
    brand_name = (brand or "all").lower()
    out_dir = Path(out_root_dir) / brand_name
    out_dir.mkdir(parents=True, exist_ok=True)

    fclip = FashionCLIP(model_name="fashion-clip")

    imgs = []
    rows = []

    for d in detections:
        crop_path = Path(d["crop_path"])
        if not crop_path.exists():
            continue

        try:
            # RGB 3채널 형식으로 강제변환
            img = Image.open(crop_path).convert("RGB")
        except Exception:
            continue

        imgs.append(img)
        rows.append(
            {
                "id": _make_id(d["product_id"], str(crop_path)),
                "product_id": d["product_id"],
                "brand": d.get("brand", brand_name),
                "class_id": d["class_id"],
                "class_name": d["class_name"],
                "confidence": d["confidence"],
                "crop_path": str(crop_path),
            }
        )

    if not imgs:
        raise RuntimeError("No valid crop images found for embedding.")

    emb = fclip.encode_images(images=imgs, batch_size=batch_size)
    embeddings = np.asarray(emb, dtype=np.float32)

    # L2 정규화 (unit norm) - 코사인 유사도 기반 검색에 유리
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings = embeddings / np.clip(norms, 1e-12, None)

    meta = pd.DataFrame(rows)

    emb_path = out_dir / "embeddings.npy"
    meta_path = out_dir / "metadata.csv"
    np.save(emb_path, embeddings)
    meta.to_csv(meta_path, index=False)

    return {
        "brand": brand_name,
        "embeddings_path": str(emb_path),
        "metadata_path": str(meta_path),
        "rows": int(len(meta)),
        "dim": int(embeddings.shape[1]),
    }















