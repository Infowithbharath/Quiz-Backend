import os
import sys

# Ensure backend directory and its parent are in sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
parent_dir = os.path.dirname(backend_dir)

for path in [backend_dir, parent_dir]:
    if path and path not in sys.path:
        sys.path.insert(0, path)

# In Vercel serverless environment, use /tmp for SQLite
if os.environ.get("VERCEL") or "VERCEL" in os.environ:
    os.environ.setdefault("CTF_DB_PATH", "/tmp/ctf_quiz.db")

try:
    from backend.app.main import app
    from backend.app.database import init_db
    from backend.app.import_questions import import_questions_to_db
except ImportError:
    from app.main import app
    from app.database import init_db
    from app.import_questions import import_questions_to_db

# Serverless environments do not consistently invoke FastAPI lifespan handlers.
# Pre-initialize SQLite tables and verify questions on function import.
try:
    init_db()
    import_questions_to_db(force=False)
except Exception as e:
    print(f"[Vercel Init Warning]: {e}")
