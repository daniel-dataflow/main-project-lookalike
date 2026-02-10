from .auth import router as auth_router
from .products import router as products_router
from .posts import router as posts_router
from .search import router as search_router

__all__ = ["auth_router", "products_router", "posts_router", "search_router"]
