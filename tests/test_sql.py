"""
PNEUMA SQL Test Suite
=====================
Tests the complete SQL layer without audio hardware.
Uses in-memory SQLite backend for speed.
"""

import sys, os, time, pytest, sqlite3
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pneuma-db"))

from sql_engine import (
    SQLParser, RowEvaluator, PneumaSQLStore, Connection,
    TableSchema, Column, ColType,
    OperationalError, IntegrityError, ProgrammingError,
)


# ── Fixture: in-memory store ───────────────────────────────────
class FakeDB:
    """Simple in-memory dict that mimics PNEUMA-DB's interface."""
    def __init__(self):
        self._data = {}

    def put(self, key, value):
        self._data[key] = value

    def get(self, key):
        return self._data.get(key)

    def delete(self, key):
        self._data.pop(key, None)

    def scan_prefix(self, prefix):
        return {k: v for k, v in self._data.items() if k.startswith(prefix)}

    def next_id(self, table):
        seq_key = f"_seq:{table}"
        current = self._data.get(seq_key, 0)
        self._data[seq_key] = current + 1
        return current + 1


def make_conn() -> Connection:
    db    = FakeDB()
    store = PneumaSQLStore(db, "test-node")
    return Connection(db, "test-node")


# ══════════════════════════════════════════════════════
# SQL PARSER TESTS
# ══════════════════════════════════════════════════════
class TestSQLParser:
    def test_create_table_basic(self):
        sql = "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)"
        schema = SQLParser.parse_create_table(sql)
        assert schema.name == "users"
        assert len(schema.columns) == 3
        id_col = schema.col("id")
        assert id_col.primary_key is True
        assert id_col.type == ColType.INTEGER

    def test_create_table_with_constraints(self):
        sql = """CREATE TABLE products (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            name  TEXT    NOT NULL,
            price REAL    DEFAULT 0.0,
            sku   TEXT    UNIQUE
        )"""
        schema = SQLParser.parse_create_table(sql)
        assert schema.col("id").autoincrement is True
        assert schema.col("name").not_null is True
        assert schema.col("sku").unique is True

    def test_create_table_if_not_exists(self):
        sql = "CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER)"
        schema = SQLParser.parse_create_table(sql)
        assert schema.name == "sessions"

    def test_parse_insert_basic(self):
        sql = "INSERT INTO users (name, age) VALUES ('Alice', 30)"
        p   = SQLParser.parse_insert(sql)
        assert p["table"]   == "users"
        assert p["columns"] == ["name", "age"]
        assert p["values"]  == ["Alice", 30]

    def test_parse_insert_with_params(self):
        sql    = "INSERT INTO users (name, email) VALUES (?, ?)"
        p      = SQLParser.parse_insert(sql, ("Bob", "bob@co.com"))
        assert p["values"] == ["Bob", "bob@co.com"]

    def test_parse_insert_null(self):
        sql = "INSERT INTO users (name, age) VALUES ('Charlie', NULL)"
        p   = SQLParser.parse_insert(sql)
        assert p["values"][1] is None

    def test_parse_select_star(self):
        sql = "SELECT * FROM users"
        p   = SQLParser.parse_select(sql)
        assert p["table"]   == "users"
        assert p["columns"] == ["*"]
        assert p["where"]   is None

    def test_parse_select_cols(self):
        sql = "SELECT name, age FROM users WHERE age > 25"
        p   = SQLParser.parse_select(sql)
        assert p["columns"] == ["name", "age"]
        assert p["where"]["op"] == ">"
        assert p["where"]["col"] == "age"
        assert p["where"]["val"] == 25

    def test_parse_select_order_limit(self):
        sql = "SELECT * FROM users ORDER BY name ASC LIMIT 10 OFFSET 5"
        p   = SQLParser.parse_select(sql)
        assert p["order_by"] == [("name", "ASC")]
        assert p["limit"]    == 10
        assert p["offset"]   == 5

    def test_parse_select_and_where(self):
        sql = "SELECT * FROM users WHERE age > 20 AND role = 'admin'"
        p   = SQLParser.parse_select(sql)
        assert p["where"]["op"] == "AND"
        assert len(p["where"]["conditions"]) == 2

    def test_parse_update(self):
        sql = "UPDATE users SET age = 31, role = 'admin' WHERE name = 'Alice'"
        p   = SQLParser.parse_update(sql)
        assert p["table"]           == "users"
        assert p["updates"]["age"]  == 31
        assert p["updates"]["role"] == "admin"
        assert p["where"]["col"]    == "name"

    def test_parse_update_with_params(self):
        sql = "UPDATE users SET age = ? WHERE id = ?"
        p   = SQLParser.parse_update(sql, (32, 1))
        assert p["updates"]["age"]  == 32
        assert p["where"]["val"]    == 1

    def test_parse_delete(self):
        sql = "DELETE FROM users WHERE id = 5"
        p   = SQLParser.parse_delete(sql)
        assert p["table"]        == "users"
        assert p["where"]["val"] == 5

    def test_parse_delete_all(self):
        sql = "DELETE FROM logs"
        p   = SQLParser.parse_delete(sql)
        assert p["where"] is None


