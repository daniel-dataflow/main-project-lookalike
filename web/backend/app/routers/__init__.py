from .auth import router as auth_router
from .product import router as product_router
from .search import router as search_router
from .inquiry import router as inquiry_router
from .admin import router as admin_router
from .log import router as log_router
from .metric import router as metric_router
from .metric_realtime import router as metric_realtime_router

__all__ = [
    "auth_router", "product_router", "search_router", "inquiry_router", 
    "admin_router", "log_router", "metric_router", "metric_realtime_router"
]
