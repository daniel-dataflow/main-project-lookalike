"""
Lookalike - FastAPI 메인 애플리케이션
패션 유사 상품 검색 웹 서비스
"""
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn
import os
import logging

from .config import get_settings
from .database import init_all_databases, close_all_databases
from .routers import auth_router, products_router, posts_router, search_router, inquiries_router, admin_router, logs_router, metrics_router

# ──────────────────────────────────────
# 로깅 설정
# ──────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────
# 앱 생명주기 (startup / shutdown)
# ──────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작 시 DB 연결, 종료 시 DB 연결 해제"""
    logger.info("🚀 앱 시작 - 데이터베이스 연결 초기화")
    try:
        init_all_databases()
    except Exception as e:
        logger.warning(f"⚠️ DB 연결 초기화 중 일부 실패 (앱은 계속 실행): {e}")
    
    # Elasticsearch 인덱스 초기화
    logger.info("📊 Elasticsearch 인덱스 초기화")
    try:
        from .core.elasticsearch_setup import (
            init_elasticsearch_index,
            init_metric_index,
            init_product_index,
        )
        init_elasticsearch_index()    # container-logs
        init_metric_index()           # container-metrics
        init_product_index()          # products (ML 임베딩 + VLM 설명용)
    except Exception as e:
        logger.warning(f"⚠️ Elasticsearch 인덱스 초기화 실패: {e}")
    
    # 백그라운드 로그 및 메트릭 수집 서비스 시작
    logger.info("🔄 백그라운드 수집 서비스 시작")
    import asyncio
    from .services.log_collector import LogCollector
    from .services.metric_collector import MetricCollector
    from .services.kafka_metric_consumer import KafkaMetricConsumer
    
    log_collector = LogCollector()
    metric_collector = MetricCollector()
    
    # Kafka → Elasticsearch 메트릭 컨슈머 시작
    kafka_metric_consumer = KafkaMetricConsumer()
    kafka_metric_consumer.start()
    
    # 백그라운드 태스크 생성
    log_task = asyncio.create_task(log_collector.start_background_collection())
    metric_task = asyncio.create_task(metric_collector.start())
    
    yield
    
    # 종료 시 태스크 취소
    logger.info("🛑 앱 종료 - 백그라운드 서비스 중지")
    log_task.cancel()
    metric_task.cancel()
    kafka_metric_consumer.stop()
    
    logger.info("🛑 앱 종료 - 데이터베이스 연결 해제")
    close_all_databases()


# ──────────────────────────────────────
# FastAPI 앱 생성
# ──────────────────────────────────────
settings = get_settings()

