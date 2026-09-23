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
    from backend.app.main import app
except ImportError:
    from app.main import app
