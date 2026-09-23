import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.database import init_db
from backend.app.import_questions import import_questions_to_db

from backend.app.security import (
    rate_limiter, registration_rate_limiter, login_rate_limiter,
    admin_rate_limiter, answer_rate_limiter, submission_rate_limiter,
    violation_rate_limiter
)

client = TestClient(app)

class TestCTFPlatformBackend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        count = import_questions_to_db()
        assert count == 50, f"Expected 50 questions, got {count}"

    def setUp(self):
        client.cookies.clear()
        self.suffix = uuid.uuid4().hex[:6]
        rate_limiter.attempts.clear()
        registration_rate_limiter.attempts.clear()
        login_rate_limiter.attempts.clear()
        admin_rate_limiter.attempts.clear()
        answer_rate_limiter.attempts.clear()
        submission_rate_limiter.attempts.clear()
        violation_rate_limiter.attempts.clear()

    def test_01_health_check(self):
        res = client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "ok")

    def test_02_registration_validation(self):
        # Invalid team name with space
        res = client.post("/api/register", json={
            "team_name": "Cyber Team",
            "member1_name": "Alice"
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn("cannot contain spaces", res.json()["detail"])

        # Invalid characters
        res = client.post("/api/register", json={
            "team_name": "Cyber#123",
            "member1_name": "Alice"
        })
        self.assertEqual(res.status_code, 400)

        # Valid registration with allowed characters A-Z, a-z, 0-9, !, ?, @, _
        valid_team = f"Cyber_{self.suffix}!?"
        res = client.post("/api/register", json={
            "team_name": valid_team,
            "member1_name": "Alice",
            "member2_name": "Bob"
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["team_name"], valid_team)
        self.assertTrue(len(data["generated_password"]) >= 16)

        # Case-insensitive duplicate check
        res_dup = client.post("/api/register", json={
            "team_name": valid_team.lower(),
            "member1_name": "Charlie"
        })
        self.assertEqual(res_dup.status_code, 400)
        self.assertIn("already registered", res_dup.json()["detail"])

    def test_03_login_and_single_active_session(self):
        # Register a new team
        team_name = f"Team_Sec_{self.suffix}"
        reg_res = client.post("/api/register", json={
            "team_name": team_name,
            "member1_name": "SecTester"
        })
        self.assertEqual(reg_res.status_code, 200)
        password = reg_res.json()["generated_password"]

        # Valid Login 1
        login1 = client.post("/api/login", json={
            "team_name": team_name,
            "password": password
        })
        self.assertEqual(login1.status_code, 200)
        token1 = login1.json()["session_token"]

        # Attempt Login 2 (Single active session check)
        login2 = client.post("/api/login", json={
            "team_name": team_name,
            "password": password
        })
        self.assertEqual(login2.status_code, 409)
        self.assertIn("already logged in", login2.json()["detail"])

        # Verify session with token1
        session_res = client.get("/api/session", headers={"Authorization": f"Bearer {token1}"})
        self.assertEqual(session_res.status_code, 200)
        self.assertEqual(session_res.json()["team_name"], team_name)

    def test_04_quiz_flow_and_privacy(self):
        team_name = f"Team_Quiz_{self.suffix}"
        reg = client.post("/api/register", json={
            "team_name": team_name,
            "member1_name": "TesterQ"
        })
        self.assertEqual(reg.status_code, 200)
        pwd = reg.json()["generated_password"]
        login = client.post("/api/login", json={"team_name": team_name, "password": pwd})
        token = login.json()["session_token"]
        headers = {"Authorization": f"Bearer {token}", "X-Tab-ID": f"tab_{self.suffix}"}

        # Fetch quiz info
        q_info = client.get("/api/quiz", headers=headers).json()
        self.assertTrue(q_info["quiz_started"])
        self.assertEqual(q_info["total_questions"], 50)
        self.assertEqual(len(q_info["questions"]), 50)

        # Check DATA MINIMIZATION: No correct answer or score in quiz info
        self.assertNotIn("correct_answer", str(q_info))
        self.assertNotIn("score", q_info)

        # Fetch question detail
        q1_detail = client.get(f"/api/quiz/questions/{q_info['questions'][0]['question_number']}", headers=headers).json()
        self.assertIn("question_text", q1_detail)
        self.assertIn("option_a", q1_detail)
        self.assertNotIn("correct_answer", q1_detail)

        # Save an answer
        ans_res = client.post("/api/quiz/answer", json={
            "question_number": q_info['questions'][0]['question_number'],
            "selected_answer": "B"
        }, headers=headers)
        self.assertEqual(ans_res.status_code, 200)
        self.assertEqual(ans_res.json()["selected_answer"], "B")

        # Submit quiz
        sub_res = client.post("/api/quiz/submit", headers=headers)
        self.assertEqual(sub_res.status_code, 200)
        sub_data = sub_res.json()
        self.assertTrue(sub_data["success"])
        # CRITICAL PRIVACY: Contestant response must NOT have score or count
        self.assertNotIn("score", sub_data)
        self.assertNotIn("correct_count", sub_data)
        self.assertNotIn("wrong_count", sub_data)
        self.assertNotIn("unanswered_count", sub_data)

        # Post-submission lock: Answer modification rejected
        ans_after = client.post("/api/quiz/answer", json={
            "question_number": 1,
            "selected_answer": "A"
        }, headers=headers)
        self.assertEqual(ans_after.status_code, 401)  # session deactivated on submit

        # Re-login rejected without admin authorization
        relogin = client.post("/api/login", json={"team_name": team_name, "password": pwd})
        self.assertEqual(relogin.status_code, 403)
        self.assertIn("completed the quiz", relogin.json()["detail"])

    def test_04b_anti_cheat_auto_submit(self):
        team_name = f"Team_Blur_{self.suffix}"
        reg = client.post("/api/register", json={
            "team_name": team_name,
            "member1_name": "BlurTester"
        })
        self.assertEqual(reg.status_code, 200)
        pwd = reg.json()["generated_password"]
        login = client.post("/api/login", json={"team_name": team_name, "password": pwd})
        token = login.json()["session_token"]
        headers = {"Authorization": f"Bearer {token}", "X-Tab-ID": f"tab_{self.suffix}"}

        # Start quiz
        client.get("/api/quiz", headers=headers)

        # Trigger visibility_change auto-submit
        auto_res = client.post("/api/quiz/auto-submit", json={"reason": "visibility_change"}, headers=headers)
        self.assertEqual(auto_res.status_code, 200)
        data = auto_res.json()
        self.assertTrue(data["success"])
        self.assertIn("window was left", data["message"])
        self.assertEqual(data["submission_reason"], "visibility_change")
        # Ensure score is strictly hidden from contestant response
        self.assertNotIn("score", data)

    def test_05_admin_features(self):
        # Admin Login
        adm_login = client.post("/api/admin/login", json={
            "username": "ctf_admin",
            "password": "CyberSec_2026!"
        })
        self.assertEqual(adm_login.status_code, 200)
        adm_token = adm_login.json()["session_token"]
        adm_headers = {"Authorization": f"Bearer {adm_token}"}

        # Admin Statistics
        stats = client.get("/api/admin/statistics", headers=adm_headers).json()
        self.assertIn("total_registered_teams", stats)
        self.assertIn("highest_score", stats)
        self.assertTrue(stats["total_registered_teams"] >= 2)

        # Admin Ranking (Top 5)
        ranking = client.get("/api/admin/ranking", headers=adm_headers).json()
        self.assertIsInstance(ranking, list)
        if len(ranking) > 0:
            self.assertIn("score", ranking[0])
            self.assertIn("rank", ranking[0])

        # Admin Results Table
        results = client.get("/api/admin/results", headers=adm_headers).json()
        self.assertIsInstance(results, list)
        if len(results) > 0:
            self.assertIn("score", results[0])
            self.assertIn("correct_count", results[0])
            self.assertIn("wrong_count", results[0])

        # Contestant cannot access admin endpoints
        client.cookies.clear()
        unauth = client.get("/api/admin/statistics")
        self.assertEqual(unauth.status_code, 401)

    def test_06_unauthenticated_protected_endpoint_denied(self):
        client.cookies.clear()
        self.assertEqual(client.get("/api/session").status_code, 401)
        self.assertEqual(client.get("/api/quiz").status_code, 401)
        self.assertEqual(client.get("/api/admin/statistics").status_code, 401)
        self.assertEqual(client.get("/api/admin/teams").status_code, 401)
        self.assertEqual(client.get("/api/admin/results").status_code, 401)

    def test_07_contestant_cannot_access_admin_endpoint(self):
        team_name = f"Team_BFLA_{self.suffix}"
        reg = client.post("/api/register", json={"team_name": team_name, "member1_name": "Tester"})
        pwd = reg.json()["generated_password"]
        login = client.post("/api/login", json={"team_name": team_name, "password": pwd})
        token = login.json()["session_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Attempt admin endpoint access with contestant session token
        res_stats = client.get("/api/admin/statistics", headers=headers)
        self.assertEqual(res_stats.status_code, 401)
        self.assertIn("session", res_stats.json()["detail"].lower())

        res_results = client.get("/api/admin/results", headers=headers)
        self.assertEqual(res_results.status_code, 401)

    def test_08_cross_team_access_and_identity_tampering(self):
        # Register Team A and Team B
        team_a_name = f"Team_A_{self.suffix}"
        reg_a = client.post("/api/register", json={"team_name": team_a_name, "member1_name": "Alice"})
        pwd_a = reg_a.json()["generated_password"]
        token_a = client.post("/api/login", json={"team_name": team_a_name, "password": pwd_a}).json()["session_token"]

        team_b_name = f"Team_B_{self.suffix}"
        reg_b = client.post("/api/register", json={"team_name": team_b_name, "member1_name": "Bob"})
        pwd_b = reg_b.json()["generated_password"]
        token_b = client.post("/api/login", json={"team_name": team_b_name, "password": pwd_b}).json()["session_token"]

        # Team A session query returns Team A session details regardless of any query/body parameters
        sess_a = client.get("/api/session", headers={"Authorization": f"Bearer {token_a}"}).json()
        self.assertEqual(sess_a["team_name"], team_a_name)

        # Team B session query returns Team B details
        sess_b = client.get("/api/session", headers={"Authorization": f"Bearer {token_b}"}).json()
        self.assertEqual(sess_b["team_name"], team_b_name)

    def test_09_rate_limiting_enforcement(self):
        # Test rate limiting on contestant login
        suffix = uuid.uuid4().hex[:6]
        ip_fake = f"192.168.1.{self.suffix[:2]}"
        headers = {"X-Forwarded-For": ip_fake}

        for _ in range(25):
            res = client.post("/api/login", json={"team_name": "NonExistentTeam", "password": "WrongPassword!"}, headers=headers)
            if res.status_code == 429:
                break
        self.assertEqual(res.status_code, 429)
        self.assertIn("Too many attempts", res.json()["detail"])

    def test_10_exception_redaction_and_data_privacy(self):
        # Test data privacy for contestant quiz info
        team_name = f"Team_Priv_{self.suffix}"
        reg = client.post("/api/register", json={"team_name": team_name, "member1_name": "PrivTester"})
        pwd = reg.json()["generated_password"]
        token = client.post("/api/login", json={"team_name": team_name, "password": pwd}).json()["session_token"]
        headers = {"Authorization": f"Bearer {token}", "X-Tab-ID": f"tab_{self.suffix}"}

        q_info = client.get("/api/quiz", headers=headers).json()
        self.assertNotIn("correct_answer", str(q_info))
        self.assertNotIn("ctf_quiz.db", str(q_info))
        self.assertNotIn("traceback", str(q_info))

    def test_11_single_tab_enforcement_and_stale_tab_rejection(self):
        # Single-tab scenarios A, B, C, D
        team_name = f"Team_Tab_{self.suffix}"
        reg = client.post("/api/register", json={"team_name": team_name, "member1_name": "TabTester"})
        pwd = reg.json()["generated_password"]
        token = client.post("/api/login", json={"team_name": team_name, "password": pwd}).json()["session_token"]

        tab_a_headers = {"Authorization": f"Bearer {token}", "X-Tab-ID": "tab_A_unique"}
        tab_b_headers = {"Authorization": f"Bearer {token}", "X-Tab-ID": "tab_B_unique"}

        # Scenario A: Start quiz in Tab A
        quiz_a = client.get("/api/quiz", headers=tab_a_headers)
        self.assertEqual(quiz_a.status_code, 200)

        # Tab A answers question 1
        ans_a = client.post("/api/quiz/answer", json={"question_number": 1, "selected_answer": "C"}, headers=tab_a_headers)
        self.assertEqual(ans_a.status_code, 200)

        # Scenario B: Refresh same Tab A (same tab_id)
        ref_a = client.get("/api/quiz", headers=tab_a_headers)
        self.assertEqual(ref_a.status_code, 200)
        self.assertFalse(ref_a.json()["quiz_submitted"])

        # Scenario C: Open second tab (Tab B with tab_B_unique)
        quiz_b = client.get("/api/quiz", headers=tab_b_headers)
        self.assertEqual(quiz_b.status_code, 403)
        self.assertIn("Multiple active quiz tabs detected", quiz_b.json()["detail"])

        # Scenario D: Old Tab A attempt to answer after second tab event -> rejected by backend
        ans_stale = client.post("/api/quiz/answer", json={"question_number": 2, "selected_answer": "A"}, headers=tab_a_headers)
        self.assertIn(ans_stale.status_code, [401, 403])

    def test_13_violation_warning_flow_and_termination(self):
        # Register and login team
        team_name = f"Team_Vio_{self.suffix}"
        reg = client.post("/api/register", json={"team_name": team_name, "member1_name": "VioTester"})
        pwd = reg.json()["generated_password"]
        login = client.post("/api/login", json={"team_name": team_name, "password": pwd})
        token = login.json()["session_token"]
        headers = {"Authorization": f"Bearer {token}", "X-Tab-ID": f"tab_vio_{self.suffix}"}

        # Start quiz
        q_state = client.get("/api/quiz", headers=headers).json()
        self.assertEqual(q_state["violation_count"], 0)
        self.assertEqual(q_state["remaining_warnings"], 3)

        # Violation #1 -> Warning 1 of 3
        v1 = client.post("/api/quiz/violation", json={"event_id": f"evt_1_{self.suffix}", "event_type": "visibility_change"}, headers=headers)
        self.assertEqual(v1.status_code, 200)
        d1 = v1.json()
        self.assertEqual(d1["status"], "warning")
        self.assertEqual(d1["violation_count"], 1)
        self.assertEqual(d1["remaining_warnings"], 2)
        self.assertIn("WARNING 1 OF 3", d1["message"])

        # Refresh check: warning count survives refresh
        q_state_ref = client.get("/api/quiz", headers=headers).json()
        self.assertEqual(q_state_ref["violation_count"], 1)
        self.assertEqual(q_state_ref["remaining_warnings"], 2)

        # Idempotency check: duplicate event_id does not increment counter
        v1_dup = client.post("/api/quiz/violation", json={"event_id": f"evt_1_{self.suffix}", "event_type": "visibility_change"}, headers=headers)
        self.assertEqual(v1_dup.json()["violation_count"], 1)

        # Wait 1.6s for cooldown to elapse
        import time
        time.sleep(1.6)

        # Violation #2 -> Warning 2 of 3
        v2 = client.post("/api/quiz/violation", json={"event_id": f"evt_2_{self.suffix}", "event_type": "window_blur"}, headers=headers)
        self.assertEqual(v2.status_code, 200)
        d2 = v2.json()
        self.assertEqual(d2["status"], "warning")
        self.assertEqual(d2["violation_count"], 2)
        self.assertEqual(d2["remaining_warnings"], 1)
        self.assertIn("WARNING 2 OF 3", d2["message"])

        time.sleep(1.6)

        # Violation #3 -> Warning 3 of 3 (Final warning)
        v3 = client.post("/api/quiz/violation", json={"event_id": f"evt_3_{self.suffix}", "event_type": "visibility_change"}, headers=headers)
        self.assertEqual(v3.status_code, 200)
        d3 = v3.json()
        self.assertEqual(d3["status"], "warning")
        self.assertEqual(d3["violation_count"], 3)
        self.assertEqual(d3["remaining_warnings"], 0)
        self.assertIn("WARNING 3 OF 3", d3["message"])

        time.sleep(1.6)

        # Violation #4 -> AUTOMATIC TERMINATION / QUIZ SUBMITTED / SESSION INVALIDATED
        v4 = client.post("/api/quiz/violation", json={"event_id": f"evt_4_{self.suffix}", "event_type": "visibility_change"}, headers=headers)
        self.assertEqual(v4.status_code, 200)
        d4 = v4.json()
        self.assertEqual(d4["status"], "terminated")
        self.assertEqual(d4["violation_count"], 4)
        self.assertEqual(d4["remaining_warnings"], 0)
        self.assertIn("TERMINATED", d4["message"])

        # Verify backend session is now invalidated / quiz locked
        ans_post_term = client.post("/api/quiz/answer", json={"question_number": 1, "selected_answer": "A"}, headers=headers)
        self.assertIn(ans_post_term.status_code, [401, 403])

        # Verify re-login is locked
        relog = client.post("/api/login", json={"team_name": team_name, "password": pwd})
        self.assertEqual(relog.status_code, 403)
        self.assertIn("completed the quiz", relog.json()["detail"])

    def test_12_duplicate_submission_idempotency(self):
        team_name = f"Team_DupSub_{self.suffix}"
        reg = client.post("/api/register", json={"team_name": team_name, "member1_name": "DupTester"})
        pwd = reg.json()["generated_password"]
        token = client.post("/api/login", json={"team_name": team_name, "password": pwd}).json()["session_token"]
        headers = {"Authorization": f"Bearer {token}", "X-Tab-ID": f"tab_{self.suffix}"}

        client.get("/api/quiz", headers=headers)
        sub1 = client.post("/api/quiz/submit", headers=headers)
        self.assertEqual(sub1.status_code, 200)
        sub1_data = sub1.json()

        # Duplicate submit call
        sub2 = client.post("/api/quiz/submit", headers=headers)
        # Session deactivated on first submit so subsequent call is 401
        self.assertEqual(sub2.status_code, 401)

if __name__ == "__main__":
    unittest.main()
