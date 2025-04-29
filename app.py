import os
from difflib import SequenceMatcher
from datetime import datetime
from flask import Flask, render_template, redirect, url_for, request, session, flash, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
from functools import wraps

# Initialize Flask App
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or 'your-secret-key-here'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ASSIGNMENTS_FOLDER'] = 'assignments'
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5MB limit
app.config['DATABASE'] = 'app.db'

# Ensure upload folders exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['ASSIGNMENTS_FOLDER'], exist_ok=True)

# Database Helper Functions
def get_db():
    db = sqlite3.connect(app.config['DATABASE'])
    db.row_factory = sqlite3.Row
    return db

def init_db():
    with app.app_context():
        db = get_db()
        # Existing tables
        db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS comparisons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                text1 TEXT,
                text2 TEXT,
                score REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        # New tables for assignment system
        db.execute('''
            CREATE TABLE IF NOT EXISTS assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                teacher_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (teacher_id) REFERENCES users (id)
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                assignment_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (assignment_id) REFERENCES assignments (id),
                FOREIGN KEY (student_id) REFERENCES users (id)
            )
        ''')
        db.commit()

# Initialize Database
init_db()

# Template Filter for Date Formatting
@app.template_filter('datetimeformat')
def datetimeformat(value, format='%d.%m.%Y %H:%M'):
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return value
    return value.strftime(format)

# Context Processor
@app.context_processor
def inject_current_year():
    return {'current_year': datetime.now().year}

# Helper Functions
def calculate_similarity(text1, text2):
    return round(SequenceMatcher(None, text1, text2).ratio() * 100, 2)

def extract_text_from_file(file_path):
    if not file_path or not os.path.exists(file_path):
        return ""
    
    try:
        if file_path.endswith('.txt'):
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        elif file_path.endswith('.docx'):
            from docx import Document
            doc = Document(file_path)
            return '\n'.join([para.text for para in doc.paragraphs if para.text])
    except Exception as e:
        app.logger.error(f"Error extracting text from {file_path}: {e}")
        return ""

def save_assignment_file(file):
    if not file or file.filename == '':
        return None
    
    filename = secure_filename(file.filename)
    file_path = os.path.join(app.config['ASSIGNMENTS_FOLDER'], filename)
    file.save(file_path)
    return file_path

