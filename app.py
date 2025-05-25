from flask import Flask, render_template, request, redirect, url_for, session, Response
from waitress import serve
import sqlite3
from datetime import datetime
import os
import csv
import io
import time

app = Flask(__name__)
app.secret_key = 'your_secret_key'
DB_NAME = 'status_app.db'

def get_connection():
    return sqlite3.connect(DB_NAME, timeout=10, isolation_level=None, check_same_thread=False)

def safe_commit(conn, retries=3, delay=0.5):
    for _ in range(retries):
        try:
            conn.commit()
            return
        except sqlite3.OperationalError as e:
            if 'locked' in str(e):
                time.sleep(delay)
            else:
                raise
    raise RuntimeError("書き込みに失敗しました（ロック継続）")

def init_db():
    conn = get_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 username TEXT UNIQUE NOT NULL,
                 password TEXT NOT NULL,
                 email TEXT UNIQUE NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS statuses (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 username TEXT NOT NULL,
                 status TEXT NOT NULL,
                 start_time TEXT NOT NULL,
                 end_time TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_buttons (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 username TEXT NOT NULL,
                 button_label TEXT NOT NULL)''')
    safe_commit(conn)
    conn.close()

def get_last_status(username):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT status FROM statuses WHERE username=? ORDER BY id DESC LIMIT 1", (username,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else "なし"

def get_user_buttons(username):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT button_label FROM user_buttons WHERE username=?", (username,))
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows] if rows else ["休憩", "移動", "作業", "会議", "勤務終了"]

@app.route('/', methods=['GET', 'POST'])
def index():
    if 'username' not in session:
        return redirect(url_for('login'))

    from markupsafe import Markup
    import pandas as pd
    import plotly.express as px

    username = session['username']
    if request.method == 'POST':
        new_status = request.form['status']
        now = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT id FROM statuses WHERE username=? AND end_time IS NULL ORDER BY id DESC LIMIT 1", (username,))
        row = c.fetchone()
        if row:
            c.execute("UPDATE statuses SET end_time=? WHERE id=?", (now, row[0]))
        if new_status != '勤務終了':
            c.execute("INSERT INTO statuses (username, status, start_time) VALUES (?, ?, ?)", (username, new_status, now))
        safe_commit(conn)
        conn.close()
        return redirect(url_for('index'))

    last_status = get_last_status(username)
    buttons = get_user_buttons(username)
    if '勤務終了' not in buttons:
        buttons.append('勤務終了')

    # ガントチャートの生成
    today = datetime.now().strftime('%Y/%m/%d')
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT status, start_time, end_time FROM statuses
        WHERE username=? AND start_time LIKE ?
        ORDER BY start_time ASC
    """, (username, f'{today}%'))
    rows = c.fetchall()
    conn.close()

    cleaned = [(r[0], r[1], r[2] if r[2] else datetime.now().strftime('%Y/%m/%d %H:%M:%S')) for r in rows if r[1]]
    chart_html = ""
    if cleaned:
        df = pd.DataFrame(cleaned, columns=['Task', 'Start', 'Finish'])
        df['Start'] = pd.to_datetime(df['Start'], format='%Y/%m/%d %H:%M:%S')
        df['Finish'] = pd.to_datetime(df['Finish'], format='%Y/%m/%d %H:%M:%S')
        fig = px.timeline(df, x_start='Start', x_end='Finish', y='Task', color='Task')
        fig.update_yaxes(autorange='reversed')
        chart_html = fig.to_html(full_html=False)

    return render_template('index.html', username=username, last_status=last_status, buttons=buttons, chart=Markup(chart_html))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        email = request.form['email']
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE username=? OR email=?", (username, email))
        existing_user = c.fetchone()
        if existing_user:
            conn.close()
            return "ユーザー名またはメールアドレスは既に使用されています。"
        try:
            c.execute("INSERT INTO users (username, password, email) VALUES (?, ?, ?)", (username, password, email))
            safe_commit(conn)
        except sqlite3.IntegrityError:
            conn.close()
            return "登録時にエラーが発生しました。"
        conn.close()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password))
        user = c.fetchone()
        conn.close()
        if user:
            session['username'] = username
            return redirect(url_for('index'))
        else:
            return "ログイン失敗"
    return render_template('login.html')

@app.route('/logout')
def logout():
    username = session.get('username')
    if username:
        now = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT id FROM statuses WHERE username=? AND end_time IS NULL ORDER BY id DESC LIMIT 1", (username,))
        row = c.fetchone()
        if row:
            c.execute("UPDATE statuses SET end_time=? WHERE id=?", (now, row[0]))
            safe_commit(conn)
        conn.close()
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/customize', methods=['GET', 'POST'])
def customize():
    if 'username' not in session:
        return redirect(url_for('login'))

    username = session['username']
    if request.method == 'POST':
        labels = request.form.getlist('buttons')
        conn = get_connection()
        c = conn.cursor()
        c.execute("DELETE FROM user_buttons WHERE username=?", (username,))
        for label in labels:
            if label.strip():
                c.execute("INSERT INTO user_buttons (username, button_label) VALUES (?, ?)", (username, label.strip()))
        safe_commit(conn)
        conn.close()
        return redirect(url_for('index'))

    current_buttons = get_user_buttons(username)
    return render_template('customize.html', buttons=current_buttons)

@app.route('/download_csv')
def download_csv():
    if 'username' not in session:
        return redirect(url_for('login'))

    username = session['username']
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT status, start_time, end_time FROM statuses WHERE username=?", (username,))
    rows = c.fetchall()
    conn.close()

    formatted_rows = []
    for row in rows:
        status, start, end = row
        try:
            start_fmt = datetime.strptime(start, '%Y/%m/%d %H:%M:%S').strftime('%Y/%m/%d %H:%M:%S') if start else ''
        except:
            start_fmt = start
        try:
            end_fmt = datetime.strptime(end, '%Y/%m/%d %H:%M:%S').strftime('%Y/%m/%d %H:%M:%S') if end else ''
        except:
            end_fmt = end
        formatted_rows.append([status, start_fmt, end_fmt])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['状態', '開始時刻', '終了時刻'])
    writer.writerows(formatted_rows)

    csv_bytes = output.getvalue().encode('utf-8-sig')
    return Response(csv_bytes, mimetype='text/csv', headers={
        'Content-Disposition': f'attachment;filename={username}_statuses.csv'
    })

@app.route('/current_status')
def current_status():
    if 'username' not in session:
        return redirect(url_for('login'))

    status_filter = request.args.get('filter')
    sort_order = request.args.get('sort', 'desc')

    conn = get_connection()
    c = conn.cursor()
    query = "SELECT username, status, start_time FROM statuses WHERE end_time IS NULL"
    params = []
    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)
    query += " ORDER BY start_time {}".format("ASC" if sort_order == "asc" else "DESC")
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()

    # 経過時間を追加
    statuses = []
    for username, status, start_time in rows:
        start_dt = datetime.strptime(start_time, '%Y/%m/%d %H:%M:%S')
        elapsed = datetime.now() - start_dt
        elapsed_str = str(elapsed).split('.')[0]  # hh:mm:ss 形式
        statuses.append((username, status, start_time, elapsed_str))

        unique_statuses = sorted(list(set(row[1] for row in rows)))
    return render_template('current_status.html', statuses=statuses, current_filter=status_filter, sort_order=sort_order, unique_statuses=unique_statuses)

@app.route('/my_chart')
def my_chart():
    if 'username' not in session:
        return redirect(url_for('login'))

    import pandas as pd
    import plotly.express as px
    from markupsafe import Markup

    username = session['username']
    today = datetime.now().strftime('%Y/%m/%d')

    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT status, start_time, end_time FROM statuses
        WHERE username=? AND start_time LIKE ?
        ORDER BY start_time ASC
    """, (username, f'{today}%'))
    rows = c.fetchall()
    conn.close()

    # end_time がないレコードは除外
    cleaned = [(r[0], r[1], r[2] if r[2] else datetime.now().strftime('%Y/%m/%d %H:%M:%S')) for r in rows if r[1]]
    if not cleaned:
        return "本日のガントチャート用データがありません"

    df = pd.DataFrame(cleaned, columns=['Task', 'Start', 'Finish'])
    if df.empty:
        return "本日のデータがありません"

    df['Start'] = pd.to_datetime(df['Start'], format='%Y/%m/%d %H:%M:%S')
    df['Finish'] = pd.to_datetime(df['Finish'], format='%Y/%m/%d %H:%M:%S')

    fig = px.timeline(df, x_start='Start', x_end='Finish', y='Task', color='Task')
    fig.update_yaxes(autorange='reversed')
    plot_html = fig.to_html(full_html=False)

    return render_template('my_chart.html', chart=Markup(plot_html))


if __name__ == '__main__':
    init_db()
    print("Starting app on http://0.0.0.0:5000")
    serve(app, host='0.0.0.0', port=5000, threads=20)
