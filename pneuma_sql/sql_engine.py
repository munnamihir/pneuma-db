"""
PNEUMA SQL Engine
=================
A complete SQL layer on top of PNEUMA-DB's key-value store.
Implements Python DB-API 2.0 (PEP 249) — drop-in replacement for sqlite3.

Supported SQL:
  CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)
  INSERT INTO users (name, age) VALUES ('Alice', 30)
  SELECT * FROM users WHERE age > 25
  SELECT name, age FROM users WHERE role = 'admin' ORDER BY name LIMIT 10
  UPDATE users SET age = 31 WHERE name = 'Alice'
  DELETE FROM users WHERE id = 1
  DROP TABLE users
  CREATE INDEX idx_users_email ON users (email)

Usage (identical to sqlite3):
    import pneuma_sql as sql

    conn = sql.connect("ws://relay.pneuma.io:8765", node_id="my-app")
    cur  = conn.cursor()
    cur.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE)")
    cur.execute("INSERT INTO users (name, email) VALUES (?, ?)", ("Alice", "alice@co.com"))
    cur.execute("SELECT * FROM users WHERE name = ?", ("Alice",))
    print(cur.fetchall())
    conn.commit()
    conn.close()
"""

import json
import time
import uuid
import re
import threading
import sqlite3 as _sqlite3   # used for schema storage only
from typing import Any, Optional, List, Tuple, Iterator
from dataclasses import dataclass, field
from enum import Enum
import sqlparse
from sqlparse.sql import Statement, Identifier, IdentifierList, Where, Comparison
from sqlparse.tokens import Keyword, DML, DDL, Punctuation, Number, String, Name


# ── DB-API 2.0 required module-level attributes ───────────────
apilevel    = "2.0"
threadsafety = 1
paramstyle  = "qmark"   # uses ? placeholders like sqlite3


# ── Exceptions (DB-API 2.0) ───────────────────────────────────
class Error(Exception):          pass
class DatabaseError(Error):      pass
class OperationalError(DatabaseError): pass
class IntegrityError(DatabaseError):   pass
class ProgrammingError(DatabaseError): pass
class InterfaceError(Error):     pass


# ── Column types ──────────────────────────────────────────────
class ColType(Enum):
    INTEGER = "INTEGER"
    REAL    = "REAL"
    TEXT    = "TEXT"
    BLOB    = "BLOB"
    BOOLEAN = "BOOLEAN"
    NULL    = "NULL"


@dataclass
class Column:
    name:         str
    type:         ColType = ColType.TEXT
    primary_key:  bool    = False
    unique:       bool    = False
    not_null:     bool    = False
    default:      Any     = None
    autoincrement:bool    = False

    def coerce(self, value: Any) -> Any:
        if value is None:
            if self.not_null:
                raise IntegrityError(f"NOT NULL constraint failed: {self.name}")
            return None
        if self.type == ColType.INTEGER:
            return int(value)
        if self.type == ColType.REAL:
            return float(value)
        if self.type == ColType.TEXT:
            return str(value)
        if self.type == ColType.BOOLEAN:
            if isinstance(value, str):
                return value.lower() in ('1', 'true', 'yes')
            return bool(value)
        return value


@dataclass
class TableSchema:
    name:    str
    columns: List[Column]
    indexes: dict = field(default_factory=dict)   # index_name → [col_names]

    def col(self, name: str) -> Optional[Column]:
        for c in self.columns:
            if c.name.lower() == name.lower():
                return c
        return None

    def col_names(self) -> List[str]:
        return [c.name for c in self.columns]

    def pk_col(self) -> Optional[Column]:
        for c in self.columns:
            if c.primary_key:
                return c
        return None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "columns": [
                {"name": c.name, "type": c.type.value,
                 "primary_key": c.primary_key, "unique": c.unique,
                 "not_null": c.not_null, "default": c.default,
                 "autoincrement": c.autoincrement}
                for c in self.columns
            ],
            "indexes": self.indexes,
        }

    @staticmethod
    def from_dict(d: dict) -> "TableSchema":
        cols = [Column(
            name=c["name"], type=ColType(c["type"]),
            primary_key=c.get("primary_key", False),
            unique=c.get("unique", False),
            not_null=c.get("not_null", False),
            default=c.get("default"),
            autoincrement=c.get("autoincrement", False),
        ) for c in d["columns"]]
        return TableSchema(name=d["name"], columns=cols, indexes=d.get("indexes", {}))


