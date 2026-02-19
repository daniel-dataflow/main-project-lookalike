from .auth import router as auth_router
from .products import router as products_router
from .inquiry_board import router as posts_router
from .search import router as search_router
from .inquiries import router as inquiries_router
from .admin import router as admin_router
from .logs import router as logs_router
from .metrics import router as metrics_router

__all__ = ["auth_router", "products_router", "posts_router", "search_router", "inquiries_router", "admin_router", "logs_router", "metrics_router"]