# ══════════════════════════════════════════════════════
# ROW EVALUATOR TESTS
# ══════════════════════════════════════════════════════
class TestRowEvaluator:
    def row(self, **kw): return kw

    def test_eq(self):
        assert RowEvaluator.match(self.row(name="Alice"), {"col":"name","op":"=","val":"Alice"})
        assert not RowEvaluator.match(self.row(name="Bob"), {"col":"name","op":"=","val":"Alice"})

    def test_gt_lt(self):
        assert RowEvaluator.match(self.row(age=30), {"col":"age","op":">","val":25})
        assert not RowEvaluator.match(self.row(age=20), {"col":"age","op":">","val":25})
        assert RowEvaluator.match(self.row(age=20), {"col":"age","op":"<","val":25})

    def test_gte_lte(self):
        assert RowEvaluator.match(self.row(age=25), {"col":"age","op":">=","val":25})
        assert RowEvaluator.match(self.row(age=25), {"col":"age","op":"<=","val":25})

    def test_not_equal(self):
        assert RowEvaluator.match(self.row(role="admin"), {"col":"role","op":"<>","val":"user"})

    def test_is_null(self):
        assert RowEvaluator.match(self.row(age=None), {"col":"age","op":"IS NULL","val":None})
        assert not RowEvaluator.match(self.row(age=30), {"col":"age","op":"IS NULL","val":None})

    def test_is_not_null(self):
        assert RowEvaluator.match(self.row(age=30), {"col":"age","op":"IS NOT NULL","val":None})

    def test_like(self):
        assert RowEvaluator.match(self.row(name="Alice"), {"col":"name","op":"LIKE","val":"Ali%"})
        assert RowEvaluator.match(self.row(email="a@b.com"), {"col":"email","op":"LIKE","val":"%@%.com"})
        assert not RowEvaluator.match(self.row(name="Bob"), {"col":"name","op":"LIKE","val":"Ali%"})

    def test_in(self):
        assert RowEvaluator.match(self.row(role="admin"), {"col":"role","op":"IN","val":["admin","super"]})
        assert not RowEvaluator.match(self.row(role="user"), {"col":"role","op":"IN","val":["admin","super"]})

    def test_and(self):
        cond = {"op":"AND","conditions":[
            {"col":"age","op":">","val":20},
            {"col":"role","op":"=","val":"admin"},
        ]}
        assert RowEvaluator.match(self.row(age=30, role="admin"), cond)
        assert not RowEvaluator.match(self.row(age=30, role="user"), cond)

    def test_or(self):
        cond = {"op":"OR","conditions":[
            {"col":"role","op":"=","val":"admin"},
            {"col":"role","op":"=","val":"superadmin"},
        ]}
        assert RowEvaluator.match(self.row(role="admin"), cond)
        assert RowEvaluator.match(self.row(role="superadmin"), cond)
        assert not RowEvaluator.match(self.row(role="user"), cond)

    def test_no_condition(self):
        assert RowEvaluator.match({"any":"row"}, None)


