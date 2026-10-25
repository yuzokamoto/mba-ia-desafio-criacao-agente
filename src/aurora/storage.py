"""Persistência transacional das regras do condomínio."""

import json
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Storage:
    def __init__(self, path: Path, data_dir: Path):
        self.path = path
        self.data_dir = data_dir
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS apartments (number TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, apartment TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS issued_codes (code TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS reservations (
                    codigo TEXT PRIMARY KEY, apartamento TEXT NOT NULL,
                    area TEXT NOT NULL, data TEXT NOT NULL, status TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS active_slot
                    ON reservations(area, data) WHERE status = 'ativa';
                CREATE TABLE IF NOT EXISTS visitors (
                    apartamento TEXT NOT NULL, nome TEXT NOT NULL, data TEXT NOT NULL,
                    UNIQUE(apartamento, nome, data));
                CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, result TEXT NOT NULL);
            """)
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM metadata WHERE key='initialized'").fetchone():
                self._seed(db)
                db.execute("INSERT INTO metadata VALUES ('initialized')")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _load(self, name):
        return json.loads((self.data_dir / f"{name}.json").read_text())

    def _seed(self, db):
        for row in self._load("apartamentos"):
            db.execute("INSERT OR IGNORE INTO apartments VALUES (?)", (row["numero"],))
        for row in self._load("reservas"):
            db.execute("INSERT OR IGNORE INTO issued_codes VALUES (?)", (row["codigo"],))
            db.execute("INSERT INTO reservations VALUES (?, ?, ?, ?, 'ativa')",
                       tuple(row[k] for k in ("codigo", "apartamento", "area", "data")))
        for row in self._load("visitantes"):
            db.execute("INSERT INTO visitors VALUES (?, ?, ?)",
                       tuple(row[k] for k in ("apartamento", "nome", "data")))

    def reset(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM reservations")
            db.execute("DELETE FROM visitors")
            self._seed(db)

    def apartment_exists(self, apartment):
        with self.connect() as db:
            return bool(db.execute("SELECT 1 FROM apartments WHERE number=?", (apartment,)).fetchone())

    def register_session(self, session_id, apartment):
        with self.connect() as db:
            db.execute("INSERT INTO sessions VALUES (?, ?)", (session_id, apartment))

    def session_apartment(self, session_id):
        with self.connect() as db:
            row = db.execute("SELECT apartment FROM sessions WHERE id=?", (session_id,)).fetchone()
            return row[0] if row else None

    def reservations(self, apartment):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT codigo, area, data FROM reservations WHERE apartamento=? AND status='ativa' ORDER BY data, codigo",
                (apartment,))]

    def visitors(self, apartment):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT nome, data FROM visitors WHERE apartamento=? ORDER BY data, nome", (apartment,))]

    def available(self, area, date):
        with self.connect() as db:
            return not db.execute("SELECT 1 FROM reservations WHERE area=? AND data=? AND status='ativa'",
                                  (area, date)).fetchone()

    def reserve(self, apartment, area, date, operation_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT result FROM operations WHERE id=?", (operation_id,)).fetchone()
            if previous:
                return json.loads(previous[0])
            if db.execute("SELECT 1 FROM reservations WHERE area=? AND data=? AND status='ativa'", (area, date)).fetchone():
                result = {"status": "ocupada", "mensagem": "A data não está mais disponível."}
            else:
                while True:
                    code = "RSV-" + secrets.token_hex(6).upper()
                    if db.execute("SELECT 1 FROM issued_codes WHERE code=?", (code,)).fetchone():
                        continue
                    try:
                        db.execute("INSERT INTO issued_codes VALUES (?)", (code,))
                        db.execute("INSERT INTO reservations VALUES (?, ?, ?, ?, 'ativa')", (code, apartment, area, date))
                        result = {"status": "reservada", "codigo": code, "area": area, "data": date}
                        break
                    except sqlite3.IntegrityError:
                        if db.execute("SELECT 1 FROM reservations WHERE area=? AND data=? AND status='ativa'", (area, date)).fetchone():
                            result = {"status": "ocupada", "mensagem": "A data não está mais disponível."}
                            break
            db.execute("INSERT INTO operations VALUES (?, ?)", (operation_id, json.dumps(result)))
            return result

    def cancel(self, apartment, code=None, area=None, date=None):
        with self.connect() as db:
            cursor = db.execute("""UPDATE reservations SET status='cancelada'
                WHERE apartamento=? AND status='ativa' AND
                ((? IS NOT NULL AND codigo=?) OR (? IS NULL AND area=? AND data=?))""",
                (apartment, code, code, code, area, date))
            return {"status": "cancelada" if cursor.rowcount else "não encontrada",
                    "mensagem": "Reserva cancelada." if cursor.rowcount else "Você não possui essa reserva."}

    def authorize(self, apartment, name, date, operation_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT result FROM operations WHERE id=?", (operation_id,)).fetchone()
            if previous:
                return json.loads(previous[0])
            db.execute("INSERT OR IGNORE INTO visitors VALUES (?, ?, ?)", (apartment, name, date))
            result = {"status": "autorizada", "nome": name, "data": date}
            db.execute("INSERT INTO operations VALUES (?, ?)", (operation_id, json.dumps(result)))
            return result