# ── SQL Parser ─────────────────────────────────────────────────
class SQLParser:
    """Parses SQL statements into structured command objects."""

    _TYPE_MAP = {
        "int": ColType.INTEGER, "integer": ColType.INTEGER,
        "real": ColType.REAL, "float": ColType.REAL, "double": ColType.REAL,
        "text": ColType.TEXT, "varchar": ColType.TEXT, "char": ColType.TEXT,
        "string": ColType.TEXT, "blob": ColType.BLOB,
        "bool": ColType.BOOLEAN, "boolean": ColType.BOOLEAN,
        "numeric": ColType.REAL, "decimal": ColType.REAL,
    }

    @classmethod
    def parse_type(cls, type_str: str) -> ColType:
        base = type_str.split("(")[0].lower().strip()
        return cls._TYPE_MAP.get(base, ColType.TEXT)

    @classmethod
    def parse_create_table(cls, sql: str) -> TableSchema:
        """Parse CREATE TABLE statement."""
        sql = re.sub(r'\s+', ' ', sql.strip())
        m = re.match(
            r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"]?(\w+)[`"]?\s*\((.+)\)\s*;?$',
            sql, re.IGNORECASE | re.DOTALL
        )
        if not m:
            raise ProgrammingError(f"Cannot parse CREATE TABLE: {sql}")

        table_name = m.group(1)
        cols_str   = m.group(2).strip()

        # Split on commas not inside parentheses
        col_defs = []
        depth, start = 0, 0
        for i, ch in enumerate(cols_str):
            if ch == '(':   depth += 1
            elif ch == ')': depth -= 1
            elif ch == ',' and depth == 0:
                col_defs.append(cols_str[start:i].strip())
                start = i + 1
        col_defs.append(cols_str[start:].strip())

        columns = []
        for col_def in col_defs:
            if not col_def or col_def.upper().startswith(("PRIMARY KEY", "UNIQUE", "INDEX",
                                                           "CONSTRAINT", "CHECK", "FOREIGN")):
                continue
            parts = col_def.split()
            if len(parts) < 1:
                continue
            name = parts[0].strip('`"')
            type_str = parts[1] if len(parts) > 1 else "TEXT"
            col_upper = col_def.upper()

            col = Column(
                name=name,
                type=cls.parse_type(type_str),
                primary_key  = "PRIMARY KEY" in col_upper,
                unique       = "UNIQUE" in col_upper,
                not_null     = "NOT NULL" in col_upper,
                autoincrement= "AUTOINCREMENT" in col_upper or
                               ("PRIMARY KEY" in col_upper and cls.parse_type(type_str) == ColType.INTEGER),
            )
            if "DEFAULT" in col_upper:
                dm = re.search(r'DEFAULT\s+(\S+)', col_def, re.IGNORECASE)
                if dm:
                    col.default = dm.group(1).strip("'\"")
            columns.append(col)

        return TableSchema(name=table_name, columns=columns)

    @classmethod
    def parse_insert(cls, sql: str, params: tuple = ()) -> dict:
        """Parse INSERT INTO statement."""
        sql = re.sub(r'\s+', ' ', sql.strip())
        m = re.match(
            r'INSERT\s+(?:OR\s+\w+\s+)?INTO\s+[`"]?(\w+)[`"]?\s*'
            r'\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)\s*;?$',
            sql, re.IGNORECASE
        )
        if not m:
            raise ProgrammingError(f"Cannot parse INSERT: {sql}")

        table   = m.group(1)
        cols    = [c.strip().strip('`"') for c in m.group(2).split(',')]
        val_str = m.group(3)

        # Extract raw value tokens
        raw_vals = cls._split_values(val_str)
        # Substitute ? with params
        param_idx = 0
        values = []
        for rv in raw_vals:
            rv = rv.strip()
            if rv == '?':
                if param_idx >= len(params):
                    raise ProgrammingError("Not enough parameters")
                values.append(params[param_idx])
                param_idx += 1
            else:
                values.append(cls._parse_literal(rv))

        return {"table": table, "columns": cols, "values": values}

    @classmethod
    def _split_values(cls, val_str: str) -> list:
        """Split comma-separated values respecting quotes."""
        vals, cur, in_str, q = [], "", False, None
        for ch in val_str:
            if in_str:
                cur += ch
                if ch == q: in_str = False
            elif ch in ("'", '"'):
                in_str, q, cur = True, ch, cur + ch
            elif ch == ',':
                vals.append(cur.strip()); cur = ""
            else:
                cur += ch
        if cur.strip():
            vals.append(cur.strip())
        return vals

    @classmethod
    def _parse_literal(cls, s: str) -> Any:
        s = s.strip()
        if s.upper() == "NULL": return None
        if s.upper() in ("TRUE", "1"):  return True
        if s.upper() in ("FALSE", "0"): return False
        if s.startswith(("'", '"')):    return s[1:-1]
        try:    return int(s)
        except: pass
        try:    return float(s)
        except: pass
        return s

    @classmethod
    def parse_select(cls, sql: str, params: tuple = ()) -> dict:
        """Parse SELECT statement."""
        sql = sql.strip().rstrip(';')
        param_idx = [0]

        def _sub_params(s):
            def _rep(m):
                if param_idx[0] >= len(params):
                    raise ProgrammingError("Not enough parameters")
                val = params[param_idx[0]]; param_idx[0] += 1
                return repr(val)
            return re.sub(r'\?', _rep, s)

        sql = _sub_params(sql)

        # SELECT cols FROM table [WHERE ...] [ORDER BY ...] [LIMIT n] [OFFSET n]
        m = re.match(
            r'SELECT\s+(.+?)\s+FROM\s+[`"]?(\w+)[`"]?'
            r'(?:\s+WHERE\s+(.+?))?'
            r'(?:\s+ORDER\s+BY\s+(.+?))?'
            r'(?:\s+LIMIT\s+(\d+))?'
            r'(?:\s+OFFSET\s+(\d+))?'
            r'\s*$',
            sql, re.IGNORECASE | re.DOTALL
        )
        if not m:
            raise ProgrammingError(f"Cannot parse SELECT: {sql}")

        cols_str   = m.group(1).strip()
        table      = m.group(2)
        where_str  = m.group(3)
        orderby_str= m.group(4)
        limit      = int(m.group(5)) if m.group(5) else None
        offset     = int(m.group(6)) if m.group(6) else 0

        # Parse column list
        if cols_str == '*':
            columns = ['*']
        else:
            columns = [c.strip().strip('`"') for c in cols_str.split(',')]

        # Parse WHERE
        where = cls._parse_where(where_str) if where_str else None

        # Parse ORDER BY
        order_by = []
        if orderby_str:
            for part in orderby_str.split(','):
                part = part.strip()
                if part.upper().endswith(' DESC'):
                    order_by.append((part[:-5].strip(), 'DESC'))
                else:
                    col = part.replace(' ASC', '').strip()
                    order_by.append((col, 'ASC'))

        return {
            "table": table, "columns": columns, "where": where,
            "order_by": order_by, "limit": limit, "offset": offset,
        }

    @classmethod
    def _parse_where(cls, where_str: str) -> dict:
        """Parse WHERE clause into a condition tree."""
        where_str = where_str.strip()

        # Handle AND / OR (simple, non-nested)
        and_parts = re.split(r'\bAND\b', where_str, flags=re.IGNORECASE)
        if len(and_parts) > 1:
            return {"op": "AND", "conditions": [cls._parse_condition(p) for p in and_parts]}

        or_parts = re.split(r'\bOR\b', where_str, flags=re.IGNORECASE)
        if len(or_parts) > 1:
            return {"op": "OR", "conditions": [cls._parse_condition(p) for p in or_parts]}

        return cls._parse_condition(where_str)

    @classmethod
    def _parse_condition(cls, cond_str: str) -> dict:
        cond_str = cond_str.strip()

        # IS NULL / IS NOT NULL
        m = re.match(r'[`"]?(\w+)[`"]?\s+IS\s+(NOT\s+)?NULL', cond_str, re.IGNORECASE)
        if m:
            return {"col": m.group(1), "op": "IS NOT NULL" if m.group(2) else "IS NULL", "val": None}

        # LIKE
        m = re.match(r'[`"]?(\w+)[`"]?\s+LIKE\s+(.+)', cond_str, re.IGNORECASE)
        if m:
            return {"col": m.group(1), "op": "LIKE", "val": cls._parse_literal(m.group(2).strip())}

        # IN (...)
        m = re.match(r'[`"]?(\w+)[`"]?\s+IN\s*\((.+)\)', cond_str, re.IGNORECASE)
        if m:
            vals = [cls._parse_literal(v.strip()) for v in m.group(2).split(',')]
            return {"col": m.group(1), "op": "IN", "val": vals}

        # Comparison operators
        for op in ['!=', '<>', '>=', '<=', '=', '>', '<']:
            m = re.match(rf'[`"]?(\w+)[`"]?\s*{re.escape(op)}\s*(.+)', cond_str)
            if m:
                col = m.group(1)
                val = cls._parse_literal(m.group(2).strip())
                return {"col": col, "op": op if op != '!=' else '<>', "val": val}

        raise ProgrammingError(f"Cannot parse condition: {cond_str}")

    @classmethod
    def parse_update(cls, sql: str, params: tuple = ()) -> dict:
        """Parse UPDATE statement."""
        sql = re.sub(r'\s+', ' ', sql.strip().rstrip(';'))
        param_idx = [0]

        def _sub(s):
            def _rep(m):
                val = params[param_idx[0]]; param_idx[0] += 1
                return repr(val)
            return re.sub(r'\?', _rep, s)

        sql = _sub(sql)
        m = re.match(
            r'UPDATE\s+[`"]?(\w+)[`"]?\s+SET\s+(.+?)(?:\s+WHERE\s+(.+))?\s*$',
            sql, re.IGNORECASE | re.DOTALL
        )
        if not m:
            raise ProgrammingError(f"Cannot parse UPDATE: {sql}")

        table  = m.group(1)
        set_str = m.group(2)
        where  = cls._parse_where(m.group(3)) if m.group(3) else None

        # Parse SET col = val, col2 = val2
        updates = {}
        for part in re.split(r',\s*(?=\w+\s*=)', set_str):
            sm = re.match(r'[`"]?(\w+)[`"]?\s*=\s*(.+)', part.strip())
            if sm:
                updates[sm.group(1)] = cls._parse_literal(sm.group(2).strip())

        return {"table": table, "updates": updates, "where": where}

    @classmethod
    def parse_delete(cls, sql: str, params: tuple = ()) -> dict:
        sql = re.sub(r'\s+', ' ', sql.strip().rstrip(';'))
        param_idx = [0]
        def _sub(s):
            def _rep(m):
                val = params[param_idx[0]]; param_idx[0] += 1
                return repr(val)
            return re.sub(r'\?', _rep, s)
        sql = _sub(sql)

        m = re.match(r'DELETE\s+FROM\s+[`"]?(\w+)[`"]?(?:\s+WHERE\s+(.+))?\s*$',
                     sql, re.IGNORECASE)
        if not m:
            raise ProgrammingError(f"Cannot parse DELETE: {sql}")
        return {
            "table": m.group(1),
            "where": cls._parse_where(m.group(2)) if m.group(2) else None,
        }


