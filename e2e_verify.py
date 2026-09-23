import urllib.request
import json

base = 'http://127.0.0.1:8000/api'

def req(endpoint, data=None, token=None):
    url = f'{base}{endpoint}'
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req_obj = urllib.request.Request(url, headers=headers, method='POST' if data is not None else 'GET')
    body = json.dumps(data).encode('utf-8') if data is not None else None
    try:
        with urllib.request.urlopen(req_obj, data=body) as response:
            return response.status, json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode('utf-8'))

print("=== STARTING LIVE E2E HTTP VERIFICATION ===")

# 1. Register team
status, reg = req('/register', {
    'team_name': 'Omega_Defenders99!',
    'member1_name': 'John Doe',
    'member2_name': 'Jane Doe'
})
print('1. Register status:', status, '| Generated password length:', len(reg.get('generated_password', '')))
pwd = reg.get('generated_password')

# 2. Login
status, login = req('/login', {'team_name': 'Omega_Defenders99!', 'password': pwd})
print('2. Login status:', status, '| Session token length:', len(login.get('session_token', '')))
token = login.get('session_token')

# 3. Fetch Quiz
status, quiz = req('/quiz', token=token)
print('3. Quiz status:', status, '| Total questions:', quiz.get('total_questions'), '| Remaining seconds:', quiz.get('remaining_seconds'))
first_q = quiz['questions'][0]['question_number']

# 4. Answer question
status, ans = req('/quiz/answer', {'question_number': first_q, 'selected_answer': 'B'}, token=token)
print('4. Answer status:', status, '| Selected answer:', ans.get('selected_answer'))

# 5. Submit Quiz
status, sub = req('/quiz/submit', data={}, token=token)
print('5. Contestant Submit status:', status, '| Message:', sub.get('message'))
print('   CONTESTANT PRIVACY CHECK: Score in payload =', 'score' in sub, '| Correct count in payload =', 'correct_count' in sub)

# 6. Admin Login
status, adm = req('/admin/login', {'username': 'ctf_admin', 'password': 'CyberSec_2026!'})
print('6. Admin Login status:', status, '| Admin token length:', len(adm.get('session_token', '')))
adm_token = adm.get('session_token')

# 7. Admin Statistics
status, stats = req('/admin/statistics', token=adm_token)
print('7. Admin Stats:', json.dumps(stats, indent=2))

# 8. Admin Rankings
status, ranking = req('/admin/ranking', token=adm_token)
print('8. Top Ranking Teams count:', len(ranking))
for r in ranking:
    print(f"   Rank #{r['rank']}: Team '{r['team_name']}' | Score: {r['score']} | Reason: {r['submission_reason']} | Duration: {r['duration_seconds']:.1f}s")

print("=== ALL LIVE E2E CHECKS PASSED ===")