# ══════════════════════════════════════════════════════
# FULL SQL INTEGRATION TESTS
# ══════════════════════════════════════════════════════
class TestSQLIntegration:
    def setup_method(self):
        self.conn = make_conn()
        self.cur  = self.conn.cursor()
        self.cur.execute("""
            CREATE TABLE users (
                id    INTEGER PRIMARY KEY,
                name  TEXT    NOT NULL,
                email TEXT    UNIQUE,
                age   INTEGER,
                role  TEXT    DEFAULT 'user'
            )
        """)

    def test_create_and_insert(self):
        self.cur.execute("INSERT INTO users (name, email, age) VALUES (?, ?, ?)",
                         ("Alice", "alice@co.com", 30))
        assert self.cur.lastrowid is not None

    def test_select_all(self):
        self.cur.execute("INSERT INTO users (name, email, age) VALUES ('Alice', 'a@b.com', 30)")
        self.cur.execute("INSERT INTO users (name, email, age) VALUES ('Bob',   'b@b.com', 25)")
        self.cur.execute("SELECT * FROM users")
        rows = self.cur.fetchall()
        assert len(rows) == 2

    def test_select_where(self):
        self.cur.execute("INSERT INTO users (name, age) VALUES ('Alice', 30)")
        self.cur.execute("INSERT INTO users (name, age) VALUES ('Bob',   20)")
        self.cur.execute("SELECT name FROM users WHERE age > 25")
        rows = self.cur.fetchall()
        names = [r[0] for r in rows]
        assert "Alice" in names
        assert "Bob" not in names

    def test_select_with_params(self):
        self.cur.execute("INSERT INTO users (name, email) VALUES (?, ?)", ("Alice", "alice@co.com"))
        self.cur.execute("SELECT * FROM users WHERE email = ?", ("alice@co.com",))
        row = self.cur.fetchone()
        assert row is not None
        assert "Alice" in row

    def test_update(self):
        self.cur.execute("INSERT INTO users (name, age) VALUES ('Alice', 30)")
        self.cur.execute("UPDATE users SET age = 31 WHERE name = 'Alice'")
        assert self.cur.rowcount == 1
        self.cur.execute("SELECT age FROM users WHERE name = 'Alice'")
        row = self.cur.fetchone()
        assert row[0] == 31

    def test_delete(self):
        self.cur.execute("INSERT INTO users (name, age) VALUES ('Alice', 30)")
        self.cur.execute("INSERT INTO users (name, age) VALUES ('Bob', 25)")
        self.cur.execute("DELETE FROM users WHERE name = 'Alice'")
        assert self.cur.rowcount == 1
        self.cur.execute("SELECT * FROM users")
        rows = self.cur.fetchall()
        assert len(rows) == 1

    def test_delete_all(self):
        self.cur.execute("INSERT INTO users (name) VALUES ('A')")
        self.cur.execute("INSERT INTO users (name) VALUES ('B')")
        self.cur.execute("DELETE FROM users")
        self.cur.execute("SELECT * FROM users")
        assert len(self.cur.fetchall()) == 0

    def test_order_by(self):
        for name, age in [("Charlie", 35), ("Alice", 28), ("Bob", 31)]:
            self.cur.execute("INSERT INTO users (name, age) VALUES (?, ?)", (name, age))
        self.cur.execute("SELECT name FROM users ORDER BY age ASC")
        names = [r[0] for r in self.cur.fetchall()]
        assert names[0] == "Alice"
        assert names[-1] == "Charlie"

    def test_order_by_desc(self):
        for name, age in [("Alice", 28), ("Bob", 35), ("Charlie", 31)]:
            self.cur.execute("INSERT INTO users (name, age) VALUES (?, ?)", (name, age))
        self.cur.execute("SELECT name FROM users ORDER BY age DESC")
        names = [r[0] for r in self.cur.fetchall()]
        assert names[0] == "Bob"

    def test_limit_offset(self):
        for i in range(10):
            self.cur.execute("INSERT INTO users (name, age) VALUES (?, ?)", (f"user{i}", i))
        self.cur.execute("SELECT name FROM users LIMIT 3")
        assert len(self.cur.fetchall()) == 3
        self.cur.execute("SELECT name FROM users LIMIT 3 OFFSET 5")
        assert len(self.cur.fetchall()) == 3

    def test_autoincrement(self):
        self.cur.execute("INSERT INTO users (name) VALUES ('A')")
        id1 = self.cur.lastrowid
        self.cur.execute("INSERT INTO users (name) VALUES ('B')")
        id2 = self.cur.lastrowid
        assert int(id2) > int(id1)

    def test_unique_constraint(self):
        self.cur.execute("INSERT INTO users (name, email) VALUES ('Alice', 'a@b.com')")
        with pytest.raises(IntegrityError):
            self.cur.execute("INSERT INTO users (name, email) VALUES ('Bob', 'a@b.com')")

    def test_not_null_constraint(self):
        with pytest.raises(IntegrityError):
            self.cur.execute("INSERT INTO users (email) VALUES ('x@y.com')")

    def test_default_value(self):
        self.cur.execute("INSERT INTO users (name) VALUES ('Alice')")
        self.cur.execute("SELECT role FROM users WHERE name = 'Alice'")
        row = self.cur.fetchone()
        assert row[0] == "user"

    def test_null_value(self):
        self.cur.execute("INSERT INTO users (name, age) VALUES ('Alice', NULL)")
        self.cur.execute("SELECT age FROM users WHERE name = 'Alice'")
        row = self.cur.fetchone()
        assert row[0] is None

    def test_fetchmany(self):
        for i in range(5):
            self.cur.execute("INSERT INTO users (name) VALUES (?)", (f"user{i}",))
        self.cur.execute("SELECT * FROM users")
        batch = self.cur.fetchmany(2)
        assert len(batch) == 2

    def test_description(self):
        self.cur.execute("SELECT name, age FROM users")
        desc = self.cur.description
        assert desc is not None
        col_names = [d[0] for d in desc]
        assert "name" in col_names
        assert "age"  in col_names

    def test_select_cols_projection(self):
        self.cur.execute("INSERT INTO users (name, age, role) VALUES ('Alice', 30, 'admin')")
        self.cur.execute("SELECT name, role FROM users")
        row = self.cur.fetchone()
        assert len(row) == 2
        assert row[0] == "Alice"
        assert row[1] == "admin"

    def test_context_manager(self):
        with self.conn:
            self.conn.execute("INSERT INTO users (name) VALUES ('Test')")
        self.cur.execute("SELECT * FROM users WHERE name = 'Test'")
        assert self.cur.fetchone() is not None

    def test_drop_table(self):
        self.cur.execute("DROP TABLE users")
        with pytest.raises(OperationalError):
            self.cur.execute("SELECT * FROM users")

    def test_drop_table_if_exists(self):
        self.cur.execute("DROP TABLE IF EXISTS nonexistent")   # Should not raise

    def test_list_tables(self):
        tables = self.conn.tables()
        assert "users" in tables

    def test_and_where(self):
        self.cur.execute("INSERT INTO users (name, age, role) VALUES ('Alice', 30, 'admin')")
        self.cur.execute("INSERT INTO users (name, age, role) VALUES ('Bob',   25, 'user')")
        self.cur.execute("SELECT * FROM users WHERE age > 20 AND role = 'admin'")
        rows = self.cur.fetchall()
        assert len(rows) == 1

    def test_like_where(self):
        self.cur.execute("INSERT INTO users (name) VALUES ('Alice')")
        self.cur.execute("INSERT INTO users (name) VALUES ('Alfred')")
        self.cur.execute("INSERT INTO users (name) VALUES ('Bob')")
        self.cur.execute("SELECT name FROM users WHERE name LIKE 'Al%'")
        names = [r[0] for r in self.cur.fetchall()]
        assert "Alice" in names
        assert "Alfred" in names
        assert "Bob" not in names

    def test_is_null_where(self):
        self.cur.execute("INSERT INTO users (name, age) VALUES ('Alice', NULL)")
        self.cur.execute("INSERT INTO users (name, age) VALUES ('Bob',   30)")
        self.cur.execute("SELECT name FROM users WHERE age IS NULL")
        names = [r[0] for r in self.cur.fetchall()]
        assert "Alice" in names
        assert "Bob" not in names

    def test_in_where(self):
        for name in ["Alice", "Bob", "Charlie"]:
            self.cur.execute("INSERT INTO users (name) VALUES (?)", (name,))
        self.cur.execute("SELECT name FROM users WHERE name IN ('Alice', 'Charlie')")
        names = [r[0] for r in self.cur.fetchall()]
        assert "Alice"   in names
        assert "Charlie" in names
        assert "Bob" not in names

    def test_executemany(self):
        data = [("Alice", 30), ("Bob", 25), ("Charlie", 35)]
        self.cur.executemany("INSERT INTO users (name, age) VALUES (?, ?)", data)
        self.cur.execute("SELECT * FROM users")
        assert len(self.cur.fetchall()) == 3

    def test_multi_table(self):
        self.cur.execute("""
            CREATE TABLE posts (
                id      INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title   TEXT    NOT NULL
            )
        """)
        self.cur.execute("INSERT INTO users (name) VALUES ('Alice')")
        uid = self.cur.lastrowid
        self.cur.execute("INSERT INTO posts (user_id, title) VALUES (?, ?)", (uid, "Hello World"))
        self.cur.execute("SELECT * FROM posts")
        posts = self.cur.fetchall()
        assert len(posts) == 1

    def test_sqlite3_compat(self):
        """Verify our API matches sqlite3's API signature."""
        import sqlite3 as sq3
        # Create same table in sqlite3
        sq3_conn = sq3.connect(":memory:")
        sq3_cur  = sq3_conn.cursor()
        sq3_cur.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
        sq3_cur.execute("INSERT INTO t (val) VALUES (?)", ("hello",))
        sq3_cur.execute("SELECT * FROM t")
        sq3_rows = sq3_cur.fetchall()

        # Same operations in PNEUMA SQL
        pn_cur = self.conn.cursor()
        pn_cur.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
        pn_cur.execute("INSERT INTO t (val) VALUES (?)", ("hello",))
        pn_cur.execute("SELECT * FROM t")
        pn_rows = pn_cur.fetchall()

        # Results should have same shape
        assert len(pn_rows) == len(sq3_rows)
        assert len(pn_rows[0]) == len(sq3_rows[0])