# ── Row evaluator ─────────────────────────────────────────────
class RowEvaluator:
    """Evaluates WHERE conditions against a row dict."""

    @staticmethod
    def match(row: dict, condition: Optional[dict]) -> bool:
        if condition is None:
            return True

        op = condition["op"]

        if op == "AND":
            return all(RowEvaluator.match(row, c) for c in condition["conditions"])
        if op == "OR":
            return any(RowEvaluator.match(row, c) for c in condition["conditions"])

        col   = condition["col"]
        val   = condition["val"]
        rval  = row.get(col)

        if op == "IS NULL":    return rval is None
        if op == "IS NOT NULL": return rval is not None

        if op == "IN":
            return rval in val

        if op == "LIKE":
            if rval is None: return False
            like_special = frozenset('.^$*+?{}[]()|\\')
            pat = ''
            for _ch in str(val):
                if _ch == '%': pat += '.*'
                elif _ch == '_': pat += '.'
                elif _ch in like_special: pat += '\\' + _ch
                else: pat += _ch
            return bool(re.fullmatch(pat, str(rval), re.IGNORECASE))

        # Coerce for comparison
        def _coerce(a, b):
            try:
                if isinstance(b, (int, float)) and not isinstance(b, bool):
                    return float(a), float(b)
            except: pass
            return str(a) if a is not None else None, str(b) if b is not None else None

        rv, vv = _coerce(rval, val)

        if op in ('=',):   return rv == vv
        if op in ('<>',):  return rv != vv
        if op == '>':      return rv is not None and rv > vv
        if op == '>=':     return rv is not None and rv >= vv
        if op == '<':      return rv is not None and rv < vv
        if op == '<=':     return rv is not None and rv <= vv

        return False


