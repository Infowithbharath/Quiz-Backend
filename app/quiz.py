import json
import random
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from fastapi import HTTPException
from .database import get_db

QUIZ_DURATION_SECONDS = 1800  # 30 minutes

def get_or_create_team_question_order(team_id: int) -> List[int]:
    """
    Returns the persistent server-authoritative randomized question order for the team.
    If not yet generated, creates a random permutation of [1..50] and stores it in the database.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT order_json FROM team_question_order WHERE team_id = ?", (team_id,))
        row = cursor.fetchone()
        if row:
            return json.loads(row["order_json"])

        # Create randomized permutation of questions 1..50
        order = list(range(1, 51))
        random.SystemRandom().shuffle(order)
        order_json = json.dumps(order)

        cursor.execute("""
            INSERT INTO team_question_order (team_id, order_json)
            VALUES (?, ?)
            ON CONFLICT(team_id) DO UPDATE SET order_json = excluded.order_json;
        """, (team_id, order_json))

        return order

def validate_tab_session(team_id: int, tab_id: Optional[str]) -> str:
    """
    Validates single tab requirement for an active quiz attempt.
    - If tab_id is not provided, raises 400.
    - If team's active_tab_id is NULL (first tab access), sets active_tab_id to tab_id.
    - If team's active_tab_id matches tab_id, validation passes.
    - If team's active_tab_id does NOT match tab_id, invalidates/auto-submits the quiz attempt
      with reason='multiple_tabs', clears session/tab ID, and raises 403.
    """
    if not tab_id or not str(tab_id).strip():
        raise HTTPException(status_code=400, detail="Tab session identifier (X-Tab-ID) required.")

    clean_tab_id = str(tab_id).strip()

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT active_tab_id, quiz_submitted_at FROM teams WHERE id = ?", (team_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Team not found.")

        if row["quiz_submitted_at"]:
            return clean_tab_id

        current_active_tab = row["active_tab_id"]

        if not current_active_tab:
            cursor.execute("UPDATE teams SET active_tab_id = ? WHERE id = ?", (clean_tab_id, team_id))
            return clean_tab_id
        elif current_active_tab == clean_tab_id:
            return clean_tab_id
        else:
            cursor.execute("UPDATE teams SET active_tab_id = NULL WHERE id = ?", (team_id,))
            conn.commit()
            submit_quiz_for_team(team_id, reason="multiple_tabs")
            raise HTTPException(
                status_code=403,
                detail="Multiple active quiz tabs detected. Your attempt has been terminated and auto-submitted."
            )


def record_violation_for_team(team_id: int, event_id: str, event_type: str = "visibility_change") -> Dict[str, Any]:
    """
    Server-authoritative violation counter for active quiz attempts.
    Handles idempotency (event_id check) and short cooldown window (1.5 seconds).
    Increments violation count atomically up to 3 warnings.
    Upon 4th violation, automatically terminates/submits attempt and revokes session.
    """
    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT quiz_started_at, quiz_submitted_at, violation_count, last_violation_at, submission_reason
            FROM teams WHERE id = ?
        """, (team_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Team not found.")

        if not row["quiz_started_at"] or row["quiz_submitted_at"]:
            curr_c = row["violation_count"] or 0
            return {
                "status": "terminated" if row["quiz_submitted_at"] else "inactive",
                "violation_count": curr_c,
                "remaining_warnings": max(0, 3 - curr_c),
                "message": "Quiz is not currently active."
            }

        curr_count = row["violation_count"] or 0

        # Check idempotency: event_id already processed?
        cursor.execute("SELECT event_id FROM processed_violation_events WHERE event_id = ?", (event_id,))
        if cursor.fetchone():
            return {
                "status": "warning" if curr_count < 4 else "terminated",
                "violation_count": curr_count,
                "remaining_warnings": max(0, 3 - curr_count),
                "message": f"Warning {curr_count} of 3." if curr_count < 4 else "Quiz terminated."
            }

        # Check cooldown window (1.5 seconds) to prevent multi-event burst (blur + visibilitychange)
        last_v_at = row["last_violation_at"]
        if last_v_at:
            try:
                last_dt = datetime.fromisoformat(last_v_at)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                if (now_utc - last_dt).total_seconds() < 1.5:
                    cursor.execute("""
                        INSERT OR IGNORE INTO processed_violation_events (event_id, team_id, processed_at)
                        VALUES (?, ?, ?)
                    """, (event_id, team_id, now_iso))
                    return {
                        "status": "warning" if curr_count < 4 else "terminated",
                        "violation_count": curr_count,
                        "remaining_warnings": max(0, 3 - curr_count),
                        "message": f"Warning {curr_count} of 3." if curr_count < 4 else "Quiz terminated."
                    }
            except Exception:
                pass

        # Record new processed event
        cursor.execute("""
            INSERT OR IGNORE INTO processed_violation_events (event_id, team_id, processed_at)
            VALUES (?, ?, ?)
        """, (event_id, team_id, now_iso))

        new_count = curr_count + 1
        cursor.execute("""
            UPDATE teams SET violation_count = ?, last_violation_at = ? WHERE id = ?
        """, (new_count, now_iso, team_id))

        cursor.execute("""
            INSERT INTO audit_logs (timestamp, event_type, team_id, details)
            VALUES (?, 'QUIZ_VIOLATION', ?, ?)
        """, (now_iso, team_id, f"Violation #{new_count}, event_type={event_type}, event_id={event_id}"))

        if new_count >= 4:
            conn.commit()
            submit_quiz_for_team(team_id, reason="violation_limit")
            return {
                "status": "terminated",
                "violation_count": new_count,
                "remaining_warnings": 0,
                "message": "QUIZ SESSION TERMINATED: Maximum number of tab/window violations exceeded."
            }
        else:
            warnings_left = max(0, 3 - new_count)
            messages = {
                1: "WARNING 1 OF 3: You have left the active quiz window. Please return to the quiz immediately.",
                2: "WARNING 2 OF 3: You have left the active quiz window again. Please remain on the quiz page.",
                3: "WARNING 3 OF 3: Final warning. Any further tab/window/page violation will automatically terminate your quiz session."
            }
            msg = messages.get(new_count, f"Warning {new_count} of 3.")
            return {
                "status": "warning",
                "violation_count": new_count,
                "remaining_warnings": warnings_left,
                "message": msg
            }

def get_quiz_state_for_team(team: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fetches the full quiz metadata for the authenticated team.
    Initializes quiz_started_at if not yet started.
    Handles automatic timeout submission if 30 minutes have elapsed.
    Guarantees DATA MINIMIZATION: Never returns correct answers or scores.
    """
    team_id = team["id"]
    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()

    with get_db() as conn:
        cursor = conn.cursor()

        # Fetch latest team quiz state
        cursor.execute("""
            SELECT quiz_started_at, quiz_submitted_at, submission_reason, violation_count
            FROM teams WHERE id = ?
        """, (team_id,))
        t_row = cursor.fetchone()
        if not t_row:
            raise HTTPException(status_code=404, detail="Team not found.")

        started_at_str = t_row["quiz_started_at"]
        submitted_at_str = t_row["quiz_submitted_at"]
        submission_reason = t_row["submission_reason"]
        violation_count = t_row["violation_count"] or 0

        # If already submitted, return submitted status
        if submitted_at_str:
            return {
                "quiz_started": True,
                "quiz_submitted": True,
                "submission_reason": submission_reason,
                "violation_count": violation_count,
                "remaining_warnings": 0,
                "remaining_seconds": 0,
                "duration_seconds": QUIZ_DURATION_SECONDS,
                "total_questions": 50,
                "questions_order": [],
                "answered_count": 0,
                "answers": {}
            }

        # If not started, start timer now
        if not started_at_str:
            cursor.execute("UPDATE teams SET quiz_started_at = ? WHERE id = ?", (now_iso, team_id))
            started_at_str = now_iso
            started_dt = now_utc
        else:
            try:
                started_dt = datetime.fromisoformat(started_at_str)
                if started_dt.tzinfo is None:
                    started_dt = started_dt.replace(tzinfo=timezone.utc)
            except Exception:
                started_dt = now_utc

        # Calculate remaining time
        elapsed = (now_utc - started_dt).total_seconds()
        remaining_seconds = max(0.0, float(QUIZ_DURATION_SECONDS - elapsed))

        # Check for timeout auto-submit
        if remaining_seconds <= 0.0:
            submit_quiz_for_team(team_id, reason="timeout")
            return {
                "quiz_started": True,
                "quiz_submitted": True,
                "submission_reason": "timeout",
                "violation_count": violation_count,
                "remaining_warnings": 0,
                "remaining_seconds": 0,
                "duration_seconds": QUIZ_DURATION_SECONDS,
                "total_questions": 50,
                "questions_order": [],
                "answered_count": 0,
                "answers": {}
            }

        # Fetch question order
        cursor.execute("SELECT order_json FROM team_question_order WHERE team_id = ?", (team_id,))
        order_row = cursor.fetchone()
        if order_row:
            order = json.loads(order_row["order_json"])
        else:
            order = get_or_create_team_question_order(team_id)

        # Fetch team answers so far
        cursor.execute("SELECT question_number, selected_answer FROM answers WHERE team_id = ?", (team_id,))
        answers_rows = cursor.fetchall()
        answers_map = {row["question_number"]: row["selected_answer"] for row in answers_rows}

        # Fetch question metadata list (without correct answers)
        cursor.execute("""
            SELECT question_number, section, marks
            FROM quiz_questions
        """)
        meta_rows = {row["question_number"]: dict(row) for row in cursor.fetchall()}

        # Build ordered list of questions for the contestant navigator
        navigator_list = []
        for idx, q_num in enumerate(order, start=1):
            q_meta = meta_rows.get(q_num, {"section": "Easy", "marks": 2})
            navigator_list.append({
                "index": idx,
                "question_number": q_num,
                "section": q_meta["section"],
                "marks": q_meta["marks"],
                "is_answered": q_num in answers_map,
                "selected_answer": answers_map.get(q_num)
            })

        return {
            "quiz_started": True,
            "quiz_submitted": False,
            "quiz_started_at": started_at_str,
            "duration_seconds": QUIZ_DURATION_SECONDS,
            "remaining_seconds": round(remaining_seconds, 1),
            "total_questions": 50,
            "violation_count": violation_count,
            "remaining_warnings": max(0, 3 - violation_count),
            "answered_count": len(answers_map),
            "unanswered_count": 50 - len(answers_map),
            "questions": navigator_list,
            "answers": answers_map
        }

def get_question_detail_for_contestant(team_id: int, question_number: int) -> Dict[str, Any]:
    """
    Returns single question details for contestant display.
    Guarantees DATA MINIMIZATION: Never returns correct_answer.
    """
    if not (1 <= question_number <= 50):
        raise HTTPException(status_code=400, detail="Invalid question number.")

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT question_number, question_text, option_a, option_b, option_c, option_d, section, marks
            FROM quiz_questions
            WHERE question_number = ?
        """, (question_number,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Question not found.")

        # Check if team has already selected an answer
        cursor.execute("""
            SELECT selected_answer FROM answers
            WHERE team_id = ? AND question_number = ?
        """, (team_id, question_number))
        ans_row = cursor.fetchone()
        selected = ans_row["selected_answer"] if ans_row else None

        data = dict(row)
        data["selected_answer"] = selected
        return data

def record_answer_for_team(team_id: int, question_number: int, selected_answer: str) -> Dict[str, Any]:
    """
    Persists answer to the database. Validates that quiz is currently active and not expired or submitted.
    """
    if not (1 <= question_number <= 50):
        raise HTTPException(status_code=400, detail="Invalid question number.")
    if selected_answer not in ("A", "B", "C", "D"):
        raise HTTPException(status_code=400, detail="Invalid option choice. Must be A, B, C, or D.")

    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT quiz_started_at, quiz_submitted_at
            FROM teams WHERE id = ?
        """, (team_id,))
        team_row = cursor.fetchone()
        if not team_row:
            raise HTTPException(status_code=404, detail="Team not found.")

        if team_row["quiz_submitted_at"]:
            raise HTTPException(status_code=403, detail="Quiz has already been submitted. No answer modifications allowed.")

        started_at_str = team_row["quiz_started_at"]
        if not started_at_str:
            raise HTTPException(status_code=400, detail="Quiz has not been started yet.")

        # Check timer expiration
        try:
            started_dt = datetime.fromisoformat(started_at_str)
            if started_dt.tzinfo is None:
                started_dt = started_dt.replace(tzinfo=timezone.utc)
            elapsed = (now_utc - started_dt).total_seconds()
            if elapsed > QUIZ_DURATION_SECONDS:
                submit_quiz_for_team(team_id, reason="timeout")
                raise HTTPException(status_code=403, detail="Time expired. Your quiz has been automatically submitted.")
        except HTTPException:
            raise
        except Exception:
            pass

        # Upsert answer
        cursor.execute("""
            INSERT INTO answers (team_id, question_number, selected_answer, answered_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(team_id, question_number) DO UPDATE SET
                selected_answer = excluded.selected_answer,
                answered_at = excluded.answered_at;
        """, (team_id, question_number, selected_answer, now_iso))

        # Count total answered
        cursor.execute("SELECT COUNT(*) FROM answers WHERE team_id = ?", (team_id,))
        answered_count = cursor.fetchone()[0]

        return {
            "success": True,
            "question_number": question_number,
            "selected_answer": selected_answer,
            "answered_count": answered_count,
            "unanswered_count": 50 - answered_count,
            "answered_at": now_iso
        }

def submit_quiz_for_team(team_id: int, reason: str = "manual") -> Dict[str, Any]:
    """
    Calculates final score on server, records duration and submission reason, locks the quiz attempt.
    CRITICAL: Returns server-side dict for internal and admin storage.
    Contestant router filters this output to return ONLY confirmation.
    """
    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()

    valid_reasons = ("manual", "timeout", "visibility_change", "window_blur", "administrator_action", "multiple_tabs", "violation_limit")
    if reason not in valid_reasons:
        reason = "manual"

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT quiz_started_at, quiz_submitted_at, score
            FROM teams WHERE id = ?
        """, (team_id,))
        team_row = cursor.fetchone()
        if not team_row:
            raise HTTPException(status_code=404, detail="Team not found.")

        # If already submitted, preserve previous result
        if team_row["quiz_submitted_at"]:
            return {
                "already_submitted": True,
                "submission_reason": team_row.get("submission_reason") or reason,
                "quiz_submitted_at": team_row["quiz_submitted_at"]
            }

        started_at_str = team_row["quiz_started_at"]
        duration_seconds = 0.0
        if started_at_str:
            try:
                started_dt = datetime.fromisoformat(started_at_str)
                if started_dt.tzinfo is None:
                    started_dt = started_dt.replace(tzinfo=timezone.utc)
                duration_seconds = max(0.0, (now_utc - started_dt).total_seconds())
            except Exception:
                duration_seconds = 0.0

        # Calculate score from authoritative questions
        cursor.execute("SELECT question_number, correct_answer, marks FROM quiz_questions;")
        q_rows = cursor.fetchall()
        master_keys = {row["question_number"]: (row["correct_answer"], row["marks"]) for row in q_rows}

        cursor.execute("SELECT question_number, selected_answer FROM answers WHERE team_id = ?", (team_id,))
        ans_rows = cursor.fetchall()
        team_answers = {row["question_number"]: row["selected_answer"] for row in ans_rows}

        correct_count = 0
        total_score = 0
        for q_num, (correct_ans, marks) in master_keys.items():
            if q_num in team_answers and team_answers[q_num] == correct_ans:
                correct_count += 1
                total_score += marks

        answered_count = len(team_answers)
        unanswered_count = 50 - answered_count
        wrong_count = answered_count - correct_count

        # Update teams table to lock attempt
        cursor.execute("""
            UPDATE teams SET
                quiz_submitted_at = ?,
                submission_reason = ?,
                score = ?,
                correct_count = ?,
                wrong_count = ?,
                unanswered_count = ?,
                duration_seconds = ?,
                login_used = 1,
                active_session_id = NULL,
                active_tab_id = NULL
            WHERE id = ?
        """, (now_iso, reason, total_score, correct_count, wrong_count, unanswered_count, duration_seconds, team_id))

        # Deactivate all active sessions for this team
        cursor.execute("""
            UPDATE sessions SET active = 0, logout_time = ?
            WHERE team_id = ? AND active = 1
        """, (now_iso, team_id))

        # Log audit event
        cursor.execute("""
            INSERT INTO audit_logs (timestamp, event_type, team_id, details)
            VALUES (?, 'QUIZ_SUBMISSION', ?, ?)
        """, (now_iso, team_id, f"Reason: {reason}, Duration: {duration_seconds:.1f}s"))

        return {
            "already_submitted": False,
            "submission_reason": reason,
            "quiz_submitted_at": now_iso,
            "score": total_score,
            "correct_count": correct_count,
            "wrong_count": wrong_count,
            "unanswered_count": unanswered_count,
            "duration_seconds": duration_seconds
        }
