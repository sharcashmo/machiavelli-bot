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
    
    # Migración: añadir columnas si no existen
    cursor.execute("PRAGMA table_info(messages)")
    columns = [row[1] for row in cursor.fetchall()]
    if "file_name" not in columns:
        cursor.execute("ALTER TABLE messages ADD COLUMN file_name TEXT")
    if "file_data" not in columns:
        cursor.execute("ALTER TABLE messages ADD COLUMN file_data BLOB")
        
    conn.commit()
    conn.close()

def add_message(user_name: str, content: str, timestamp: datetime, file_name: str = None, file_data: bytes = None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Guardamos la fecha en formato string ISO para simplificar
    cursor.execute(
        "INSERT INTO messages (user_name, content, timestamp, file_name, file_data) VALUES (?, ?, ?, ?, ?)",
        (user_name, content, timestamp.strftime("%Y-%m-%d %H:%M:%S"), file_name, file_data)
    )
    conn.commit()
    conn.close()

def get_all_messages():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, user_name, content, timestamp, file_name FROM messages ORDER BY id ASC")
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
    cursor.execute("SELECT DISTINCT user_name, timestamp FROM messages ORDER BY user_name ASC")
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_file(message_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT file_name, file_data FROM messages WHERE id = ?", (message_id,))
    row = cursor.fetchone()
    conn.close()
    return row


