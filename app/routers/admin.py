import secrets
from datetime import datetime, timezone
from typing import Dict, Any, List
from fastapi import APIRouter, HTTPException, Depends, Request, Response
from ..database import get_db
from ..security import admin_rate_limiter
from ..auth import verify_password, get_current_admin
from ..admin import (
    get_admin_statistics,
    get_admin_ranking,
    get_admin_teams_overview,
    get_admin_results_table,
    terminate_team_session,
    authorize_team_relogin
)
from ..schemas import (
    AdminLoginRequest,
    AdminLoginResponse,
    AdminTerminateRequest,
    AdminAuthorizeRequest
)

router = APIRouter(prefix="/api/admin", tags=["Administrator"])

@router.post("/login", response_model=AdminLoginResponse)
def admin_login(req: AdminLoginRequest, request: Request, response: Response):
    """Authenticates administrator credentials and issues an admin session token."""
    client_ip = request.client.host if request.client else "unknown"
    admin_rate_limiter.check(f"admin_login_{client_ip}")

    now_iso = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, password_hash FROM admins WHERE username = ?", (req.username.strip(),))
        admin_row = cursor.fetchone()

        if not admin_row or not verify_password(req.password, admin_row["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid administrator username or password.")

        admin_id = admin_row["id"]
        username = admin_row["username"]

        session_token = secrets.token_hex(32)
        cursor.execute("""
            INSERT INTO admin_sessions (admin_id, session_token, login_time, last_activity, active)
            VALUES (?, ?, ?, ?, 1)
        """, (admin_id, session_token, now_iso, now_iso))

    response.set_cookie(
        key="ctf_admin_session",
        value=session_token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=86400,
        path="/"
    )

    return AdminLoginResponse(
        success=True,
        username=username,
        session_token=session_token
    )

@router.post("/logout")
def admin_logout(response: Response, admin: Dict[str, Any] = Depends(get_current_admin)):
    """Deactivates current administrator session."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE admin_sessions SET active = 0 WHERE session_token = ?", (admin.get("session_token"),))
    response.delete_cookie(key="ctf_admin_session", path="/")
    return {"success": True, "message": "Admin session terminated."}

@router.get("/session")
def admin_check_session(admin: Dict[str, Any] = Depends(get_current_admin)):
    """Validates active administrator session."""
    return {"authenticated": True, "username": admin["username"]}

@router.get("/teams")
def admin_get_teams(admin: Dict[str, Any] = Depends(get_current_admin)) -> List[Dict[str, Any]]:
    """Returns full team roster with session and quiz progress."""
    return get_admin_teams_overview()

@router.get("/results")
def admin_get_results(admin: Dict[str, Any] = Depends(get_current_admin)) -> List[Dict[str, Any]]:
    """Returns complete results breakdown for all submitted teams."""
    return get_admin_results_table()

@router.get("/statistics")
def admin_get_statistics(admin: Dict[str, Any] = Depends(get_current_admin)) -> Dict[str, Any]:
    """Returns aggregate competition metrics. Never accessible by contestants."""
    return get_admin_statistics()

@router.get("/ranking")
def admin_get_ranking(admin: Dict[str, Any] = Depends(get_current_admin)) -> List[Dict[str, Any]]:
    """Returns Top 5 ranked teams with tiebreakers. Never accessible by contestants."""
    return get_admin_ranking(limit=5)

@router.post("/terminate-session")
def admin_terminate_session(req: AdminTerminateRequest, admin: Dict[str, Any] = Depends(get_current_admin)):
    """Administrator action: Terminates an active contestant team session."""
    success = terminate_team_session(req.team_id)
    return {"success": success, "message": f"Active session terminated for team ID {req.team_id}."}

@router.post("/authorize-login")
def admin_authorize_login(req: AdminAuthorizeRequest, admin: Dict[str, Any] = Depends(get_current_admin)):
    """Administrator action: Re-authorizes team login or resets attempt."""
    success = authorize_team_relogin(req.team_id, reset_quiz=req.reset_quiz)
    action_type = "Quiz attempt reset and login authorized" if req.reset_quiz else "Login re-authorized"
    return {"success": success, "message": f"{action_type} for team ID {req.team_id}."}
