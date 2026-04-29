import os
import json
import sqlite3
import logging
from contextlib import contextmanager
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, g
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder='static')
logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO').upper(),
    format='%(asctime)s %(levelname)s %(name)s %(message)s',
)
logger = logging.getLogger(__name__)

ADMIN_SECRET = os.getenv('ADMIN_SECRET')
if not ADMIN_SECRET:
    raise ValueError('ADMIN_SECRET not set')
RAILWAY_VOLUME_PATH = os.getenv('RAILWAY_VOLUME_MOUNT_PATH')
default_db_path = os.path.join(RAILWAY_VOLUME_PATH, 'database.db') if RAILWAY_VOLUME_PATH else os.path.join(os.getcwd(), 'database.db')
DATABASE = os.getenv('DATABASE_PATH', default_db_path)
MAX_QUESTIONS_PER_TEST = int(os.getenv('MAX_QUESTIONS_PER_TEST', '200'))
MAX_REQUEST_SIZE_BYTES = int(os.getenv('MAX_REQUEST_SIZE_BYTES', str(2 * 1024 * 1024)))
app.config['MAX_CONTENT_LENGTH'] = MAX_REQUEST_SIZE_BYTES

# ─── Database ───────────────────────────────────────────────────────────────

def _ensure_database_parent_exists():
    parent = os.path.dirname(os.path.abspath(DATABASE))
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        _ensure_database_parent_exists()
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
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
            CREATE INDEX IF NOT EXISTS idx_questions_test_id ON questions(test_id);
            CREATE INDEX IF NOT EXISTS idx_attempts_test_id ON attempts(test_id);
            CREATE INDEX IF NOT EXISTS idx_attempts_created_at ON attempts(created_at);
        """)
        db.commit()
        logger.info('Database initialized at %s', DATABASE)

# ─── Helpers ─────────────────────────────────────────────────────────────────

@contextmanager
def db_transaction():
    db = get_db()
    try:
        db.execute('BEGIN')
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise

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

def parse_json_body():
    if not request.is_json:
        return None, (jsonify({'error': 'Request body must be JSON'}), 400)
    data = request.get_json(silent=True)
    if data is None:
        return None, (jsonify({'error': 'Invalid JSON body'}), 400)
    return data, None

def normalize_text(value, field_name, max_len=255, required=True):
    if value is None:
        value = ''
    cleaned = str(value).strip()
    if required and not cleaned:
        return None, f'{field_name} is required'
    if len(cleaned) > max_len:
        return None, f'{field_name} is too long (max {max_len})'
    return cleaned, None

def validate_questions(questions):
    if not isinstance(questions, list) or not questions:
        return None, 'At least one question is required'
    if len(questions) > MAX_QUESTIONS_PER_TEST:
        return None, f'Too many questions (max {MAX_QUESTIONS_PER_TEST})'

    validated = []
    for idx, q in enumerate(questions, start=1):
        if not isinstance(q, dict):
            return None, f'Question {idx} must be an object'

        text, err = normalize_text(q.get('text'), f'Question {idx} text', max_len=1000)
        if err:
            return None, err

        opts = []
        for opt_no in range(1, 5):
            opt, opt_err = normalize_text(q.get(f'option{opt_no}'), f'Question {idx} option{opt_no}', max_len=500)
            if opt_err:
                return None, opt_err
            opts.append(opt)

        try:
            correct = int(q.get('correctOption'))
        except (TypeError, ValueError):
            return None, f'Question {idx} correctOption must be an integer between 1 and 4'
        if correct < 1 or correct > 4:
            return None, f'Question {idx} correctOption must be 1-4'

        validated.append({
            'text': text,
            'option1': opts[0],
            'option2': opts[1],
            'option3': opts[2],
            'option4': opts[3],
            'correctOption': correct,
        })
    return validated, None

# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('static', path)

@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'service': 'telegram-test-miniapp', 'database': DATABASE})

@app.route('/api/admin/verify', methods=['POST'])
def verify_admin():
    data, error = parse_json_body()
    if error:
        return error
    return jsonify({'valid': data.get('code') == ADMIN_SECRET})

@app.route('/api/tests', methods=['POST'])
@require_admin
def create_test():
    data, error = parse_json_body()
    if error:
        return error

    code, code_err = normalize_text(data.get('code'), 'code', max_len=50)
    if code_err:
        return jsonify({'error': code_err}), 400
    title, title_err = normalize_text(data.get('title'), 'title', max_len=200)
    if title_err:
        return jsonify({'error': title_err}), 400
    description, desc_err = normalize_text(data.get('description', ''), 'description', max_len=2000, required=False)
    if desc_err:
        return jsonify({'error': desc_err}), 400
    questions = data.get('questions', [])
    validated_questions, q_err = validate_questions(questions)
    if q_err:
        return jsonify({'error': q_err}), 400

    try:
        with db_transaction() as db:
            cursor = db.execute(
                'INSERT INTO tests (code, title, description) VALUES (?, ?, ?)',
                (code, title, description)
            )
            test_id = cursor.lastrowid
            db.executemany(
                'INSERT INTO questions (test_id, text, option1, option2, option3, option4, correct_option) VALUES (?, ?, ?, ?, ?, ?, ?)',
                [
                    (test_id, q['text'], q['option1'], q['option2'], q['option3'], q['option4'], q['correctOption'])
                    for q in validated_questions
                ]
            )
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Test code already exists'}), 409

    return jsonify({'success': True, 'testId': test_id}), 201

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
    data, error = parse_json_body()
    if error:
        return error

    user_id, _ = normalize_text(data.get('userId', 'anonymous'), 'userId', max_len=64, required=False)
    username, _ = normalize_text(data.get('username', 'Anonymous'), 'username', max_len=100, required=False)
    answers = data.get('answers', {})
    if not isinstance(answers, dict):
        return jsonify({'error': 'answers must be an object keyed by question id'}), 400

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
        elif str(user_ans).isdigit() and int(user_ans) == correct_opt:
            correct += 1
            detailed.append({'questionId': q['id'], 'correct': True, 'skipped': False, 'correctAnswer': correct_opt})
        else:
            incorrect += 1
            user_answer = int(user_ans) if str(user_ans).isdigit() else None
            detailed.append({'questionId': q['id'], 'correct': False, 'skipped': False, 'userAnswer': user_answer, 'correctAnswer': correct_opt})

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
    deleted = get_db().execute('DELETE FROM tests WHERE code = ?', (code,)).rowcount
    get_db().commit()
    if deleted == 0:
        return jsonify({'error': 'Test not found'}), 404
    return jsonify({'success': True})

@app.errorhandler(413)
def payload_too_large(_):
    return jsonify({'error': 'Request too large'}), 413

@app.errorhandler(Exception)
def handle_unexpected_error(error):
    logger.exception('Unhandled server error: %s', error)
    return jsonify({'error': 'Internal server error'}), 500

# ─── Main ────────────────────────────────────────────────────────────────────

init_db()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
