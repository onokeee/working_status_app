import sqlite3

def ensure_email_column():
    conn = sqlite3.connect('status_app.db')
    c = conn.cursor()
    
    # カラム一覧を取得
    c.execute("PRAGMA table_info(users)")
    columns = [info[1] for info in c.fetchall()]
    
    # emailカラムが存在しなければ追加
    if 'email' not in columns:
        c.execute("ALTER TABLE users ADD COLUMN email TEXT")
        conn.commit()
        print("email カラムを追加しました。")
    else:
        print("email カラムは既に存在します。")

    conn.close()

ensure_email_column()