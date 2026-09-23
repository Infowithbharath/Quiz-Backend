import secrets
from datetime import datetime, timezone
from typing import Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Request, Response
from ..database import get_db
from ..security import (
    validate_team_name, sanitize_text, rate_limiter,
    registration_rate_limiter, login_rate_limiter,
    answer_rate_limiter, submission_rate_limiter,
    violation_rate_limiter
)
from ..auth import hash_password, verify_password, generate_secure_password, get_current_team
from ..quiz import (
    get_or_create_team_question_order,
    get_quiz_state_for_team,
    get_question_detail_for_contestant,
    record_answer_for_team,
    submit_quiz_for_team,
    validate_tab_session,
    record_violation_for_team
)
from ..schemas import (
    RegisterRequest,
    RegisterResponse,
    LoginRequest,
    LoginResponse,
    AnswerSubmitRequest,
    QuizAutoSubmitRequest,
    QuizSubmissionResponse,
    QuizViolationRequest,
    QuizViolationResponse
)

router = APIRouter(prefix="/api", tags=["Contestant"])

@router.post("/register", response_model=RegisterResponse)
def register_team(req: RegisterRequest, request: Request):
    """
    Registers a new team. Validates team name rules, checks case-insensitive uniqueness,
    generates a cryptographically secure random password, and initializes question randomization.
    """
    client_ip = request.client.host if request.client else "unknown"
    registration_rate_limiter.check(f"register_{client_ip}")

    # 1. Validate team name
    is_valid, err_msg = validate_team_name(req.team_name)
    if not is_valid:
        raise HTTPException(status_code=400, detail=err_msg)

    member1 = sanitize_text(req.member1_name, 50)
    if not member1:
        raise HTTPException(status_code=400, detail="Member 1 name is required.")

    member2 = sanitize_text(req.member2_name, 50) if req.member2_name else None
    team_name_lower = req.team_name.lower()
    now_iso = datetime.now(timezone.utc).isoformat()

    # 2. Check team name uniqueness (case-insensitive)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM teams WHERE team_name_lower = ?", (team_name_lower,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Team name is already registered. Please choose another name.")

        # 3. Generate secure random password and hash it
        generated_password = generate_secure_password(length=16)
        pwd_hash = hash_password(generated_password)

        # 4. Insert team into database
        cursor.execute("""
            INSERT INTO teams (
                team_name, team_name_lower, password_hash,
                member1_name, member2_name, registered_at,
                login_used, allow_relogin
            ) VALUES (?, ?, ?, ?, ?, ?, 0, 0);
        """, (req.team_name, team_name_lower, pwd_hash, member1, member2, now_iso))

        new_team_id = cursor.lastrowid

    # 5. Initialize server-authoritative randomized question order for this team
    get_or_create_team_question_order(new_team_id)

    return RegisterResponse(
        success=True,
        team_name=req.team_name,
        generated_password=generated_password,
        message="Team registered successfully! Please store your generated password securely. It will not be shown again."
    )

