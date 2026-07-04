import os
import sqlite3
from datetime import datetime

DB_NAME = os.getenv("DATABASE_PATH", "messages.db")

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def add_message(user_name: str, content: str, timestamp: datetime):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Guardamos la fecha en formato string ISO para simplificar
    cursor.execute(
        "INSERT INTO messages (user_name, content, timestamp) VALUES (?, ?, ?)",
        (user_name, content, timestamp.strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()

def get_all_messages():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_name, content, timestamp FROM messages ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return rows

def clear_all_messages():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM messages")
    conn.commit()
    conn.close()

def get_users_with_messages():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT user_name FROM messages ORDER BY user_name ASC")
    rows = [row[0] for row in cursor.fetchall()]
    conn.close()
    return rows