# ══════════════════════════════════════════════════════
# SCHEMA TESTS
# ══════════════════════════════════════════════════════
class TestSchema:
    def setup_method(self):
        self.conn = make_conn()
        self.cur  = self.conn.cursor()

    def test_create_index(self):
        self.cur.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, category TEXT)")
        self.cur.execute("CREATE INDEX idx_items_category ON items (category)")
        schema = self.conn.schema("items")
        assert "idx_items_category" in schema.indexes

    def test_table_exists(self):
        self.cur.execute("CREATE TABLE t1 (id INTEGER PRIMARY KEY)")
        tables = self.conn.tables()
        assert "t1" in tables

    def test_pragma_table_info(self):
        self.cur.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        self.cur.execute("PRAGMA table_info(t)")
        rows = self.cur.fetchall()
        assert len(rows) == 2
        col_names = [r[1] for r in rows]
        assert "id"   in col_names
        assert "name" in col_names

    def test_row_count(self):
        self.cur.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
        for i in range(5):
            self.cur.execute("INSERT INTO t (val) VALUES (?)", (f"v{i}",))
        assert self.conn.row_count("t") == 5

    def test_type_coercion(self):
        self.cur.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, score REAL, active BOOLEAN)")
        self.cur.execute("INSERT INTO t (score, active) VALUES (?, ?)", ("3.14", "true"))
        self.cur.execute("SELECT score, active FROM t")
        row = self.cur.fetchone()
        assert isinstance(row[0], float)
        assert row[1] == True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