@router.post("/login", response_model=LoginResponse)
def login_team(req: LoginRequest, request: Request, response: Response):
    """
    Authenticates contestant team. Enforces single active session, post-submission lock,
    and returns session token alongside setting HttpOnly cookie.
    """
    client_ip = request.client.host if request.client else "unknown"
    login_rate_limiter.check(f"login_{client_ip}")

    team_name_lower = req.team_name.lower().strip()
    now_iso = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, team_name, password_hash, active_session_id,
                   quiz_submitted_at, allow_relogin
            FROM teams
            WHERE team_name_lower = ?
        """, (team_name_lower,))
        team_row = cursor.fetchone()

        if not team_row or not verify_password(req.password, team_row["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid team name or password.")

        team_id = team_row["id"]
        team_name = team_row["team_name"]

        # Section 33: Post-submission lock
        if team_row["quiz_submitted_at"] and not team_row["allow_relogin"]:
            raise HTTPException(
                status_code=403,
                detail="This team has already completed the quiz. Administrator authorization is required."
            )

        # Section 32: Single active session enforcement
        active_token = team_row["active_session_id"]
        if active_token:
            cursor.execute("SELECT id FROM sessions WHERE session_token = ? AND active = 1", (active_token,))
            if cursor.fetchone():
                raise HTTPException(
                    status_code=409,
                    detail="This team is already logged in."
                )

        # Generate unique session token
        session_token = secrets.token_hex(32)

        # Update team's active session and login count atomically
        cursor.execute("""
            UPDATE teams SET
                active_session_id = ?,
                login_used = login_used + 1,
                allow_relogin = 0
            WHERE id = ?
        """, (session_token, team_id))

        # Insert session record
        cursor.execute("""
            INSERT INTO sessions (team_id, session_token, login_time, last_activity, active)
            VALUES (?, ?, ?, ?, 1)
        """, (team_id, session_token, now_iso, now_iso))

    # Set HttpOnly, SameSite cookie
    response.set_cookie(
        key="ctf_session",
        value=session_token,
        httponly=True,
        samesite="lax",
        secure=False,  # Local development on HTTP
        max_age=86400,
        path="/"
    )

    return LoginResponse(
        success=True,
        team_name=team_name,
        session_token=session_token,
        message="Login successful."
    )

@router.post("/logout")
def logout_team(response: Response, team: Dict[str, Any] = Depends(get_current_team)):
    """Deactivates active contestant session and clears session cookie."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE teams SET active_session_id = NULL, active_tab_id = NULL WHERE id = ?", (team["id"],))
        cursor.execute("UPDATE sessions SET active = 0, logout_time = ? WHERE session_token = ?", (now_iso, team["active_session_id"]))

    response.delete_cookie(key="ctf_session", path="/")
    return {"success": True, "message": "Logged out successfully."}

@router.get("/session")
def check_session(team: Dict[str, Any] = Depends(get_current_team)):
    """Validates session and returns contestant team profile and quiz status."""
    return {
        "authenticated": True,
        "team_name": team["team_name"],
        "member1_name": team["member1_name"],
        "member2_name": team["member2_name"],
        "registered_at": team["registered_at"],
        "quiz_started": bool(team["quiz_started_at"]),
        "quiz_submitted": bool(team["quiz_submitted_at"]),
        "submission_reason": team["submission_reason"]
    }

@router.get("/quiz/heartbeat")
def quiz_heartbeat(request: Request, team: Dict[str, Any] = Depends(get_current_team)):
    """
    Lightweight session & tab validation heartbeat endpoint.
    Verifies that session is active and single tab requirement is preserved.
    """
    tab_id = request.headers.get("X-Tab-ID")
    validate_tab_session(team["id"], tab_id)
    return {
        "valid": True,
        "quiz_submitted": bool(team["quiz_submitted_at"]),
        "submission_reason": team["submission_reason"]
    }

@router.get("/quiz")
def get_quiz_info(request: Request, team: Dict[str, Any] = Depends(get_current_team)):
    """
    Returns full quiz metadata and 50-question navigation summary for contestant.
    Initializes 30-minute timer if starting for the first time.
    CRITICAL: Never returns correct answers or scores.
    Enforces server-authoritative single-tab requirement.
    """
    tab_id = request.headers.get("X-Tab-ID")
    validate_tab_session(team["id"], tab_id)
    return get_quiz_state_for_team(team)

@router.get("/quiz/questions/{question_number}")
def get_question(question_number: int, request: Request, team: Dict[str, Any] = Depends(get_current_team)):
    """
    Returns text and 4 choices for a single question.
    CRITICAL: Never returns correct answer.
    Enforces server-authoritative single-tab requirement.
    """
    tab_id = request.headers.get("X-Tab-ID")
    validate_tab_session(team["id"], tab_id)
    return get_question_detail_for_contestant(team["id"], question_number)