app = FastAPI(
    title=settings.APP_TITLE,
    description="Fashion Lookalike - 패션 유사 상품 검색 서비스",
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS 설정 (프론트엔드 연동용)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────────────────────────────
# 정적 파일 & 템플릿
# ──────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "..", "..", "frontend")

app.mount("/static", StaticFiles(directory=os.path.join(FRONTEND_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(FRONTEND_DIR, "templates"))

# ──────────────────────────────────────
# API 라우터 등록
# ──────────────────────────────────────
app.include_router(auth_router)
app.include_router(products_router)
app.include_router(posts_router)
app.include_router(search_router)
app.include_router(inquiries_router)
app.include_router(admin_router)
app.include_router(logs_router)
app.include_router(metrics_router)


# ──────────────────────────────────────
# 페이지 라우트 (Jinja2 템플릿)
# ──────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/search", response_class=HTMLResponse)
async def search_results(request: Request, q: str = ""):
    return templates.TemplateResponse("search_results.html", {"request": request, "query": q})


@app.get("/product/{product_id}", response_class=HTMLResponse)
async def product_detail(request: Request, product_id: str):
    """상품 상세 페이지 (DB 연동)"""
    from .database import get_pg_cursor
    
    try:
        with get_pg_cursor() as cur:
            # 1. 상품 기본 정보 + 설명
            cur.execute(
                """
                SELECT 
                    p.product_id,
                    p.model_code,
                    p.brand_name,
                    p.prod_name,
                    p.base_price,
                    p.category_code,
                    p.img_hdfs_path,
                    pf.detected_desc
                FROM products p
                LEFT JOIN product_features pf ON p.product_id = pf.product_id
                WHERE p.product_id = %s
                """,
                (product_id,),
            )
            product = cur.fetchone()

            if not product:
                return templates.TemplateResponse(
                    "error.html",
                    {"request": request, "error": "상품을 찾을 수 없습니다"},
                    status_code=404,
                )

            # 2. 최저가 5개 쇼핑몰
            cur.execute(
                """
                SELECT 
                    mall_name,
                    price,
                    mall_url,
                    rank
                FROM naver_prices
                WHERE product_id = %s
                ORDER BY rank ASC
                LIMIT 5
                """,
                (product_id,),
            )
            prices = cur.fetchall()

        # 3. 할인율 계산
        base_price = product["base_price"]
        for price_info in prices:
            discount = base_price - price_info["price"]
            discount_rate = int((discount / base_price) * 100) if base_price > 0 else 0
            price_info["discount"] = discount
            price_info["discount_rate"] = discount_rate

        return templates.TemplateResponse(
            "product_detail.html",
            {
                "request": request,
                "product": product,
                "prices": prices,
            },
        )
    except Exception as e:
        logger.error(f"상품 상세 조회 실패: {e}", exc_info=True)
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "error": "서버 오류가 발생했습니다"},
            status_code=500,
        )



@app.get("/mypage", response_class=HTMLResponse)
async def mypage(request: Request):
    from .routers.auth import _get_session
    
    session = _get_session(request)
    if not session:
        return RedirectResponse(url="/?error=login_required", status_code=302)
    
    # 템플릿에서 사용하기 위해 state에 user 정보 저장
    request.state.user = session
        
    return templates.TemplateResponse("mypage.html", {"request": request})


# Admin Routes
@app.get("/admin", response_class=HTMLResponse)
async def admin_root(request: Request):
    """어드민 진입점: 기본 페이지를 인프라 모니터링으로 변경 (2026-02-19)
    - 구 실시간 모니터링(admin_dashboard)은 /admin/stats 로 이동
    """
    return RedirectResponse(url="/admin/infra", status_code=302)


@app.get("/admin/stats", response_class=HTMLResponse)
async def admin_stats(request: Request):
    """통계 모니터링 (구 실시간 모니터링 대시보드 — 향후 개발 예정)"""
    return templates.TemplateResponse("admin_dashboard.html", {"request": request})


@app.get("/admin/infra_old", response_class=HTMLResponse)
async def admin_infra_old(request: Request):
    return templates.TemplateResponse("admin_infra_old.html", {"request": request})



@app.get("/admin/batch", response_class=HTMLResponse)
async def admin_batch(request: Request):
    return templates.TemplateResponse("admin_batch.html", {"request": request})


@app.get("/admin/inquiry", response_class=HTMLResponse)
async def admin_inquiry(request: Request):
    return templates.TemplateResponse("admin_inquiry.html", {"request": request})


# 기존 로그 모니터링 (구버전 → _old 처리)
# @app.get("/admin/logs_old", response_class=HTMLResponse)
# async def admin_logs_old(request: Request):
#     return templates.TemplateResponse("admin_logs_old.html", {"request": request})

@app.get("/admin/logs", response_class=HTMLResponse)
async def admin_logs(request: Request):
    return templates.TemplateResponse("admin_logs.html", {"request": request})


@app.get("/admin/infra", response_class=HTMLResponse)
async def admin_infra(request: Request):
    return templates.TemplateResponse("admin_infra.html", {"request": request})


@app.get("/inquiry", response_class=HTMLResponse)
async def inquiry_page(request: Request):
    return templates.TemplateResponse("inquiry.html", {"request": request})


@app.get("/recent", response_class=HTMLResponse)
async def recent_viewed(request: Request):
    from .routers.auth import _get_session
    
    session = _get_session(request)
    if not session:
        return RedirectResponse(url="/?error=login_required", status_code=302)
    
    return templates.TemplateResponse("recent.html", {"request": request})


@app.get("/likes", response_class=HTMLResponse)
async def likes(request: Request):
    from .routers.auth import _get_session
    
    session = _get_session(request)
    if not session:
        return RedirectResponse(url="/?error=login_required", status_code=302)
    
    return templates.TemplateResponse("likes.html", {"request": request})


@app.get("/search-history", response_class=HTMLResponse)
async def search_history(request: Request):
    return templates.TemplateResponse("search_history.html", {"request": request})


# ──────────────────────────────────────
# 헬스체크 & 상태
# ──────────────────────────────────────
@app.get("/health")
async def health_check():
    """헬스 체크"""
    return {
        "status": "healthy",
        "environment": settings.APP_ENV,
        "version": settings.APP_VERSION,
    }


@app.get("/api/status")
async def api_status():
    """API 상태 및 DB 연결 상태 확인"""
    from .database import _pg_pool, _mongo_client, _redis_client

    db_status = {
        "postgresql": "connected" if _pg_pool else "disconnected",
        "mongodb": "connected" if _mongo_client else "disconnected",
        "redis": "connected" if _redis_client else "disconnected",
    }

    return {
        "status": "running",
        "environment": settings.APP_ENV,
        "databases": db_status,
    }


# ──────────────────────────────────────
# 직접 실행
# ──────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        "web.backend.app.main:app",
        host="0.0.0.0",
        port=8900,
        reload=True,
    )
