from __future__ import annotations

"""검색용 임베딩 모델 로더"""

from typing import Any, List

import numpy as np
from PIL import Image


class EncoderHub:
    """CLIP/SBERT 모델을 로드하고 임베딩 함수를 제공한다."""

    def __init__(self) -> None:
        self._clip_model: Any = None
        self._sbert_model: Any = None

    def load(self) -> None:
        # 모듈 import 시점 실패를 피하기 위해
        # 모델 의존성은 startup 시점에 지연 import한다.
        from fashion_clip.fashion_clip import FashionCLIP
        from sentence_transformers import SentenceTransformer

        self._clip_model = FashionCLIP(model_name="fashion-clip")
        self._sbert_model = SentenceTransformer("jhgan/ko-sroberta-sts")

    def encode_image_clip(self, image: Image.Image) -> List[float]:
        emb = self._clip_model.encode_images([image], batch_size=1)
        return np.asarray(emb[0], dtype=np.float32).tolist()

    def encode_text_sbert(self, text: str) -> List[float]:
        vec = self._sbert_model.encode(text, normalize_embeddings=False)
        return np.asarray(vec, dtype=np.float32).tolist()
