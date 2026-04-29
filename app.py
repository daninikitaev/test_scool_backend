import os
import json
import sqlite3
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, g
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder='static')
ADMIN_SECRET = os.getenv('ADMIN_SECRET')
if not ADMIN_SECRET:
    raise ValueError('ADMIN_SECRET not set')
DATABASE = 'database.db'

# ─── Database ───────────────────────────────────────────────────────────────

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.executescript("""
            CREATE TABLE IF NOT EXISTS tests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                option1 TEXT NOT NULL,
                option2 TEXT NOT NULL,
                option3 TEXT NOT NULL,
                option4 TEXT NOT NULL,
                correct_option INTEGER NOT NULL CHECK(correct_option BETWEEN 1 AND 4),
                FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id INTEGER NOT NULL,
                user_id TEXT,
                username TEXT,
                score INTEGER DEFAULT 0,
                correct_count INTEGER DEFAULT 0,
                incorrect_count INTEGER DEFAULT 0,
                skipped_count INTEGER DEFAULT 0,
                answers TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE
            );
        """)
        db.commit()

# ─── Helpers ─────────────────────────────────────────────────────────────────

def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        admin_code = request.headers.get('X-Admin-Code')
        if admin_code != ADMIN_SECRET:
            return jsonify({'error': 'Forbidden: Invalid admin code'}), 403
        return f(*args, **kwargs)
    return decorated

def query_db(query, args=(), one=False):
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv

def execute_db(query, args=()):
    db = get_db()
    cur = db.execute(query, args)
    db.commit()
    return cur.lastrowid

# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('static', path)

@app.route('/api/health')
def health():
    return jsonify({'status': 'ok'})

@app.route('/api/admin/verify', methods=['POST'])
def verify_admin():
    data = request.get_json() or {}
    return jsonify({'valid': data.get('code') == ADMIN_SECRET})

@app.route('/api/tests', methods=['POST'])
@require_admin
def create_test():
    data = request.get_json() or {}
    code = data.get('code', '').strip()
    title = data.get('title', '').strip()
    description = data.get('description', '').strip()
    questions = data.get('questions', [])

    if not code or not title or not questions:
        return jsonify({'error': 'Code, title and at least one question required'}), 400

    for q in questions:
        if not q.get('text') or not q.get('option1') or not q.get('option2') or not q.get('option3') or not q.get('option4'):
            return jsonify({'error': 'All question fields required'}), 400
        co = q.get('correctOption', 0)
        if co < 1 or co > 4:
            return jsonify({'error': 'Correct option must be 1-4'}), 400

    test_id = execute_db(
        'INSERT INTO tests (code, title, description) VALUES (?, ?, ?)',
        (code, title, description)
    )

    for q in questions:
        execute_db(
            'INSERT INTO questions (test_id, text, option1, option2, option3, option4, correct_option) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (test_id, q['text'], q['option1'], q['option2'], q['option3'], q['option4'], q['correctOption'])
        )

    return jsonify({'success': True, 'testId': test_id})

@app.route('/api/tests', methods=['GET'])
@require_admin
def list_tests():
    tests = query_db('SELECT * FROM tests ORDER BY created_at DESC')
    return jsonify([dict(t) for t in tests])

@app.route('/api/tests/<code>')
def get_test(code):
    test = query_db('SELECT * FROM tests WHERE code = ?', (code,), one=True)
    if not test:
        return jsonify({'error': 'Test not found'}), 404

    questions = query_db(
        'SELECT id, text, option1, option2, option3, option4 FROM questions WHERE test_id = ?',
        (test['id'],)
    )

    result = dict(test)
    result['questions'] = [dict(q) for q in questions]
    return jsonify(result)

@app.route('/api/tests/<code>/attempts', methods=['POST'])
def submit_attempt(code):
    data = request.get_json() or {}
    user_id = data.get('userId', 'anonymous')
    username = data.get('username', 'Anonymous')
    answers = data.get('answers', {})

    test = query_db('SELECT id FROM tests WHERE code = ?', (code,), one=True)
    if not test:
        return jsonify({'error': 'Test not found'}), 404

    questions = query_db('SELECT * FROM questions WHERE test_id = ?', (test['id'],))

    correct = incorrect = skipped = 0
    detailed = []

    for q in questions:
        qid = str(q['id'])
        user_ans = answers.get(qid)
        correct_opt = q['correct_option']

        if user_ans is None:
            skipped += 1
            detailed.append({'questionId': q['id'], 'correct': False, 'skipped': True, 'correctAnswer': correct_opt})
        elif int(user_ans) == correct_opt:
            correct += 1
            detailed.append({'questionId': q['id'], 'correct': True, 'skipped': False, 'correctAnswer': correct_opt})
        else:
            incorrect += 1
            detailed.append({'questionId': q['id'], 'correct': False, 'skipped': False, 'userAnswer': int(user_ans), 'correctAnswer': correct_opt})

    total = len(questions)
    score = round((correct / total) * 100) if total > 0 else 0

    attempt_id = execute_db(
        'INSERT INTO attempts (test_id, user_id, username, score, correct_count, incorrect_count, skipped_count, answers) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (test['id'], user_id, username, score, correct, incorrect, skipped, json.dumps(detailed))
    )

    return jsonify({
        'attemptId': attempt_id,
        'score': score,
        'correctCount': correct,
        'incorrectCount': incorrect,
        'skippedCount': skipped,
        'totalQuestions': total,
        'answers': detailed
    })

@app.route('/api/tests/<code>/stats')
@require_admin
def test_stats(code):
    test = query_db('SELECT id FROM tests WHERE code = ?', (code,), one=True)
    if not test:
        return jsonify({'error': 'Test not found'}), 404

    attempts = query_db('SELECT * FROM attempts WHERE test_id = ?', (test['id'],))
    questions = query_db('SELECT * FROM questions WHERE test_id = ?', (test['id'],))

    total_attempts = len(attempts)
    avg_score = round(sum(a['score'] for a in attempts) / total_attempts, 2) if total_attempts else 0

    question_stats = []
    for q in questions:
        c = i = s = 0
        for a in attempts:
            ans_list = json.loads(a['answers'])
            ans = next((x for x in ans_list if x['questionId'] == q['id']), None)
            if ans:
                if ans.get('skipped'): s += 1
                elif ans.get('correct'): c += 1
                else: i += 1
        question_stats.append({
            'id': q['id'], 'text': q['text'],
            'option1': q['option1'], 'option2': q['option2'],
            'option3': q['option3'], 'option4': q['option4'],
            'correctOption': q['correct_option'],
            'correctCount': c, 'incorrectCount': i, 'skippedCount': s
        })

    return jsonify({'totalAttempts': total_attempts, 'avgScore': avg_score, 'questionStats': question_stats})

@app.route('/api/tests/<code>/report')
@require_admin
def test_report(code):
    test = query_db('SELECT * FROM tests WHERE code = ?', (code,), one=True)
    if not test:
        return jsonify({'error': 'Test not found'}), 404

    attempts = query_db('SELECT * FROM attempts WHERE test_id = ? ORDER BY created_at DESC', (test['id'],))
    questions = query_db('SELECT * FROM questions WHERE test_id = ?', (test['id'],))

    return jsonify({
        'test': dict(test),
        'attempts': [dict(a) for a in attempts],
        'questions': [dict(q) for q in questions]
    })

@app.route('/api/tests/<code>', methods=['DELETE'])
@require_admin
def delete_test(code):
    execute_db('DELETE FROM tests WHERE code = ?', (code,))
    return jsonify({'success': True})

# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    init_db()
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