# ── PNEUMA SQL Store ──────────────────────────────────────────
class PneumaSQLStore:
    """
    SQL storage engine on top of PNEUMA-DB.
    Tables are stored as key-value pairs:
      _schema:{table}   → TableSchema JSON
      {table}:{pk}      → row JSON
      _idx:{table}:{col}:{val} → pk
      _seq:{table}      → auto-increment counter
    """

    def __init__(self, db, node_id: str = "local"):
        self.db      = db      # PNEUMA_DB instance or LocalStore
        self.node_id = node_id
        self._schema_cache: dict[str, TableSchema] = {}
        self._lock   = threading.Lock()

    # ── Schema ────────────────────────────────────────────────
    def create_table(self, schema: TableSchema, if_not_exists: bool = False):
        key = f"_schema:{schema.name}"
        existing = self._get(key)
        if existing:
            if if_not_exists:
                return
            raise OperationalError(f"Table '{schema.name}' already exists")
        self._put(key, schema.to_dict())
        with self._lock:
            self._schema_cache[schema.name] = schema

    def get_schema(self, table: str) -> TableSchema:
        with self._lock:
            if table in self._schema_cache:
                return self._schema_cache[table]
        raw = self._get(f"_schema:{table}")
        if not raw:
            raise OperationalError(f"No such table: {table}")
        schema = TableSchema.from_dict(raw)
        with self._lock:
            self._schema_cache[table] = schema
        return schema

    def drop_table(self, table: str):
        schema = self.get_schema(table)
        # Delete all rows
        rows = self._scan(f"{table}:")
        for key in rows:
            self._delete(key)
        # Delete schema
        self._delete(f"_schema:{table}")
        # Delete indexes
        idx_rows = self._scan(f"_idx:{table}:")
        for key in idx_rows:
            self._delete(key)
        with self._lock:
            self._schema_cache.pop(table, None)

    def create_index(self, index_name: str, table: str, columns: list[str]):
        schema = self.get_schema(table)
        schema.indexes[index_name] = columns
        self._put(f"_schema:{table}", schema.to_dict())
        with self._lock:
            self._schema_cache[table] = schema
        # Build index from existing rows
        for row in self.scan_all(table):
            for col in columns:
                if col in row:
                    self._put(f"_idx:{table}:{col}:{row[col]}", row.get(schema.pk_col().name if schema.pk_col() else "_id"))

    def table_exists(self, table: str) -> bool:
        return self._get(f"_schema:{table}") is not None

    def list_tables(self) -> list[str]:
        schemas = self._scan("_schema:")
        return [k.replace("_schema:", "") for k in schemas]

    # ── Insert ────────────────────────────────────────────────
    def insert(self, table: str, columns: list[str], values: list) -> Any:
        schema = self.get_schema(table)

        # Build row dict
        row = {}
        for col in schema.columns:
            if col.default is not None:
                row[col.name] = col.coerce(col.default)

        for col_name, val in zip(columns, values):
            col_def = schema.col(col_name)
            if not col_def:
                raise OperationalError(f"No column '{col_name}' in table '{table}'")
            row[col_name] = col_def.coerce(val)

        # Handle auto-increment primary key
        pk_col = schema.pk_col()
        if pk_col:
            if pk_col.name not in row or row[pk_col.name] is None:
                if pk_col.autoincrement:
                    row[pk_col.name] = self._next_id(table)
            pk = row[pk_col.name]
        else:
            pk = str(uuid.uuid4())[:8]
            row["_id"] = pk

        pk = str(pk)

        # Unique constraint check
        for col in schema.columns:
            if col.unique and col.name in row and row[col.name] is not None:
                idx_key = f"_idx:{table}:{col.name}:{row[col.name]}"
                if self._get(idx_key) is not None:
                    raise IntegrityError(f"UNIQUE constraint failed: {table}.{col.name}")

        # Not null checks
        for col in schema.columns:
            if col.not_null and col.name not in row:
                raise IntegrityError(f"NOT NULL constraint failed: {table}.{col.name}")

        # Store row
        self._put(f"{table}:{pk}", row)

        # Update indexes
        for idx_name, idx_cols in schema.indexes.items():
            for idx_col in idx_cols:
                if idx_col in row:
                    self._put(f"_idx:{table}:{idx_col}:{row[idx_col]}", pk)

        # Update unique indexes
        for col in schema.columns:
            if col.unique and col.name in row and row[col.name] is not None:
                self._put(f"_idx:{table}:{col.name}:{row[col.name]}", pk)

        return pk

    # ── Select ────────────────────────────────────────────────
    def select(self, parsed: dict) -> List[Tuple]:
        table    = parsed["table"]
        cols     = parsed["columns"]
        where    = parsed["where"]
        order_by = parsed["order_by"]
        limit    = parsed["limit"]
        offset   = parsed["offset"]
        schema   = self.get_schema(table)

        # Get all rows
        rows = self.scan_all(table)

        # Filter
        rows = [r for r in rows if RowEvaluator.match(r, where)]

        # Order
        for col_name, direction in reversed(order_by):
            reverse = (direction == 'DESC')
            rows.sort(key=lambda r: (r.get(col_name) is None, r.get(col_name)), reverse=reverse)

        # Offset
        rows = rows[offset:]

        # Limit
        if limit is not None:
            rows = rows[:limit]

        # Project columns
        if cols == ['*']:
            col_names = schema.col_names()
        else:
            col_names = cols

        return [tuple(r.get(c) for c in col_names) for r in rows], col_names

    # ── Update ────────────────────────────────────────────────
    def update(self, parsed: dict) -> int:
        table   = parsed["table"]
        updates = parsed["updates"]
        where   = parsed["where"]
        schema  = self.get_schema(table)
        count   = 0

        for row in self.scan_all(table):
            if not RowEvaluator.match(row, where):
                continue
            pk_col  = schema.pk_col()
            pk      = str(row.get(pk_col.name if pk_col else "_id", ""))
            for k, v in updates.items():
                col_def = schema.col(k)
                row[k]  = col_def.coerce(v) if col_def else v
            row["_updated_at"] = time.time()
            self._put(f"{table}:{pk}", row)
            count += 1

        return count

    # ── Delete ────────────────────────────────────────────────
    def delete(self, parsed: dict) -> int:
        table  = parsed["table"]
        where  = parsed["where"]
        schema = self.get_schema(table)
        count  = 0

        for row in self.scan_all(table):
            if not RowEvaluator.match(row, where):
                continue
            pk_col = schema.pk_col()
            pk     = str(row.get(pk_col.name if pk_col else "_id", ""))
            self._delete(f"{table}:{pk}")
            count += 1

        return count

    # ── Row scan ──────────────────────────────────────────────
    def scan_all(self, table: str) -> List[dict]:
        rows = self._scan(f"{table}:")
        result = []
        for key, val in rows.items():
            if isinstance(val, dict) and not key.startswith("_"):
                result.append(val)
        return result

    def row_count(self, table: str) -> int:
        return len(self.scan_all(table))

    # ── Storage backend abstraction ───────────────────────────
    def _put(self, key: str, value: Any):
        if hasattr(self.db, 'put'):
            self.db.put(key, value)
        else:
            self.db.store.put(key, value)

    def _get(self, key: str) -> Any:
        if hasattr(self.db, 'get'):
            return self.db.get(key)
        else:
            return self.db.store.get(key)

    def _delete(self, key: str):
        if hasattr(self.db, 'delete'):
            self.db.delete(key)
        else:
            self.db.store.delete(key)

    def _scan(self, prefix: str) -> dict:
        if hasattr(self.db, 'scan_prefix'):
            return self.db.scan_prefix(prefix)
        else:
            return self.db.store.scan_prefix(prefix)

    def _next_id(self, table: str) -> int:
        if hasattr(self.db, 'next_id'):
            return self.db.next_id(table)
        else:
            return self.db.store.next_id(table)


