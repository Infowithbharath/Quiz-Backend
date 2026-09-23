import os
import sys
import traceback

# Setup paths
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
parent_dir = os.path.dirname(backend_dir)

for path in [backend_dir, parent_dir, current_dir]:
    if path and path not in sys.path:
        sys.path.insert(0, path)

# On Vercel serverless, force SQLite into /tmp
os.environ["CTF_DB_PATH"] = "/tmp/ctf_quiz.db"

app = None
init_error = None

try:
    try:
        from backend.app.main import app as main_app
        from backend.app.database import init_db
        from backend.app.import_questions import import_questions_to_db
    except ImportError:
        from app.main import app as main_app
        from app.database import init_db
        from app.import_questions import import_questions_to_db

    # Pre-initialize SQLite in /tmp
    try:
        init_db()
        import_questions_to_db(force=False)
    except Exception as e:
        print(f"[Vercel Init DB Warning]: {e}")

    # ASGI middleware to unwrap Vercel rewritten paths
    class VercelPathMiddleware:
        def __init__(self, inner_app):
            self.inner_app = inner_app

        async def __call__(self, scope, receive, send):
            if scope.get("type") == "http":
                headers = dict(scope.get("headers", []))
                # Vercel sends the true original requested path in these headers
                matched = (
                    headers.get(b"x-matched-path") or 
                    headers.get(b"x-forwarded-uri") or 
                    headers.get(b"x-invoke-path")
                )
                if matched:
                    decoded = matched.decode("utf-8").split("?")[0]
                    # If Vercel rewrote the path to /api/index, restore the real client path
                    if scope.get("path") in ("/api/index", "/api/index.py", "/api"):
                        scope["path"] = decoded
                        scope["raw_path"] = decoded.encode("utf-8")
            await self.inner_app(scope, receive, send)

    app = VercelPathMiddleware(main_app)

except Exception:
    init_error = traceback.format_exc()
    print(f"[VERCEL STARTUP FATAL ERROR]:\n{init_error}", file=sys.stderr)

if init_error:
    async def app(scope, receive, send):
        if scope.get("type") == "http":
            body = f"=== VERCEL FASTAPI STARTUP ERROR ===\n\n{init_error}".encode("utf-8")
            await send({
                "type": "http.response.start",
                "status": 500,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("utf-8")),
                ],
            })
            await send({
                "type": "http.response.body",
                "body": body,
            })
