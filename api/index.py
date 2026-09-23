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
    init_db()
    import_questions_to_db(force=False)
    app = main_app

except Exception:
    init_error = traceback.format_exc()
    print(f"[VERCEL STARTUP FATAL ERROR]:\n{init_error}", file=sys.stderr)


# If an initialization error occurred, capture and display it cleanly instead of crashing
if init_error:
    async def app(scope, receive, send):
        if scope.get("type") == "http":
            body = (
                f"=== VERCEL FASTAPI STARTUP ERROR ===\n\n"
                f"{init_error}\n\n"
                f"Python sys.path: {sys.path}\n"
                f"Current Dir: {os.getcwd()}\n"
                f"Dir Contents: {os.listdir('.') if os.path.exists('.') else 'N/A'}\n"
            ).encode("utf-8")
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
