from __future__ import annotations

import json
import logging
import os
import re
import threading
from io import BytesIO
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Any, List, Optional, Sequence

import numpy as np
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from PIL import Image

from search_logic import SearchConfig, SearchService

# YOLO 분리 설계 모듈
from yolo_router import router as yolo_router
from yolo_service import yolo_detector

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
            # "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
            "jhgan/ko-sroberta-sts"
        )

    def encode_image_clip(self, image: Image.Image) -> List[float]:
        emb = self._clip_model.encode_images([image], batch_size=1)
        return np.asarray(emb[0], dtype=np.float32).tolist()

    def encode_text_sbert(self, text: str) -> List[float]:
        vec = self._sbert_model.encode(text, normalize_embeddings=False)
        return np.asarray(vec, dtype=np.float32).tolist()


class QueryRewriter:
    """Qwen 기반 검색어 rewrite 래퍼."""

    def __init__(self) -> None:
        # 기본값은 off. 운영 중 지연/자원 이슈가 있으면 env만 바꿔 즉시 비활성화 가능하다.
        self._enabled = os.getenv("ENABLE_QUERY_REWRITE", "false").lower() == "true"
        # env가 없을 때만 3B 기본 모델을 사용한다.
        self._model_name = os.getenv("QUERY_REWRITE_MODEL", "Qwen/Qwen2.5-3B-Instruct")
        self._max_new_tokens = int(os.getenv("QUERY_REWRITE_MAX_NEW_TOKENS", "48"))
        self._timeout_sec = float(os.getenv("QUERY_REWRITE_TIMEOUT_SEC", "3.0"))
        # 짧은 질의일수록 rewrite를 적용하고, 너무 긴 질의는 그대로 사용한다.
        self._max_text_len = int(os.getenv("QUERY_REWRITE_MAX_TEXT_LEN", "20"))
        self._cache_max_size = int(os.getenv("QUERY_REWRITE_CACHE_SIZE", "512"))
        # true일 때만 startup 백그라운드 preload 수행 (기본은 off)
        self._preload_on_startup = os.getenv("QUERY_REWRITE_PRELOAD_ON_STARTUP", "false").lower() == "true"
        self._tokenizer: Any = None
        self._model: Any = None
        self._load_attempted = False
        self._load_lock = threading.Lock()
        self._cache: "OrderedDict[str, str]" = OrderedDict()
        self._executor = ThreadPoolExecutor(max_workers=1)
        # 모델코드/품번 형태 키워드는 rewrite에서 제외
        self._model_code_pattern = re.compile(r"\b[A-Z]{1,5}\d{6,}\b")

    def load(self) -> None:
        if not self._enabled:
            logger.info("Query rewrite disabled.")
            return

        with self._load_lock:
            if self._tokenizer is not None and self._model is not None:
                return

            logger.info("Query rewrite model loading: %s", self._model_name)
            from transformers import AutoModelForCausalLM, AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(self._model_name)
            self._model = AutoModelForCausalLM.from_pretrained(
                self._model_name,
                torch_dtype="auto",
            )
            logger.info("Query rewrite model loaded.")

    def preload_in_background(self) -> None:
        """startup 지연을 피하기 위해 rewrite 모델을 백그라운드에서 미리 올린다."""
        if not self._enabled:
            return
        if not self._preload_on_startup:
            return
        if self._load_attempted:
            return
        self._load_attempted = True
        threading.Thread(target=self._safe_load, daemon=True).start()

    def _safe_load(self) -> None:
        try:
            self.load()
        except Exception as exc:
            logger.warning("Query rewrite background load failed: %s", exc)

    def _is_ready(self) -> bool:
        return self._tokenizer is not None and self._model is not None

    def _ensure_loaded(self) -> bool:
        """텍스트 검색이 실제 발생했을 때만 rewrite 모델을 로드한다."""
        if not self._enabled:
            return False
        if self._is_ready():
            return True
        if self._load_attempted:
            return False

        self._load_attempted = True
        try:
            self.load()
            return self._model is not None and self._tokenizer is not None
        except Exception as exc:
            logger.warning("Query rewrite lazy load failed: %s", exc)
            return False

    def _rewrite_core(self, text: str) -> str:
        """모델 추론 부분만 분리. timeout 제어는 상위에서 수행한다."""
        if not self._is_ready():
            return text

        base = text.strip()
        if not base:
            return base

        system_prompt = (
            "당신은 패션 검색 질의를 보완하는 도우미입니다.\n"
            "출력은 한국어 한 줄 검색어만 출력하세요.\n"
            "짧은 질의(예: '검정 자켓')는 검색 품질을 위해 색상/카테고리/핏 관련 표현을 최대 2~3개까지만 보완할 수 있습니다.\n"
            "단, 상황(면접/하객/데이트 등)은 입력에 명시된 경우에만 포함하세요.\n"
            "근거 없는 브랜드/성별/시즌/가격 정보는 추가하지 마세요.\n"
            "출력이 비어 있거나 확신이 낮으면 원문과 동일하게 출력하세요"
        )

        user_prompt = (
            f"사용자 검색 질의: {base}\n\n"
            "요청:\n"
            "- 검색용 한 줄 문장으로 다시 작성하세요.\n"
            "- 입력이 짧고 모호하면 색상/아이템/핏 중심으로 최대 2~3개 속성만 보완하세요.\n"
            "- 명백한 오타/띄어쓰기 오류는 수정하세요.\n"
            "- 입력에 없는 상황(면접/하객/데이트 등)은 추가하지 마세요.\n"
            "- 설명 없이 최종 검색 문장만 출력하세요."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            # 채팅 템플릿을 사용해 모델 출력 형식을 안정화한다.
            input_ids = self._tokenizer.apply_chat_template(
                messages,
                add_generation_prompt=True,
                return_tensors="pt",
            ).to(self._model.device)
            output_ids = self._model.generate(
                input_ids,
                max_new_tokens=self._max_new_tokens,
                do_sample=False,
                temperature=0.0,
            )
            generated_ids = output_ids[0][input_ids.shape[1] :]
            rewritten_raw = self._tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
            payload = self._extract_json_payload(rewritten_raw)
            search_text = payload.get("search_text")
            if isinstance(search_text, str) and search_text.strip():
                return search_text.strip()
            return base
        except Exception as exc:
            logger.warning("Query rewrite failed, fallback to original text: %s", exc)
            return base

    def rewrite_if_needed(
        self,
        text: str,
        has_image: bool,
        category: Optional[str | Sequence[str]],
    ) -> str:
        """
        성능/품질 균형 규칙:
        - 짧은 텍스트는 rewrite 대상, 긴 텍스트는 skip
        - 이미지+텍스트는 skip
        - 카테고리 선택 시 skip
        - 품번/모델코드 형태 텍스트는 skip
        - 모델 준비 안 됐으면 즉시 원문 fallback
        - 5초(timeout) 초과 시 원문 fallback
        """
        base = (text or "").strip()
        if not base:
            return base
        if not self._enabled:
            return base
        if len(base) > self._max_text_len:
            return base
        if has_image:
            return base
        if category is not None and str(category).strip():
            return base
        if self._model_code_pattern.search(base):
            return base

        cached = self._cache.get(base)
        if cached is not None:
            self._cache.move_to_end(base)
            logger.info("rewrite cache hit: base='%s' -> rewritten='%s'", base, cached)
            return cached


        if not self._ensure_loaded():
            return base

        future = self._executor.submit(self._rewrite_core, base)
        try:
            rewritten = future.result(timeout=self._timeout_sec).strip()
        except TimeoutError:
            logger.info("Query rewrite timeout(%.1fs), fallback to original text", self._timeout_sec)
            return base
        except Exception as exc:
            logger.warning("Query rewrite failed, fallback to original text: %s", exc)
            return base

        if not rewritten:
            return base

        logger.info("rewrite applied: base='%s' -> rewritten='%s'", base, rewritten)


        # LRU 캐시 저장
        self._cache[base] = rewritten
        self._cache.move_to_end(base)
        if len(self._cache) > self._cache_max_size:
            self._cache.popitem(last=False)
        return rewritten

    @staticmethod
    def _extract_json_payload(raw_text: str) -> dict:
        # 모델이 코드블록/여분 텍스트를 섞어도 첫 JSON object를 복원한다.
        text = raw_text.strip()
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except Exception:
            return {}


app = FastAPI(title="Lookalike ML Inference API", version="1.1.0")

# 기존 라우터 말고 새로 만든 YOLO 라우터를 붙인다.
app.include_router(yolo_router)

encoders = EncoderHub()
rewriter = QueryRewriter()
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
    rewriter.preload_in_background()
    
    # 별도로 YOLO 모델 탑재 (별도 스레드/프로세스처럼 독립적)
    yolo_detector.load()

    cfg = SearchConfig(
        index_name=os.getenv("ML_SEARCH_INDEX", "products"),
        es_url=os.getenv("ELASTICSEARCH_URL", "http://elasticsearch:9200"),
        clip_field="image_vector",
        sbert_field="text_vector",
        # ML 점수 키를 product_id로 설정(env)
        id_field=os.getenv("ML_ID_FIELD", "product_code"),
        # ML은 후보를 넉넉히 반환하고, 최종 노출 개수(6개)는 FastAPI 백엔드에서 제한한다.
        # dedupe/hydration 단계에서 후보가 줄어드는 경우를 대비하기 위함.
        final_k=int(os.getenv("ML_CANDIDATE_K", "24")),
        w_clip=0.7,
        w_sbert=0.3,
        fusion_method="rrf",
    )
    service = SearchService(
        config=cfg,
        clip_image_encoder=encoders.encode_image_clip,
        sbert_text_encoder=encoders.encode_text_sbert,
        # rewrite는 엔드포인트 레벨 조건으로 제어한다.
        query_rewriter=None,
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
    gender: Optional[str] = Form(default=None),
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
    gender_value = _parse_category(gender)
    text_value = text
    if text_value is not None:
        text_value = rewriter.rewrite_if_needed(
            text=text_value,
            has_image=pil_img is not None,
            category=category_value,
        )
    try:
        results = service.search(image=pil_img, text=text_value, category=category_value, gender=gender_value)
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
    gender: Optional[str] = Form(default=None),
) -> dict:
    """
    백엔드 호환 응답 포맷:
    - ml_product_scores: {product_id: score}
    - gender/applied_category/tags: 기존 키 유지
    """
    payload = await search(image=image, text=text, category=category, gender=gender)

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
