import os
import sys

# Setup paths
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
parent_dir = os.path.dirname(backend_dir)

for path in [backend_dir, parent_dir, current_dir]:
    if path and path not in sys.path:
        sys.path.insert(0, path)

os.environ["CTF_DB_PATH"] = "/tmp/ctf_quiz.db"

try:
    from backend.app.main import app as main_app
except ImportError:
    from app.main import app as main_app

class VercelPrefixMiddleware:
    """
    Vercel serverless mounts api/index.py at /api and strips /api from request paths.
    This middleware ensures paths match FastAPI's /api prefix routes.
    """
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path", "")
            # Normalize root and ensure /api prefix
            if path in ("", "/"):
                scope["path"] = "/api"
                scope["raw_path"] = b"/api"
            elif not path.startswith("/api"):
                new_path = "/api" + path
                scope["path"] = new_path
                scope["raw_path"] = new_path.encode("latin1")

        await self.inner(scope, receive, send)

app = VercelPrefixMiddleware(main_app)
