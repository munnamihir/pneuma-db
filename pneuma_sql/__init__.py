"""
pneuma_sql — Drop-in SQLite replacement backed by PNEUMA-DB
============================================================
Quantum-safe, distributed, works offline over ultrasonic air
or globally via WebSocket relay.

Usage — identical to sqlite3:

    import pneuma_sql                   # instead of: import sqlite3

    conn = pneuma_sql.connect(":local:", node_id="my-app")
    cur  = conn.cursor()

    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id    INTEGER PRIMARY KEY,
            name  TEXT    NOT NULL,
            email TEXT    UNIQUE,
            age   INTEGER,
            role  TEXT    DEFAULT 'user'
        )
    ''')

    cur.execute("INSERT INTO users (name, email, age) VALUES (?, ?, ?)",
                ("Alice", "alice@co.com", 30))

    cur.execute("SELECT * FROM users WHERE age > ?", (25,))
    rows = cur.fetchall()

    conn.commit()
    conn.close()
"""

from pneuma_sql.sql_engine import (
    connect,
    Connection,
    Cursor,
    PneumaSQLStore,
    SQLParser,
    RowEvaluator,
    TableSchema,
    Column,
    ColType,
    # Exceptions (DB-API 2.0)
    Error,
    DatabaseError,
    OperationalError,
    IntegrityError,
    ProgrammingError,
    InterfaceError,
    # DB-API 2.0 attributes
    apilevel,
    threadsafety,
    paramstyle,
)

__version__ = "1.0.0"
__all__ = [
    "connect",
    "Connection", "Cursor",
    "Error", "DatabaseError", "OperationalError",
    "IntegrityError", "ProgrammingError", "InterfaceError",
    "apilevel", "threadsafety", "paramstyle",
]