# ── Cursor (DB-API 2.0) ───────────────────────────────────────
class Cursor:
    """DB-API 2.0 compatible cursor."""

    def __init__(self, store: PneumaSQLStore, connection: "Connection"):
        self._store      = store
        self._conn       = connection
        self._results:   List[Tuple] = []
        self._col_names: List[str]   = []
        self._pos:       int         = 0
        self.rowcount:   int         = -1
        self.lastrowid:  Any         = None
        self.arraysize:  int         = 1

    @property
    def description(self) -> Optional[List]:
        if not self._col_names:
            return None
        return [(name, None, None, None, None, None, None)
                for name in self._col_names]

    def execute(self, sql: str, parameters: tuple = ()):
        sql_stripped = re.sub(r'\s+', ' ', sql.strip())
        verb = sql_stripped.split()[0].upper()

        if verb == "CREATE":
            self._execute_create(sql_stripped, parameters)
        elif verb == "INSERT":
            self._execute_insert(sql_stripped, parameters)
        elif verb == "SELECT":
            self._execute_select(sql_stripped, parameters)
        elif verb == "UPDATE":
            self._execute_update(sql_stripped, parameters)
        elif verb == "DELETE":
            self._execute_delete(sql_stripped, parameters)
        elif verb == "DROP":
            self._execute_drop(sql_stripped, parameters)
        elif verb in ("BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT"):
            pass   # transactions are no-ops (PNEUMA-DB is eventually consistent)
        elif verb == "PRAGMA":
            self._execute_pragma(sql_stripped)
        else:
            raise ProgrammingError(f"Unsupported SQL verb: {verb}")

        return self

    def _execute_create(self, sql: str, params: tuple):
        upper = sql.upper()
        if "CREATE TABLE" in upper:
            schema = SQLParser.parse_create_table(sql)
            if_not_exists = "IF NOT EXISTS" in upper
            self._store.create_table(schema, if_not_exists=if_not_exists)
            self.rowcount = 0
        elif "CREATE INDEX" in upper:
            m = re.match(
                r'CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?'
                r'[`"]?(\w+)[`"]?\s+ON\s+[`"]?(\w+)[`"]?\s*\(([^)]+)\)',
                sql, re.IGNORECASE
            )
            if m:
                self._store.create_index(m.group(1), m.group(2),
                    [c.strip().strip('`"') for c in m.group(3).split(',')])
            self.rowcount = 0

    def _execute_insert(self, sql: str, params: tuple):
        parsed        = SQLParser.parse_insert(sql, params)
        pk            = self._store.insert(parsed["table"], parsed["columns"], parsed["values"])
        self.lastrowid = pk
        self.rowcount  = 1
        self._results  = []

    def _execute_select(self, sql: str, params: tuple):
        parsed             = SQLParser.parse_select(sql, params)
        self._results, self._col_names = self._store.select(parsed)
        self._pos          = 0
        self.rowcount      = len(self._results)

    def _execute_update(self, sql: str, params: tuple):
        parsed        = SQLParser.parse_update(sql, params)
        self.rowcount = self._store.update(parsed)
        self._results = []

    def _execute_delete(self, sql: str, params: tuple):
        parsed        = SQLParser.parse_delete(sql, params)
        self.rowcount = self._store.delete(parsed)
        self._results = []

    def _execute_drop(self, sql: str, params: tuple):
        m = re.match(r'DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?[`"]?(\w+)[`"]?',
                     sql, re.IGNORECASE)
        if m:
            try:
                self._store.drop_table(m.group(1))
            except OperationalError:
                if "IF EXISTS" not in sql.upper():
                    raise
        self.rowcount = 0

    def _execute_pragma(self, sql: str):
        m = re.match(r'PRAGMA\s+(\w+)', sql, re.IGNORECASE)
        if m:
            pragma = m.group(1).lower()
            if pragma == "table_info":
                tm = re.search(r'table_info\(["`]?(\w+)["`]?\)', sql, re.IGNORECASE)
                if tm:
                    try:
                        schema = self._store.get_schema(tm.group(1))
                        self._results  = [(i, c.name, c.type.value,
                                           1 if c.not_null else 0,
                                           c.default, 1 if c.primary_key else 0)
                                          for i, c in enumerate(schema.columns)]
                        self._col_names = ["cid","name","type","notnull","dflt_value","pk"]
                        self._pos = 0
                    except: self._results = []
            elif pragma == "database_list":
                self._results  = [(0, "main", "pneuma")]
                self._col_names = ["seq", "name", "file"]
                self._pos = 0

    def executemany(self, sql: str, seq_of_params):
        for params in seq_of_params:
            self.execute(sql, params)

    def executescript(self, script: str):
        for stmt in script.split(';'):
            stmt = stmt.strip()
            if stmt:
                self.execute(stmt)

    # ── Fetch methods ─────────────────────────────────────────
    def fetchone(self) -> Optional[Tuple]:
        if self._pos >= len(self._results):
            return None
        row = self._results[self._pos]
        self._pos += 1
        return row

    def fetchmany(self, size: Optional[int] = None) -> List[Tuple]:
        size = size or self.arraysize
        rows = self._results[self._pos:self._pos + size]
        self._pos += len(rows)
        return rows

    def fetchall(self) -> List[Tuple]:
        rows = self._results[self._pos:]
        self._pos = len(self._results)
        return rows

    def __iter__(self) -> Iterator[Tuple]:
        return iter(self._results[self._pos:])

    def close(self):
        self._results  = []
        self._col_names = []
        self._pos      = 0


