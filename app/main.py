import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from .database import init_db
from .import_questions import import_questions_to_db
from .routers import contestant_router, admin_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context to safely initialize the SQLite database and verify 50 questions."""
    try:
        init_db()
        imported_count = import_questions_to_db(force=False)
        print(f"[CTF Platform] Database initialized. Verified {imported_count} authoritative questions in SQLite.")
    except Exception as e:
        print(f"[CTF Platform] Startup initialization note: {e}")
    yield
    print("[CTF Platform] Shutting down backend.")

app = FastAPI(
    title="Cybersecurity CTF Quiz Platform API",
    version="1.0.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
    redoc_url=None,
    lifespan=lifespan
)

@app.get("/api/docs", include_in_schema=False)
def api_docs():
    from fastapi.openapi.docs import get_swagger_ui_html
    return get_swagger_ui_html(openapi_url="/openapi.json", title="Cybersecurity CTF API Docs")

# Explicit CORS Origins for Contestant (5173) and Administrator (5174)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174"
    ],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Enforces standard security headers across all API endpoints."""
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Global exception handler to sanitize internal errors.
    Prevents leakage of tracebacks, database paths, SQL query details, or secrets.
    """
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    # Log exception internally, but return generic error to client
    import logging
    logging.exception("Unhandled server exception occurred: %s", str(exc))
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )

# Register API Routers
app.include_router(contestant_router)
app.include_router(admin_router)

@app.get("/")
@app.get("/api")
def root_status():
    """System status endpoint."""
    return {"status": "ok", "platform": "Cybersecurity CTF Quiz Platform", "docs": "/api/docs"}

@app.get("/api/health")
def health_check():
    """System health check endpoint."""
    return {"status": "ok", "platform": "Cybersecurity CTF Quiz Platform", "version": "1.0.0"}
