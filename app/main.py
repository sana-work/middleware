from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.lifespan import lifespan
from app.core.exceptions import (
    ExecutionNotFoundError,
    SessionNotFoundError,
    TokenFetchError,
    BackendExecutorError,
)
from app.core.limiter import limiter
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from slowapi.middleware import SlowAPIMiddleware
from prometheus_client import make_asgi_app

from app.api.chat_routes import router as chat_router
from app.api.websocket_routes import router as ws_router
from app.api.health_routes import router as health_router

app = FastAPI(
    title=settings.APP_NAME,
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# --- Metrics ---
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Exception handlers ---

@app.exception_handler(ExecutionNotFoundError)
async def execution_not_found_handler(request: Request, exc: ExecutionNotFoundError):
    return JSONResponse(status_code=404, content={"success": False, "message": str(exc)})


@app.exception_handler(SessionNotFoundError)
async def session_not_found_handler(request: Request, exc: SessionNotFoundError):
    return JSONResponse(status_code=404, content={"success": False, "message": str(exc)})


@app.exception_handler(TokenFetchError)
async def token_fetch_error_handler(request: Request, exc: TokenFetchError):
    return JSONResponse(status_code=502, content={"success": False, "message": "Token service unavailable"})


@app.exception_handler(BackendExecutorError)
async def backend_executor_error_handler(request: Request, exc: BackendExecutorError):
    return JSONResponse(status_code=502, content={"success": False, "message": "Backend executor unavailable"})


# --- Routers ---
app.include_router(chat_router)
app.include_router(ws_router)
app.include_router(health_router)
