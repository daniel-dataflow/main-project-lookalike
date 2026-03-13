from __future__ import annotations

"""텍스트 임베딩 전에 사용할 검색어 리라이트 서비스."""

import json
import logging
import os
import re
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)


class QueryRewriter:
    """짧은 패션 검색어를 지연 로딩과 캐시를 이용해 보정한다."""


    def __init__(self) -> None:
        self._enabled = os.getenv("ENABLE_QUERY_REWRITE", "false").lower() == "true"
        self._model_name = os.getenv("QUERY_REWRITE_MODEL", "Qwen/Qwen2.5-3B-Instruct")
        self._max_new_tokens = int(os.getenv("QUERY_REWRITE_MAX_NEW_TOKENS", "48"))
        self._timeout_sec = float(os.getenv("QUERY_REWRITE_TIMEOUT_SEC", "3.0"))
        self._max_text_len = int(os.getenv("QUERY_REWRITE_MAX_TEXT_LEN", "20"))
        self._cache_max_size = int(os.getenv("QUERY_REWRITE_CACHE_SIZE", "512"))
        self._preload_on_startup = (
            os.getenv("QUERY_REWRITE_PRELOAD_ON_STARTUP", "false").lower() == "true"
        )
        self._tokenizer: Any = None
        self._model: Any = None
        self._load_attempted = False
        self._load_lock = threading.Lock()
        self._cache: "OrderedDict[str, str]" = OrderedDict()
        self._executor = ThreadPoolExecutor(max_workers=1)
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
        """서버 시작을 막지 않으면서 리라이트 모델을 백그라운드에서 미리 올린다."""
        if not self._enabled or not self._preload_on_startup or self._load_attempted:
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
        """조건에 맞는 첫 텍스트 요청에서 리라이트 모델을 지연 로드한다."""
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
        """모델 준비가 끝난 뒤 실제 LLM 리라이트를 수행한다."""
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
        """설정한 조건에 맞는 요청에만 리라이트를 적용한다."""
        base = (text or "").strip()
        if not base or not self._enabled or len(base) > self._max_text_len:
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
        self._cache[base] = rewritten
        self._cache.move_to_end(base)
        if len(self._cache) > self._cache_max_size:
            self._cache.popitem(last=False)
        return rewritten


    @staticmethod
    def _extract_json_payload(raw_text: str) -> dict:
        """모델 출력에 잡음이 섞여 있어도 첫 JSON 객체를 복원한다."""
        text = raw_text.strip()
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except Exception:
            return {}
