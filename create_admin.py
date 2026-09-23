import sys
import os
import getpass
from datetime import datetime, timezone

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.database import get_db, init_db
from backend.app.auth import hash_password

FORBIDDEN_PASSWORDS = {"admin", "admin123", "password", "123456", "admin@123", "root"}

def create_admin_interactive(username: str = None, password: str = None):
    init_db()
    print("==================================================")
    print("  CYBERSECURITY CTF - ADMINISTRATOR INITIALIZATION")
    print("==================================================")

    if not username:
        username = input("Enter Administrator Username: ").strip()
    if not username:
        print("[ERROR] Username cannot be empty.")
        sys.exit(1)

    if not password:
        password = getpass.getpass("Enter Administrator Password: ").strip()
        confirm = getpass.getpass("Confirm Administrator Password: ").strip()
        if password != confirm:
            print("[ERROR] Passwords do not match.")
            sys.exit(1)

    if len(password) < 8:
        print("[ERROR] Administrator password must be at least 8 characters long.")
        sys.exit(1)

    if password.lower() in FORBIDDEN_PASSWORDS:
        print("[ERROR] Insecure or predictable password. Please choose a strong unique password.")
        sys.exit(1)

    now_iso = datetime.now(timezone.utc).isoformat()
    pwd_hash = hash_password(password)

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO admins (username, password_hash, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET password_hash = excluded.password_hash;
        """, (username, pwd_hash, now_iso))

    print(f"[SUCCESS] Administrator '{username}' created/updated successfully.")

if __name__ == "__main__":
    u = sys.argv[1] if len(sys.argv) > 1 else None
    p = sys.argv[2] if len(sys.argv) > 2 else None
    create_admin_interactive(u, p)
