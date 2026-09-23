import os
import hmac
import hashlib
import json
import base64
import secrets
import string
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from fastapi import HTTPException, Request, Depends
import argon2
import bcrypt
from .database import get_db

ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "ctf-super-secret-admin-key-2026-production")

def create_admin_token(admin_id: int, username: str) -> str:
    """Generates a cryptographically signed HMAC token for administrators."""
    payload = {
        "admin_id": admin_id,
        "username": username,
        "exp": int(datetime.now(timezone.utc).timestamp()) + 86400,
        "nonce": secrets.token_hex(8)
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.new(ADMIN_SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"

def verify_admin_token(token: str) -> Optional[Dict[str, Any]]:
    """Verifies HMAC signature and expiration of an admin token."""
    if not token or "." not in token:
        return None
    try:
        raw, sig = token.rsplit(".", 1)
        expected_sig = hmac.new(ADMIN_SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
        if payload.get("exp", 0) < datetime.now(timezone.utc).timestamp():
            return None
        return payload
    except Exception:
        return None

# Initialize Argon2id hasher
try:
    _argon2_hasher = argon2.PasswordHasher(
        time_cost=2,
        memory_cost=65536,
        parallelism=2,
        hash_len=32,
        type=argon2.Type.ID
    )
except Exception:
    _argon2_hasher = argon2.PasswordHasher()

def hash_password(password: str) -> str:
    """Hashes password using Argon2id with automatic bcrypt fallback."""
    try:
        return _argon2_hasher.hash(password)
    except Exception:
        salt = bcrypt.gensalt(rounds=12)
        return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

def verify_password(password: str, hashed: str) -> bool:
    """Verifies a password against an Argon2id or bcrypt hash."""
    if not password or not hashed:
        return False
    if hashed.startswith("$argon2"):
        try:
            return _argon2_hasher.verify(hashed, password)
        except Exception:
            return False
    elif hashed.startswith("$2b$") or hashed.startswith("$2a$"):
        try:
            return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
        except Exception:
            return False
    return False

def generate_secure_password(length: int = 16) -> str:
    """
    Generates a cryptographically secure random password.
    Contains uppercase, lowercase, digits, and allowed symbols (!, ?, @, _).
    Guarantees at least one character from each required character group.
    """
    alphabet = string.ascii_letters + string.digits + "!?@_"
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        has_upper = any(c.isupper() for c in pwd)
        has_lower = any(c.islower() for c in pwd)
        has_digit = any(c.isdigit() for c in pwd)
        has_symbol = any(c in "!?@_" for c in pwd)
        if has_upper and has_lower and has_digit and has_symbol:
            return pwd

def extract_token_from_request(request: Request, cookie_name: str = "ctf_session") -> Optional[str]:
    """Extracts session token from Authorization: Bearer <token> or HttpOnly cookie."""
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if token:
            return token
    return request.cookies.get(cookie_name)

def get_current_team(request: Request) -> Dict[str, Any]:
    """
    Dependency that extracts, verifies, and returns the authenticated team dictionary.
    Enforces active session ownership and session validity.
    """
    token = extract_token_from_request(request, cookie_name="ctf_session")
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required. No session token provided.")

    now_iso = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.id AS session_id, s.team_id, s.active AS session_active,
                   t.id, t.team_name, t.member1_name, t.member2_name,
                   t.registered_at, t.quiz_started_at, t.quiz_submitted_at,
                   t.submission_reason, t.score, t.active_session_id, t.active_tab_id, t.allow_relogin
            FROM sessions s
            JOIN teams t ON s.team_id = t.id
            WHERE s.session_token = ? AND s.active = 1
        """, (token,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=401, detail="Invalid or expired session. Please log in again.")

        team = dict(row)

        # Ensure that the session token matches team's active_session_id
        if team["active_session_id"] != token:
            # Terminate stale session
            cursor.execute("UPDATE sessions SET active = 0 WHERE session_token = ?", (token,))
            raise HTTPException(status_code=401, detail="Session expired or replaced by another login.")

        # Update last activity timestamp
        cursor.execute("UPDATE sessions SET last_activity = ? WHERE session_token = ?", (now_iso, token))

        return team

def get_current_admin(request: Request) -> Dict[str, Any]:
    """
    Dependency that extracts, verifies, and returns the authenticated admin dictionary.
    Rejects any non-admin request. Works across serverless instances via stateless HMAC tokens.
    """
    token = extract_token_from_request(request, cookie_name="ctf_admin_session")
    if not token:
        raise HTTPException(status_code=401, detail="Administrator authentication required.")

    # 1. Stateless verification (works across all serverless lambda containers)
    admin_payload = verify_admin_token(token)
    if admin_payload:
        return {
            "session_id": 1,
            "admin_id": admin_payload["admin_id"],
            "id": admin_payload["admin_id"],
            "username": admin_payload["username"],
            "session_token": token
        }

    # 2. Database verification fallback for legacy or local session tokens
    now_iso = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.id AS session_id, s.admin_id, a.id, a.username
            FROM admin_sessions s
            JOIN admins a ON s.admin_id = a.id
            WHERE s.session_token = ? AND s.active = 1
        """, (token,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=401, detail="Invalid or expired administrator session.")

        admin = dict(row)
        cursor.execute("UPDATE admin_sessions SET last_activity = ? WHERE session_token = ?", (now_iso, token))
        return admin
