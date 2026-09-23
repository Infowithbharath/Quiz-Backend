import os
import sys
import json

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

class VercelHandler:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
            # Diagnostic debug route
            req_path = scope.get("path", "")
            matched = headers.get("x-matched-path") or headers.get("x-forwarded-uri")

            if "/debug" in req_path or (matched and "/debug" in matched):
                payload = json.dumps({
                    "scope_path": req_path,
                    "matched": matched,
                    "headers": {k: v for k, v in headers.items() if "cookie" not in k and "auth" not in k},
                    "routes": [getattr(r, "path", str(r)) for r in main_app.routes]
                }, indent=2).encode("utf-8")
                await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": payload})
                return

            # Map the actual path
            if matched:
                clean_path = matched.split("?")[0]
                scope["path"] = clean_path
                scope["raw_path"] = clean_path.encode("latin1")

        await self.inner(scope, receive, send)

app = VercelHandler(main_app)
