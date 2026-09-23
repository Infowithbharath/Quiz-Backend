import os
import sqlite3
from contextlib import contextmanager
from typing import Generator

import shutil

# Authoritative database path resolver
def get_db_path() -> str:
    if os.environ.get("CTF_DB_PATH"):
        return os.environ["CTF_DB_PATH"]
    # If on Vercel serverless or read-only filesystem, always use /tmp
    if os.environ.get("VERCEL") or not os.access(os.path.dirname(os.path.abspath(__file__)), os.W_OK):
        tmp_db = "/tmp/ctf_quiz.db"
        if not os.path.exists(tmp_db):
            repo_db = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "ctf_quiz.db"))
            if os.path.exists(repo_db):
                try:
                    shutil.copyfile(repo_db, tmp_db)
                except Exception as e:
                    print(f"[Seed DB Copy Warning]: {e}")
        return tmp_db
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "ctf_quiz.db"))

def get_connection() -> sqlite3.Connection:
    """Create and configure a SQLite connection with WAL mode and foreign keys enabled."""
    db_file = get_db_path()
    os.makedirs(os.path.dirname(db_file), exist_ok=True)
    conn = sqlite3.connect(db_file, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    if os.environ.get("VERCEL"):
        conn.execute("PRAGMA journal_mode = DELETE;")
    else:
        conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn

_DB_INITIALIZED = False

def ensure_db_initialized() -> None:
    global _DB_INITIALIZED
    if not _DB_INITIALIZED:
        _DB_INITIALIZED = True
        init_db()
        try:
            from .import_questions import import_questions_to_db
            import_questions_to_db(force=False)
        except Exception as e:
            print(f"[Lazy Import Note]: {e}")

@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Context manager for SQLite database connections."""
    ensure_db_initialized()
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db() -> None:
    """Initialize database tables, constraints, and indexes."""
    global _DB_INITIALIZED
    db_file = get_db_path()
    os.makedirs(os.path.dirname(db_file), exist_ok=True)
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        # 1. Teams table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_name TEXT NOT NULL,
            team_name_lower TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            member1_name TEXT NOT NULL,
            member2_name TEXT,
            registered_at TEXT NOT NULL,
            login_used INTEGER NOT NULL DEFAULT 0,
            active_session_id TEXT,
            quiz_started_at TEXT,
            quiz_submitted_at TEXT,
            submission_reason TEXT,
            score INTEGER,
            correct_count INTEGER,
            wrong_count INTEGER,
            unanswered_count INTEGER,
            duration_seconds REAL,
            allow_relogin INTEGER NOT NULL DEFAULT 0,
            active_tab_id TEXT,
            violation_count INTEGER NOT NULL DEFAULT 0,
            last_violation_at TEXT
        );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_teams_lower ON teams(team_name_lower);")
        
        # Schema migration check: Add columns if missing in existing database
        cursor.execute("PRAGMA table_info(teams);")
        columns = [col["name"] for col in cursor.fetchall()]
        if "active_tab_id" not in columns:
            cursor.execute("ALTER TABLE teams ADD COLUMN active_tab_id TEXT;")
        if "violation_count" not in columns:
            cursor.execute("ALTER TABLE teams ADD COLUMN violation_count INTEGER NOT NULL DEFAULT 0;")
        if "last_violation_at" not in columns:
            cursor.execute("ALTER TABLE teams ADD COLUMN last_violation_at TEXT;")

        # Processed Violation Events table for idempotency
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS processed_violation_events (
            event_id TEXT PRIMARY KEY,
            team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            processed_at TEXT NOT NULL
        );
        """)
        
        # 2. Quiz questions table (holds authoritative 50 questions)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS quiz_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_number INTEGER NOT NULL UNIQUE,
            question_text TEXT NOT NULL,
            option_a TEXT NOT NULL,
            option_b TEXT NOT NULL,
            option_c TEXT NOT NULL,
            option_d TEXT NOT NULL,
            correct_answer TEXT NOT NULL,
            section TEXT NOT NULL,
            marks INTEGER NOT NULL DEFAULT 2
        );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_questions_num ON quiz_questions(question_number);")

        # 3. Team Question Order table (persists server-authoritative randomized question sequence per team)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS team_question_order (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL UNIQUE REFERENCES teams(id) ON DELETE CASCADE,
            order_json TEXT NOT NULL
        );
        """)
        
        # 4. Answers table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            question_number INTEGER NOT NULL,
            selected_answer TEXT NOT NULL,
            answered_at TEXT NOT NULL,
            UNIQUE(team_id, question_number)
        );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_answers_team ON answers(team_id);")

        # 5. Sessions table (Contestants)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            session_token TEXT NOT NULL UNIQUE,
            login_time TEXT NOT NULL,
            last_activity TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            logout_time TEXT
        );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(session_token);")

        # 6. Admin table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """)

        # 7. Admin Sessions table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS admin_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
            session_token TEXT NOT NULL UNIQUE,
            login_time TEXT NOT NULL,
            last_activity TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_admin_sessions_token ON admin_sessions(session_token);")

        # 8. Audit logs table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            team_id INTEGER,
            details TEXT
        );
        """)
        conn.commit()
    finally:
        conn.close()
