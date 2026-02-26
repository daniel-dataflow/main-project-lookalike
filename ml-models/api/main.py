from __future__ import annotations

import logging
import os
from io import BytesIO
from typing import Any, List, Optional, Sequence

import numpy as np
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from PIL import Image

from search_logic import SearchConfig, SearchService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class EncoderHub:
    """모델 로딩/인코딩 래퍼."""

    def __init__(self) -> None:
        self._clip_model: Any = None
        self._sbert_model: Any = None

    def load(self) -> None:
        from fashion_clip.fashion_clip import FashionCLIP
        from sentence_transformers import SentenceTransformer

        self._clip_model = FashionCLIP(model_name="fashion-clip")
        self._sbert_model = SentenceTransformer(
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )

    def encode_image_clip(self, image: Image.Image) -> List[float]:
        emb = self._clip_model.encode_images([image], batch_size=1)
        return np.asarray(emb[0], dtype=np.float32).tolist()

    def encode_text_sbert(self, text: str) -> List[float]:
        vec = self._sbert_model.encode(text, normalize_embeddings=False)
        return np.asarray(vec, dtype=np.float32).tolist()


app = FastAPI(title="Lookalike ML Inference API", version="1.1.0")
encoders = EncoderHub()
service: Optional[SearchService] = None


def _parse_category(category: Optional[str]) -> Optional[str | Sequence[str]]:
    if category is None:
        return None
    trimmed = category.strip()
    if not trimmed:
        return None
    if "," in trimmed:
        parts = [x.strip() for x in trimmed.split(",") if x.strip()]
        return parts if parts else None
    return trimmed


@app.on_event("startup")
def on_startup() -> None:
    global service

    logger.info("AI 모델 적재 시작")
    encoders.load()

    cfg = SearchConfig(
        index_name=os.getenv("ML_SEARCH_INDEX", "products"),
        es_url=os.getenv("ELASTICSEARCH_URL", "http://elasticsearch:9200"),
        clip_field="image_vector",
        sbert_field="text_vector",
        # ML 점수 키를 product_code로 고정해 FastAPI의 DB hydration(model_code)과 맞춘다.
        id_field=os.getenv("ML_ID_FIELD", "product_code"),
        final_k=6,
        w_clip=0.7,
        w_sbert=0.3,
        fusion_method="rrf",
    )
    service = SearchService(
        config=cfg,
        clip_image_encoder=encoders.encode_image_clip,
        sbert_text_encoder=encoders.encode_text_sbert,
    )
    logger.info("AI 모델 적재 완료")


@app.get("/health")
def health_check() -> dict:
    return {"status": "ok", "message": "ML Engine is running"}


@app.post("/search")
async def search(
    image: Optional[UploadFile] = File(default=None),
    text: Optional[str] = Form(default=None),
    category: Optional[str] = Form(default=None),
) -> dict:
    if service is None:
        raise HTTPException(status_code=500, detail="Search service is not initialized.")

    pil_img: Optional[Image.Image] = None
    if image is not None:
        data = await image.read()
        if not data:
            raise HTTPException(status_code=400, detail="업로드된 이미지가 비어 있습니다.")
        try:
            pil_img = Image.open(BytesIO(data)).convert("RGB")
        except Exception as exc:
            raise HTTPException(status_code=400, detail="유효한 이미지 파일이 아닙니다.") from exc

    category_value = _parse_category(category)
    try:
        results = service.search(image=pil_img, text=text, category=category_value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"검색 중 오류가 발생했습니다: {exc}") from exc

    return {"count": len(results), "results": results}


@app.post("/predict_vector")
async def predict_vector(
    image: Optional[UploadFile] = File(default=None),
    text: Optional[str] = Form(default=None),
    category: Optional[str] = Form(default=None),
) -> dict:
    """
    백엔드 호환 응답 포맷:
    - ml_product_scores: {product_id: score}
    - gender/applied_category/tags: 기존 키 유지
    """
    payload = await search(image=image, text=text, category=category)

    ml_product_scores = {
        str(item.get("id")): float(item.get("score", 0.0))
        for item in payload.get("results", [])
        if item.get("id") is not None
    }

    return {
        "ml_product_scores": ml_product_scores,
        "gender": None,
        "applied_category": None,
        "tags": [],
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8914, reload=True)
