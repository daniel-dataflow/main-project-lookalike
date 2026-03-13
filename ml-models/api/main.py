from __future__ import annotations

"""ML 추론 서버 엔트리포인트.

FastAPI 애플리케이션 생성, 수명주기 연결, 라우터 등록만 담당한다.
"""

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from routers.search import router as search_router
from routers.yolo import router as yolo_router
from services.bootstrap import initialize_runtime

# 기본 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작 시 공용 런타임을 초기화한다."""
    initialize_runtime(app)
    yield

# FastAPI 애플리케이션 생성
app = FastAPI(
    title="Lookalike ML Inference API",  # Swagger 문서에 표시되는 이름
    version="1.1.0",  # API 버전
    lifespan=lifespan,  # 서버 시작/종료 시 실행할 수명주기 함수
)
# 라우터 등록
app.include_router(search_router)
app.include_router(yolo_router)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8914, reload=True)