# ── Connection (DB-API 2.0) ───────────────────────────────────
class Connection:
    """
    DB-API 2.0 compatible connection to PNEUMA-DB.
    Identical interface to sqlite3.Connection.

    Usage:
        conn = pneuma_sql.connect("ws://relay.pneuma.io:8765", node_id="app")
        # or for local/offline:
        conn = pneuma_sql.connect(":local:", node_id="app")
    """

    def __init__(self, db, node_id: str = "pneuma-sql"):
        self._db    = db
        self._store = PneumaSQLStore(db, node_id)
        self._closed = False
        self._lock   = threading.Lock()
        # Standard sqlite3 attributes
        self.isolation_level = ""    # auto-commit mode
        self.row_factory     = None  # can be set to sqlite3.Row equivalent

    def cursor(self) -> Cursor:
        if self._closed:
            raise InterfaceError("Connection closed")
        return Cursor(self._store, self)

    def execute(self, sql: str, parameters: tuple = ()) -> Cursor:
        """Shortcut — creates a cursor and executes."""
        cur = self.cursor()
        cur.execute(sql, parameters)
        return cur

    def executemany(self, sql: str, seq_of_params) -> Cursor:
        cur = self.cursor()
        cur.executemany(sql, seq_of_params)
        return cur

    def executescript(self, script: str) -> Cursor:
        cur = self.cursor()
        cur.executescript(script)
        return cur

    def commit(self):
        pass   # PNEUMA-DB writes are immediately durable

    def rollback(self):
        pass   # No transaction support yet (v1.0)

    def close(self):
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.commit()
        self.close()
        return False

    # ── Introspection ─────────────────────────────────────────
    def tables(self) -> List[str]:
        """List all tables in the database."""
        return self._store.list_tables()

    def schema(self, table: str) -> TableSchema:
        """Return the schema for a table."""
        return self._store.get_schema(table)

    def row_count(self, table: str) -> int:
        """Return the number of rows in a table."""
        return self._store.row_count(table)


