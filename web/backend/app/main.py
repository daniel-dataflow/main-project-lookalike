"""
Lookalike - FastAPI 메인 애플리케이션
패션 유사 상품 검색 웹 서비스
"""
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn
import os
import logging

from .config import get_settings
from .database import init_all_databases, close_all_databases
from .routers import auth_router, products_router, posts_router, search_router, inquiries_router

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
    yield
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
    return templates.TemplateResponse("product_detail.html", {"request": request, "product_id": product_id})


@app.get("/mypage", response_class=HTMLResponse)
async def mypage(request: Request):
    return templates.TemplateResponse("mypage.html", {"request": request})


# Admin Routes
@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    return templates.TemplateResponse("admin_dashboard.html", {"request": request})


@app.get("/admin/infra", response_class=HTMLResponse)
async def admin_infra(request: Request):
    return templates.TemplateResponse("admin_infra.html", {"request": request})


@app.get("/admin/batch", response_class=HTMLResponse)
async def admin_batch(request: Request):
    return templates.TemplateResponse("admin_batch.html", {"request": request})


@app.get("/admin/inquiry", response_class=HTMLResponse)
async def admin_inquiry(request: Request):
    return templates.TemplateResponse("admin_inquiry.html", {"request": request})


@app.get("/inquiry", response_class=HTMLResponse)
async def inquiry_page(request: Request):
    return templates.TemplateResponse("inquiry.html", {"request": request})


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