@router.post("/quiz/answer")
def submit_answer(req: AnswerSubmitRequest, request: Request, team: Dict[str, Any] = Depends(get_current_team)):
    """Persists contestant answer choice. Validates quiz is currently active and unexpired."""
    client_ip = request.client.host if request.client else "unknown"
    answer_rate_limiter.check(f"answer_{team['id']}_{client_ip}")

    tab_id = request.headers.get("X-Tab-ID")
    validate_tab_session(team["id"], tab_id)
    return record_answer_for_team(team["id"], req.question_number, req.selected_answer)

@router.post("/quiz/submit", response_model=QuizSubmissionResponse)
def submit_quiz_manually(request: Request, response: Response, team: Dict[str, Any] = Depends(get_current_team)):
    """
    Contestant manual submission. Calculates score server-side, locks attempt.
    CRITICAL PRIVACY ENFORCEMENT: Returns ONLY confirmation. Score is strictly hidden.
    """
    client_ip = request.client.host if request.client else "unknown"
    submission_rate_limiter.check(f"submit_{team['id']}_{client_ip}")

    tab_id = request.headers.get("X-Tab-ID")
    validate_tab_session(team["id"], tab_id)

    result = submit_quiz_for_team(team["id"], reason="manual")
    response.delete_cookie(key="ctf_session", path="/")
    return QuizSubmissionResponse(
        success=True,
        message="Your answers have been submitted successfully.",
        submission_reason="manual",
        submitted_at=result["quiz_submitted_at"]
    )

@router.post("/quiz/auto-submit", response_model=QuizSubmissionResponse)
def submit_quiz_automatically(
    req: QuizAutoSubmitRequest,
    request: Request,
    response: Response,
    team: Dict[str, Any] = Depends(get_current_team)
):
    """
    Anti-cheat & timeout automatic submission. Triggered on visibility loss, blur, or timeout.
    CRITICAL PRIVACY ENFORCEMENT: Returns ONLY confirmation. Score is strictly hidden.
    """
    client_ip = request.client.host if request.client else "unknown"
    submission_rate_limiter.check(f"auto_submit_{team['id']}_{client_ip}")

    # Note: Auto-submit from active tab passes X-Tab-ID validation
    tab_id = request.headers.get("X-Tab-ID")
    if tab_id:
        try:
            validate_tab_session(team["id"], tab_id)
        except HTTPException:
            pass # If tab validation fails during auto-submit, proceed to lock attempt

    result = submit_quiz_for_team(team["id"], reason=req.reason)
    response.delete_cookie(key="ctf_session", path="/")

    if req.reason in ("visibility_change", "window_blur"):
        user_msg = "Quiz automatically submitted because the quiz window was left."
    elif req.reason == "timeout":
        user_msg = "Time expired. Your quiz has been automatically submitted."
    elif req.reason == "multiple_tabs":
        user_msg = "Multiple tabs detected. Your quiz has been automatically submitted."
    else:
        user_msg = "Your quiz has been submitted."

    return QuizSubmissionResponse(
        success=True,
        message=user_msg,
        submission_reason=req.reason,
        submitted_at=result["quiz_submitted_at"]
    )

@router.post("/quiz/violation", response_model=QuizViolationResponse)
def report_quiz_violation(
    req: QuizViolationRequest,
    request: Request,
    response: Response,
    team: Dict[str, Any] = Depends(get_current_team)
):
    """
    Records contestant tab/window leaving event server-authoritatively.
    Issues Warning 1, 2, or 3. Upon 4th violation, automatically terminates attempt and revokes session.
    """
    client_ip = request.client.host if request.client else "unknown"
    violation_rate_limiter.check(f"violation_{team['id']}_{client_ip}")

    tab_id = request.headers.get("X-Tab-ID")
    if tab_id:
        try:
            validate_tab_session(team["id"], tab_id)
        except HTTPException:
            pass

    result = record_violation_for_team(team["id"], req.event_id, req.event_type)
    if result.get("status") == "terminated":
        response.delete_cookie(key="ctf_session", path="/")

    return QuizViolationResponse(
        status=result["status"],
        violation_count=result["violation_count"],
        remaining_warnings=result["remaining_warnings"],
        message=result["message"]
    )