# ── Module-level connect() function (DB-API 2.0) ──────────────
def connect(
    database:  str  = ":local:",
    node_id:   str  = "pneuma-sql",
    relay_url: Optional[str] = None,
    **kwargs
) -> Connection:
    """
    Connect to PNEUMA-DB with a SQL interface.

    Parameters:
        database:  Relay URL "ws://..." or ":local:" for offline/acoustic mode
        node_id:   Unique name for this node
        relay_url: Optional relay URL (alternative to passing in database)

    Returns a Connection object identical to sqlite3.connect().

    Examples:
        # Local mode (acoustic mesh, no internet)
        conn = pneuma_sql.connect(":local:", node_id="my-app")

        # Global mode (via relay)
        conn = pneuma_sql.connect("ws://relay.pneuma.io:8765", node_id="my-app")

        # Drop-in sqlite3 replacement (uses local mode)
        conn = pneuma_sql.connect("my_database.db")
    """
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pneuma-db"))

    from pneuma_db.db   import PNEUMA_DB, LocalStore
    from pneuma_db.node import PNEUMANode

    # Determine relay URL
    if relay_url:
        _relay = relay_url
    elif database.startswith("ws://") or database.startswith("wss://"):
        _relay = database
    else:
        _relay = None   # local mode

    # Create node and DB
    node = PNEUMANode(node_id=node_id, known_nodes=[node_id])
    db   = PNEUMA_DB(node, relay_url=_relay)

    if _relay:
        try:
            db.connect_relay_sync()
        except Exception as e:
            print(f"[PNEUMA SQL] Relay connection failed: {e}. Using local mode.")

    return Connection(db, node_id=node_id)
