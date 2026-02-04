from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
import uvicorn
import os

app = FastAPI(
    title="Lookalike",
    description="Fashion search web application",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Get current directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Frontend directory (sibling of backend)
FRONTEND_DIR = os.path.join(BASE_DIR, "..", "..", "frontend")

# Mount static files
app.mount("/static", StaticFiles(directory=os.path.join(FRONTEND_DIR, "static")), name="static")

# Templates
templates = Jinja2Templates(directory=os.path.join(FRONTEND_DIR, "templates"))

# Routes
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "environment": os.getenv("APP_ENV", "development")
    }

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

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
