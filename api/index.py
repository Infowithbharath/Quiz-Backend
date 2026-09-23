import os
import sys
from urllib.parse import parse_qs, urlencode

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

class VercelRouteFixer:
    """
    Restores the real request path forwarded by Vercel via __p__ rewrite parameter.
    """
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            qs = scope.get("query_string", b"").decode("latin1")
            if "__p__" in qs:
                params = parse_qs(qs, keep_blank_values=True)
                p_vals = params.pop("__p__", [])
                if p_vals:
                    real_path = p_vals[0]
                    if not real_path.startswith("/"):
                        real_path = "/" + real_path
                    while "//" in real_path:
                        real_path = real_path.replace("//", "/")
                    scope["path"] = real_path
                    scope["raw_path"] = real_path.encode("latin1")
                    scope["query_string"] = urlencode(params, doseq=True).encode("latin1")

        await self.inner(scope, receive, send)

app = VercelRouteFixer(main_app)
