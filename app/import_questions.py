import os
import re
import sqlite3
from typing import List, Dict, Any
from .database import get_db

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_LOCAL_QUESTIONS_PATH = os.path.join(_PROJECT_ROOT, "questions", "CTF_Challenge_50_Questions.txt")
_BACKEND_QUESTIONS_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "questions", "CTF_Challenge_50_Questions.txt"))
if os.path.exists(_BACKEND_QUESTIONS_PATH):
    QUESTIONS_FILE_PATH = _BACKEND_QUESTIONS_PATH
elif os.path.exists(_LOCAL_QUESTIONS_PATH):
    QUESTIONS_FILE_PATH = _LOCAL_QUESTIONS_PATH
else:
    QUESTIONS_FILE_PATH = os.path.abspath(r"C:\CTF\questions\CTF_Challenge_50_Questions.txt")

def parse_questions_file(file_path: str = QUESTIONS_FILE_PATH) -> List[Dict[str, Any]]:
    """
    Parses and rigorously validates exactly 50 questions from the authoritative text file.
    Raises ValueError or FileNotFoundError if anything is incomplete, missing, or malformed.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Authoritative question file not found at: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Parse master answer key from summary table at the bottom
    answer_key: Dict[int, str] = {}
    for m in re.finditer(r"Q(\d+):\s*\[\s*([A-D])\s*\]", content):
        q_num = int(m.group(1))
        ans = m.group(2)
        answer_key[q_num] = ans

    if len(answer_key) != 50:
        raise ValueError(
            f"Expected exactly 50 answers in master answer key, but found {len(answer_key)}."
        )

    # 2. Parse questions
    # Split content by question headers (Q1., Q2., etc.)
    q_blocks = re.split(r"\n(?=Q\d+\.)", content)
    parsed_questions: List[Dict[str, Any]] = []

    for block in q_blocks:
        m = re.match(r"^Q(\d+)\.\s*(.+?)(?=\nA\))", block, re.DOTALL)
        if not m:
            continue
        q_num = int(m.group(1))
        q_text = m.group(2).strip()

        # Extract 4 options
        opt_a = re.search(r"\nA\)\s*(.+?)(?=\nB\))", block, re.DOTALL)
        opt_b = re.search(r"\nB\)\s*(.+?)(?=\nC\))", block, re.DOTALL)
        opt_c = re.search(r"\nC\)\s*(.+?)(?=\nD\))", block, re.DOTALL)
        opt_d = re.search(r"\nD\)\s*(.+?)(?=\n\n|\Z)", block, re.DOTALL)

        if not (opt_a and opt_b and opt_c and opt_d):
            raise ValueError(f"Question Q{q_num} does not have all 4 options (A, B, C, D).")

        correct_ans = answer_key.get(q_num)
        if not correct_ans or correct_ans not in ("A", "B", "C", "D"):
            raise ValueError(f"Missing or invalid master answer for Q{q_num}: {correct_ans}")

        # Section and marks validation according to quiz specification:
        # Q1-Q15: Easy (15 Qs | 30 Marks)
        # Q16-Q30: Medium (15 Qs | 30 Marks)
        # Q31-Q50: Hard (20 Qs | 40 Marks)
        if 1 <= q_num <= 15:
            section = "Easy"
        elif 16 <= q_num <= 30:
            section = "Medium"
        elif 31 <= q_num <= 50:
            section = "Hard"
        else:
            raise ValueError(f"Question number {q_num} is outside expected range 1..50.")

        parsed_questions.append({
            "question_number": q_num,
            "question_text": q_text,
            "option_a": opt_a.group(1).strip(),
            "option_b": opt_b.group(1).strip(),
            "option_c": opt_c.group(1).strip(),
            "option_d": opt_d.group(1).strip(),
            "correct_answer": correct_ans,
            "section": section,
            "marks": 2
        })

    # Sort by question number to ensure sequential order
    parsed_questions.sort(key=lambda q: q["question_number"])

    if len(parsed_questions) != 50:
        raise ValueError(
            f"Strict verification failed: Expected exactly 50 parsed questions, got {len(parsed_questions)}."
        )

    # Verify no missing numbers in 1..50
    found_numbers = [q["question_number"] for q in parsed_questions]
    if found_numbers != list(range(1, 51)):
        raise ValueError(f"Question numbering sequence is broken: {found_numbers}")

    return parsed_questions

def import_questions_to_db(force: bool = False) -> int:
    """
    Imports the parsed 50 questions into SQLite database.
    If questions already exist and count == 50, does not duplicate unless force=True.
    Returns count of imported questions.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM quiz_questions;")
        existing_count = cursor.fetchone()[0]

        if existing_count == 50 and not force:
            return 50

        questions = parse_questions_file()

        if force:
            cursor.execute("DELETE FROM quiz_questions;")

        cursor.executemany("""
            INSERT INTO quiz_questions 
            (question_number, question_text, option_a, option_b, option_c, option_d, correct_answer, section, marks)
            VALUES (:question_number, :question_text, :option_a, :option_b, :option_c, :option_d, :correct_answer, :section, :marks)
            ON CONFLICT(question_number) DO UPDATE SET
                question_text = excluded.question_text,
                option_a = excluded.option_a,
                option_b = excluded.option_b,
                option_c = excluded.option_c,
                option_d = excluded.option_d,
                correct_answer = excluded.correct_answer,
                section = excluded.section,
                marks = excluded.marks;
        """, questions)

        # Final verification in database
        cursor.execute("SELECT COUNT(*) FROM quiz_questions;")
        final_count = cursor.fetchone()[0]
        if final_count != 50:
            raise RuntimeError(f"Database import verification failed. Total in DB: {final_count}, expected: 50.")

        return final_count

if __name__ == "__main__":
    count = import_questions_to_db()
    print(f"Successfully verified and imported exactly {count} CTF questions.")
