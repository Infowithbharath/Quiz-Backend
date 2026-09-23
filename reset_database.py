import sys
import os

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.database import DB_PATH, init_db
from backend.app.import_questions import import_questions_to_db

def reset_database_interactive():
    print("==========================================================")
    print("  WARNING: RESETTING CTF PLATFORM DATABASE                ")
    print("==========================================================")
    print("This action will permanently delete ALL contestant teams,  ")
    print("answers, sessions, and scoring results!                   ")
    print(f"Target Database: {DB_PATH}")
    print("==========================================================")

    if "--force" not in sys.argv:
        confirmation = input("Type 'RESET_DATABASE' to confirm deletion: ").strip()
        if confirmation != "RESET_DATABASE":
            print("[ABORTED] Database reset cancelled by user.")
            sys.exit(0)

    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
            print("[OK] Existing database file deleted.")
        except Exception as e:
            print(f"[ERROR] Failed to delete database: {e}")
            sys.exit(1)

    init_db()
    count = import_questions_to_db(force=True)
    print(f"[SUCCESS] Fresh database initialized with {count} authoritative questions.")

if __name__ == "__main__":
    reset_database_interactive()
