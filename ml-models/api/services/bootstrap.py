from __future__ import annotations

"""공용 ML 런타임 초기화 로직."""

import logging
import os
from dataclasses import dataclass

from fastapi import FastAPI

from search_logic import SearchConfig, SearchService
from services.encoder_hub import EncoderHub
from services.query_rewriter import QueryRewriter
from services.yolo_service import yolo_detector

logger = logging.getLogger(__name__)


@dataclass
class SearchRuntime:
    """요청 처리 시 재사용할 공용 객체 묶음."""

    service: SearchService
    rewriter: QueryRewriter


def initialize_runtime(app: FastAPI) -> None:
    """모델을 한 번만 로드하고 재사용 가능한 런타임을 app.state에 저장한다."""
    logger.info("AI 모델 적재 시작")

    encoders = EncoderHub()
    rewriter = QueryRewriter()

    encoders.load()
    rewriter.preload_in_background()
    yolo_detector.load()

    cfg = SearchConfig(
        index_name=os.getenv("ML_SEARCH_INDEX", "products"),
        es_url=os.getenv("ELASTICSEARCH_URL", "http://elasticsearch:9200"),
        clip_field="image_vector",
        sbert_field="text_vector",
        id_field=os.getenv("ML_ID_FIELD", "product_code"),
        final_k=int(os.getenv("ML_CANDIDATE_K", "24")),
        w_clip=0.7,
        w_sbert=0.3,
        fusion_method="rrf",
    )
    app.state.search_runtime = SearchRuntime(
        service=SearchService(
            config=cfg,
            clip_image_encoder=encoders.encode_image_clip,
            sbert_text_encoder=encoders.encode_text_sbert,
            query_rewriter=None,
        ),
        rewriter=rewriter,
    )
    logger.info("AI 모델 적재 완료")