# Decorators
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            flash('Lütfen giriş yapınız', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def role_required(role):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'role' not in session or session['role'] != role:
                flash('Bu sayfaya erişim izniniz yok', 'error')
                return redirect(url_for('login'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if not username or not password:
            flash('Lütfen kullanıcı adı ve şifre giriniz', 'error')
            return redirect(url_for('login'))
            
        db = get_db()
        user = db.execute(
            'SELECT * FROM users WHERE username = ?', (username,)
        ).fetchone()
        
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            return redirect(url_for(f"{user['role']}_dashboard"))
        else:
            flash('Geçersiz kullanıcı adı veya şifre', 'error')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        role = request.form.get('role')
        
        if not all([username, password, role]):
            flash('Lütfen tüm alanları doldurunuz', 'error')
            return redirect(url_for('register'))
            
        db = get_db()
        try:
            db.execute(
                'INSERT INTO users (username, password, role) VALUES (?, ?, ?)',
                (username, generate_password_hash(password), role)
            )
            db.commit()
            flash('Kayıt başarılı! Giriş yapabilirsiniz', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Bu kullanıcı adı zaten alınmış', 'error')
    
    return render_template('register.html')

@app.route('/compare', methods=['GET', 'POST'])
@login_required
@role_required('teacher')
def compare():
    score = None
    text1 = request.form.get('text1', '')
    text2 = request.form.get('text2', '')
    
    if request.method == 'POST':
        file1 = request.files.get('file1')
        file2 = request.files.get('file2')

        if file1 and file1.filename:
            filename = secure_filename(file1.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file1.save(file_path)
            text1 = extract_text_from_file(file_path)
            os.remove(file_path)
            
        if file2 and file2.filename:
            filename = secure_filename(file2.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file2.save(file_path)
            text2 = extract_text_from_file(file_path)
            os.remove(file_path)
        
        if text1 and text2:
            score = calculate_similarity(text1, text2)
            
            # Save comparison to database
            db = get_db()
            db.execute(
                'INSERT INTO comparisons (user_id, text1, text2, score) VALUES (?, ?, ?, ?)',
                (session['user_id'], text1, text2, score)
            )
            db.commit()
        else:
            flash('Lütfen iki metin veya dosya giriniz', 'error')
    
    return render_template('compare.html', score=score, text1=text1, text2=text2)

# ====================== TEACHER ROUTES ======================
@app.route('/teacher_dashboard')
@login_required
@role_required('teacher')
def teacher_dashboard():
    db = get_db()
    
    # Get assignments created by this teacher
    assignments = db.execute(
        'SELECT * FROM assignments WHERE teacher_id = ? ORDER BY created_at DESC',
        (session['user_id'],)
    ).fetchall()
    
    # Get recent submissions (last active students)
    recent_submissions = db.execute('''
        SELECT s.*, u.username as student_name, a.name as assignment_name
        FROM submissions s
        JOIN users u ON s.student_id = u.id
        JOIN assignments a ON s.assignment_id = a.id
        ORDER BY s.submitted_at DESC
        LIMIT 10
    ''').fetchall()
    
    # Get all students
    students = db.execute(
        'SELECT id, username FROM users WHERE role = "student" ORDER BY username'
    ).fetchall()
    
    return render_template('teacher_dashboard.html',
                         assignments=assignments,
                         recent_submissions=recent_submissions,
                         students=students)

@app.route('/upload_assignment', methods=['POST'])
@login_required
@role_required('teacher')
def upload_assignment():
    if request.method == 'POST':
        assignment_name = request.form.get('assignment_name')
        assignment_file = request.files.get('assignment_file')
        
        if not assignment_name or not assignment_file:
            flash('Lütfen tüm alanları doldurunuz', 'error')
            return redirect(url_for('teacher_dashboard'))
        
        try:
            file_path = save_assignment_file(assignment_file)
            if not file_path:
                raise ValueError("Dosya yüklenemedi")
            
            db = get_db()
            db.execute(
                'INSERT INTO assignments (teacher_id, name) VALUES (?, ?)',
                (session['user_id'], assignment_name)
            )
            db.commit()
            flash('Ödev başarıyla yüklendi!', 'success')
        except Exception as e:
            app.logger.error(f"Error uploading assignment: {e}")
            flash('Ödev yüklenirken hata oluştu', 'error')
    
    return redirect(url_for('teacher_dashboard'))

@app.route('/compare_assignments', methods=['POST'])
@login_required
@role_required('teacher')
def compare_assignments():
    assignment1_id = request.form.get('assignment1')
    assignment2_id = request.form.get('assignment2')
    student1_id = request.form.get('student1', '')
    student2_id = request.form.get('student2', '')
    
    if not assignment1_id or not assignment2_id:
        flash('Lütfen en az iki ödev seçiniz', 'error')
        return redirect(url_for('teacher_dashboard'))
    
    db = get_db()
    
    try:
        # Build query for first submission
        query1 = 'SELECT file_path FROM submissions WHERE assignment_id = ?'
        params1 = [assignment1_id]
        
        if student1_id:
            query1 += ' AND student_id = ?'
            params1.append(student1_id)
        
        query1 += ' LIMIT 1'
        
        # Build query for second submission
        query2 = 'SELECT file_path FROM submissions WHERE assignment_id = ?'
        params2 = [assignment2_id]
        
        if student2_id:
            query2 += ' AND student_id = ?'
            params2.append(student2_id)
        
        query2 += ' LIMIT 1'
        
        # Execute queries
        submission1 = db.execute(query1, tuple(params1)).fetchone()
        submission2 = db.execute(query2, tuple(params2)).fetchone()
        
        if not submission1 or not submission2:
            flash('Seçilen ödevlerde yeterli gönderim yok', 'error')
            return redirect(url_for('teacher_dashboard'))
        
        # Extract texts
        text1 = extract_text_from_file(submission1['file_path'])
        text2 = extract_text_from_file(submission2['file_path'])
        
        if not text1 or not text2:
            flash('Dosyalardan metin çıkarılamadı', 'error')
            return redirect(url_for('teacher_dashboard'))
        
        # Calculate similarity
        score = calculate_similarity(text1, text2)
        
        # Save comparison
        db.execute(
            'INSERT INTO comparisons (user_id, text1, text2, score) VALUES (?, ?, ?, ?)',
            (session['user_id'], text1[:500], text2[:500], score)
        )
        db.commit()
        
        flash(f'Benzerlik skoru: {score}%', 'success')
        
    except Exception as e:
        app.logger.error(f"Comparison error: {str(e)}")
        flash('Karşılaştırma yapılırken bir hata oluştu', 'error')
    
    return redirect(url_for('teacher_dashboard'))

@app.route('/examine/<int:submission_id>')
@login_required
@role_required('teacher')
def examine_submission(submission_id):
    db = get_db()
    
    # Get submission details with student and assignment info
    submission = db.execute('''
        SELECT s.*, u.username as student_name, a.name as assignment_name
        FROM submissions s
        JOIN users u ON s.student_id = u.id
        JOIN assignments a ON s.assignment_id = a.id
        WHERE s.id = ?
    ''', (submission_id,)).fetchone()
    
    if not submission:
        flash('Gönderim bulunamadı', 'error')
        return redirect(url_for('teacher_dashboard'))
    
    # Extract text from the file
    submission_text = extract_text_from_file(submission['file_path'])
    
    # Get all submissions for the same assignment (excluding current one)
    other_submissions = db.execute('''
        SELECT s2.id, s2.file_path, s2.submitted_at, u.username as student_name
        FROM submissions s2
        JOIN users u ON s2.student_id = u.id
        WHERE s2.assignment_id = ? AND s2.id != ?
    ''', (submission['assignment_id'], submission_id)).fetchall()
    
    # Calculate similarity for each submission
    similar_submissions = []
    current_text = extract_text_from_file(submission['file_path'])
    
    for sub in other_submissions:
        other_text = extract_text_from_file(sub['file_path'])
        similarity = calculate_similarity(current_text, other_text)
        similar_submissions.append({
            'id': sub['id'],
            'file_path': sub['file_path'],
            'student_name': sub['student_name'],
            'submitted_at': sub['submitted_at'],  # Add this line
            'similarity': similarity
        })
    
    # Sort by similarity (highest first) and take top 3
    similar_submissions = sorted(similar_submissions, key=lambda x: x['similarity'], reverse=True)[:3]
    
    return render_template('examine.html', 
                         submission=submission,
                         submission_text=submission_text,
                         similar_submissions=similar_submissions)

@app.route('/download/<int:submission_id>')
@login_required
@role_required('teacher')
def download_submission(submission_id):
    db = get_db()
    submission = db.execute(
        'SELECT * FROM submissions WHERE id = ?', (submission_id,)
    ).fetchone()
    
    if not submission or not os.path.exists(submission['file_path']):
        flash('Dosya bulunamadı', 'error')
        return redirect(url_for('teacher_dashboard'))
    
    return send_file(submission['file_path'], as_attachment=True)

@app.route('/compare_submissions/<int:submission1_id>/<int:submission2_id>')
@login_required
@role_required('teacher')
def compare_submissions(submission1_id, submission2_id):
    db = get_db()
    
    # Get both submissions
    submission1 = db.execute(
        'SELECT s.*, u.username as student_name FROM submissions s JOIN users u ON s.student_id = u.id WHERE s.id = ?', 
        (submission1_id,)
    ).fetchone()
    submission2 = db.execute(
        'SELECT s.*, u.username as student_name FROM submissions s JOIN users u ON s.student_id = u.id WHERE s.id = ?', 
        (submission2_id,)
    ).fetchone()
    
    if not submission1 or not submission2:
        flash('Gönderimler bulunamadı', 'error')
        return redirect(url_for('teacher_dashboard'))
    
    # Extract texts
    text1 = extract_text_from_file(submission1['file_path'])
    text2 = extract_text_from_file(submission2['file_path'])
    
    # Calculate similarity
    score = calculate_similarity(submission1['file_path'], submission2['file_path'])
    
    return render_template('compare_submissions.html',
                         submission1=submission1,
                         submission2=submission2,
                         text1=text1,
                         text2=text2,
                         score=score)

# ====================== STUDENT ROUTES ======================
@app.route('/student_dashboard')
@login_required
@role_required('student')
def student_dashboard():
    db = get_db()
    
    # Get all available assignments
    assignments = db.execute(
        'SELECT * FROM assignments ORDER BY created_at DESC'
    ).fetchall()
    
    # Get student's submissions
    submissions = db.execute(
        '''SELECT s.*, a.name as assignment_name 
           FROM submissions s 
           JOIN assignments a ON s.assignment_id = a.id 
           WHERE student_id = ? 
           ORDER BY submitted_at DESC''',
        (session['user_id'],)
    ).fetchall()
    
    return render_template('student_dashboard.html',
                         assignments=assignments,
                         submissions=submissions)

@app.route('/submit_assignment', methods=['POST'])
@login_required
@role_required('student')
def submit_assignment():
    if request.method == 'POST':
        assignment_id = request.form.get('assignment_id')
        homework_file = request.files.get('homework_file')
        
        if not assignment_id or not homework_file:
            flash('Lütfen tüm alanları doldurunuz', 'error')
            return redirect(url_for('student_dashboard'))
        
        try:
            # Check if assignment exists
            db = get_db()
            assignment = db.execute(
                'SELECT * FROM assignments WHERE id = ?',
                (assignment_id,)
            ).fetchone()
            
            if not assignment:
                flash('Geçersiz ödev IDsi', 'error')
                return redirect(url_for('student_dashboard'))
            
            # Save file
            filename = secure_filename(homework_file.filename)
            file_path = os.path.join(
                app.config['UPLOAD_FOLDER'],
                f"sub_{session['user_id']}_{assignment_id}_{filename}"
            )
            homework_file.save(file_path)
            
            # Save to database
            db.execute(
                '''INSERT INTO submissions 
                   (assignment_id, student_id, file_path) 
                   VALUES (?, ?, ?)''',
                (assignment_id, session['user_id'], file_path)
            )
            db.commit()
            flash('Ödev başarıyla gönderildi!', 'success')
        except Exception as e:
            app.logger.error(f"Error submitting assignment: {str(e)}")
            flash('Ödev gönderilirken hata oluştu', 'error')
    
    return redirect(url_for('student_dashboard'))

# ====================== SHARED ROUTES ======================
@app.route('/history')
@login_required
@role_required('teacher')
def history():
    db = get_db()
    
    if session['role'] == 'teacher':
        comparisons = db.execute('''
            SELECT c.*, u.username as user 
            FROM comparisons c
            JOIN users u ON c.user_id = u.id
            ORDER BY c.created_at DESC
            LIMIT 50
        ''').fetchall()
    else:
        comparisons = db.execute('''
            SELECT * FROM comparisons 
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 50
        ''', (session['user_id'],)).fetchall()
    
    return render_template('history.html', history=comparisons)

@app.route('/clear_history', methods=['POST'])
@login_required
@role_required('teacher')
def clear_history():
    db = get_db()
    db.execute('DELETE FROM comparisons')
    db.commit()
    flash('Karşılaştırma geçmişi temizlendi', 'success')
    return redirect(url_for('history'))

@app.route('/iletisim')
def contact():
    return render_template('contact.html')

@app.route('/hakkinda')
def about():
    return render_template('about.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)