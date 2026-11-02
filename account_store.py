"""Local account provisioning and schema migrations, without starting the app."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import re
import sqlite3
import uuid

import bcrypt


def ensure_schema(con: sqlite3.Connection) -> None:
    """Create/migrate tables; never replay or change queued chat work."""
    con.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,password TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS chats(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),title TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL,error TEXT,mode TEXT NOT NULL DEFAULT 'chat',request_state TEXT NOT NULL DEFAULT 'idle',request_message_id TEXT);
        CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,chat_id TEXT NOT NULL REFERENCES chats(id),role TEXT NOT NULL,content TEXT NOT NULL,attachments TEXT NOT NULL DEFAULT '[]',created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),name TEXT NOT NULL,mime TEXT NOT NULL,kind TEXT NOT NULL,extracted TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    """)
    columns = {row[1] for row in con.execute("PRAGMA table_info(chats)")}
    for name, definition in (
        ("mode", "TEXT NOT NULL DEFAULT 'chat'"),
        ("request_state", "TEXT NOT NULL DEFAULT 'idle'"),
        ("request_message_id", "TEXT"),
    ):
        if name not in columns:
            con.execute(f"ALTER TABLE chats ADD COLUMN {name} {definition}")


def normalize_username(value: str) -> str:
    username = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", username):
        raise ValueError("Username must contain 3-64 ASCII letters, digits, dots, underscores or hyphens, starting with a letter or digit.")
    return username


def password_bytes(value: str) -> bytes:
    encoded = value.encode("utf-8")
    if len(value) < 12 or len(encoded) > 72:
        raise ValueError("Password must contain at least 12 characters and at most 72 UTF-8 bytes.")
    return encoded


def create_user(database: Path, username: str, password: str) -> str:
    username = normalize_username(username)
    password_hash = bcrypt.hashpw(password_bytes(password), bcrypt.gensalt()).decode("ascii")
    with closing(sqlite3.connect(database, timeout=10)) as con, con:
        con.execute("PRAGMA foreign_keys=ON")
        ensure_schema(con)
        con.execute("BEGIN IMMEDIATE")
        if con.execute("SELECT 1 FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone():
            raise ValueError("That username already exists. Use reset-password to change its password.")
        con.execute("INSERT INTO users(id,username,password) VALUES(?,?,?)", (str(uuid.uuid4()), username, password_hash))
    return username


def reset_password(database: Path, username: str, password: str) -> str:
    username = normalize_username(username)
    password_hash = bcrypt.hashpw(password_bytes(password), bcrypt.gensalt()).decode("ascii")
    with closing(sqlite3.connect(database, timeout=10)) as con, con:
        con.execute("PRAGMA foreign_keys=ON")
        ensure_schema(con)
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT id,username FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
        if row is None:
            raise ValueError("That username does not exist.")
        con.execute("UPDATE users SET password=? WHERE id=?", (password_hash, row[0]))
        con.execute("DELETE FROM sessions WHERE user_id=?", (row[0],))
    return row[1]


def list_users(database: Path) -> list[str]:
    if not database.is_file():
        return []
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone():
            return []
        return [row[0] for row in con.execute("SELECT username FROM users ORDER BY username")]
