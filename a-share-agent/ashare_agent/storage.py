"""SQLite 存储：持仓快照、成交、卡片、对话、发送记录、登录会话、缓存。只保存脱敏后的数据。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .market import now_cn
from .portfolio import Position
from .trades import Trade

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS positions_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT, as_of TEXT, created_at TEXT, source TEXT, cash REAL, positions TEXT);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, uniq TEXT UNIQUE, trade_date TEXT, trade_time TEXT, code TEXT, name TEXT,
    side TEXT, quantity INTEGER, price REAL, amount REAL, commission REAL, stamp_tax REAL, transfer_fee REAL,
    other_fee REAL, fee_estimated INTEGER, plan_card_id INTEGER, source TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(trade_date);
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, code TEXT, title TEXT, created_at TEXT, as_of TEXT,
    target_date TEXT, review_date TEXT, status TEXT, payload TEXT, feedback TEXT, feedback_note TEXT, result TEXT);
CREATE INDEX IF NOT EXISTS idx_cards_kind ON cards(kind, as_of);
CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY AUTOINCREMENT, card_id INTEGER, title TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, role TEXT, content TEXT, meta TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS send_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, profile TEXT, model TEXT, purpose TEXT, payload TEXT,
    masked INTEGER);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, created_at REAL, expires_at REAL, ip TEXT, agent TEXT);
CREATE TABLE IF NOT EXISTS login_attempts (ip TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, created_at REAL, payload TEXT);
"""


def _now() -> str:
    return now_cn().strftime("%Y-%m-%d %H:%M:%S")


class Storage:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self):
        with self._lock:
            c = sqlite3.connect(self.path, timeout=30)
            c.row_factory = sqlite3.Row
            try:
                c.execute("PRAGMA journal_mode=WAL")
                yield c
                c.commit()
            finally:
                c.close()

    # ---------- kv ----------
    def get(self, key: str, default: Any = None) -> Any:
        with self.conn() as c:
            row = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set(self, key: str, value: Any) -> None:
        with self.conn() as c:
            c.execute("INSERT OR REPLACE INTO kv(key, value) VALUES(?, ?)", (key, json.dumps(value, ensure_ascii=False)))

    # ---------- 持仓 ----------
    def save_positions(self, positions: list[Position], as_of: str, source: str, cash: float | None = None) -> int:
        if cash is None:
            cash = self.latest_cash()
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO positions_snapshots(as_of, created_at, source, cash, positions) VALUES(?,?,?,?,?)",
                (as_of, _now(), source, cash, json.dumps([p.to_dict() for p in positions], ensure_ascii=False)),
            )
            return int(cur.lastrowid)

    def latest_positions(self) -> tuple[list[Position], dict | None]:
        with self.conn() as c:
            row = c.execute("SELECT * FROM positions_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return [], None
        meta = {"as_of": row["as_of"], "created_at": row["created_at"], "source": row["source"], "cash": row["cash"] or 0.0}
        return [Position.from_dict(d) for d in json.loads(row["positions"])], meta

    def positions_before(self, as_of: str) -> list[Position]:
        """某日之前最后一份持仓快照（用于对账）。"""
        with self.conn() as c:
            row = c.execute(
                "SELECT positions FROM positions_snapshots WHERE as_of < ? ORDER BY as_of DESC, id DESC LIMIT 1", (as_of,)
            ).fetchone()
        return [Position.from_dict(d) for d in json.loads(row["positions"])] if row else []

    def latest_cash(self) -> float:
        with self.conn() as c:
            row = c.execute("SELECT cash FROM positions_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        return float(row["cash"] or 0.0) if row else 0.0

    def set_cash(self, cash: float) -> None:
        positions, meta = self.latest_positions()
        as_of = meta["as_of"] if meta else now_cn().date().isoformat()
        self.save_positions(positions, as_of, "资金更新", cash)

    # ---------- 成交 ----------
    def add_trades(self, trades: list[Trade]) -> tuple[int, int]:
        added = dup = 0
        with self.conn() as c:
            for t in trades:
                uniq = "|".join(map(str, t.key()))
                try:
                    c.execute(
                        "INSERT INTO trades(uniq, trade_date, trade_time, code, name, side, quantity, price, amount, commission,"
                        " stamp_tax, transfer_fee, other_fee, fee_estimated, plan_card_id, source, created_at)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (uniq, t.trade_date, t.trade_time, t.code, t.name, t.side, t.quantity, t.price, t.amount, t.commission,
                         t.stamp_tax, t.transfer_fee, t.other_fee, int(t.fee_estimated), t.plan_card_id, t.source, _now()),
                    )
                    added += 1
                except sqlite3.IntegrityError:
                    dup += 1
        return added, dup

    def trades(self, start: str | None = None, end: str | None = None, code: str | None = None) -> list[Trade]:
        sql = "SELECT * FROM trades WHERE 1=1"
        args: list[Any] = []
        if start:
            sql += " AND trade_date >= ?"
            args.append(start)
        if end:
            sql += " AND trade_date <= ?"
            args.append(end)
        if code:
            sql += " AND code = ?"
            args.append(code)
        sql += " ORDER BY trade_date, trade_time, id"
        with self.conn() as c:
            rows = c.execute(sql, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["fee_estimated"] = bool(d["fee_estimated"])
            out.append(Trade.from_dict(d))
        return out

    def delete_trades_on(self, trade_date: str) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM trades WHERE trade_date=?", (trade_date,))

    # ---------- 卡片 ----------
    def add_card(self, kind: str, payload: dict, *, code: str = "", title: str = "", as_of: str = "",
                 target_date: str = "", review_date: str = "", status: str = "open") -> int:
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO cards(kind, code, title, created_at, as_of, target_date, review_date, status, payload)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (kind, code, title, _now(), as_of, target_date, review_date, status, json.dumps(payload, ensure_ascii=False)),
            )
            return int(cur.lastrowid)

    def update_card(self, card_id: int, **fields: Any) -> None:
        if "payload" in fields:
            fields["payload"] = json.dumps(fields["payload"], ensure_ascii=False)
        if "result" in fields and not isinstance(fields["result"], str):
            fields["result"] = json.dumps(fields["result"], ensure_ascii=False)
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.conn() as c:
            c.execute(f"UPDATE cards SET {cols} WHERE id=?", (*fields.values(), card_id))

    @staticmethod
    def _card(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["payload"] = json.loads(d["payload"] or "{}")
        d["result"] = json.loads(d["result"]) if d.get("result") else None
        return d

    def card(self, card_id: int) -> dict | None:
        with self.conn() as c:
            row = c.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
        return self._card(row) if row else None

    def cards(self, kind: str | None = None, status: str | None = None, as_of: str | None = None,
              target_date: str | None = None, code: str | None = None, limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM cards WHERE 1=1"
        args: list[Any] = []
        for col, val in (("kind", kind), ("status", status), ("as_of", as_of), ("target_date", target_date), ("code", code)):
            if val:
                sql += f" AND {col}=?"
                args.append(val)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self.conn() as c:
            return [self._card(r) for r in c.execute(sql, args).fetchall()]

    # ---------- 对话 ----------
    def new_chat(self, title: str, card_id: int | None = None) -> int:
        with self.conn() as c:
            cur = c.execute("INSERT INTO chats(card_id, title, created_at) VALUES(?,?,?)", (card_id, title, _now()))
            return int(cur.lastrowid)

    def chats(self, limit: int = 30) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM chats ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    def chat(self, chat_id: int) -> dict | None:
        with self.conn() as c:
            row = c.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        return dict(row) if row else None

    def add_message(self, chat_id: int, role: str, content: str, meta: dict | None = None) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO chat_messages(chat_id, role, content, meta, created_at) VALUES(?,?,?,?,?)",
                (chat_id, role, content, json.dumps(meta or {}, ensure_ascii=False), _now()),
            )

    def messages(self, chat_id: int) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM chat_messages WHERE chat_id=? ORDER BY id", (chat_id,)).fetchall()
        return [dict(r) | {"meta": json.loads(r["meta"] or "{}")} for r in rows]

    # ---------- 发送记录 ----------
    def log_send(self, profile: str, model: str, purpose: str, payload: Any, masked: int) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO send_log(created_at, profile, model, purpose, payload, masked) VALUES(?,?,?,?,?,?)",
                (_now(), profile, model, purpose, json.dumps(payload, ensure_ascii=False, default=str), masked),
            )

    def send_logs(self, limit: int = 100) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM send_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    def all_sent_text(self) -> str:
        with self.conn() as c:
            return "\n".join(r["payload"] for r in c.execute("SELECT payload FROM send_log").fetchall())

    # ---------- 会话与登录 ----------
    def add_session(self, token_hash: str, ttl: float, ip: str, agent: str) -> None:
        now = time.time()
        with self.conn() as c:
            c.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (token_hash, now, now + ttl, ip, agent[:200]))

    def session_valid(self, token_hash: str) -> bool:
        with self.conn() as c:
            row = c.execute("SELECT expires_at FROM sessions WHERE token_hash=?", (token_hash,)).fetchone()
        return bool(row) and row["expires_at"] > time.time()

    def delete_session(self, token_hash: str | None = None) -> None:
        with self.conn() as c:
            if token_hash:
                c.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
            else:
                c.execute("DELETE FROM sessions")

    def session_count(self) -> int:
        with self.conn() as c:
            return int(c.execute("SELECT COUNT(*) FROM sessions WHERE expires_at > ?", (time.time(),)).fetchone()[0])

    def record_failed_login(self, ip: str) -> None:
        with self.conn() as c:
            c.execute("INSERT INTO login_attempts VALUES(?,?)", (ip, time.time()))

    def failed_logins_since(self, ip: str, since: float) -> list[float]:
        with self.conn() as c:
            rows = c.execute("SELECT ts FROM login_attempts WHERE ip=? AND ts>=? ORDER BY ts", (ip, since)).fetchall()
        return [r["ts"] for r in rows]

    def clear_failed_logins(self, ip: str) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM login_attempts WHERE ip=?", (ip,))

    # ---------- 缓存 ----------
    def cache_get(self, key: str) -> tuple[float, Any] | None:
        with self.conn() as c:
            row = c.execute("SELECT created_at, payload FROM cache WHERE key=?", (key,)).fetchone()
        return (row["created_at"], json.loads(row["payload"])) if row else None

    def cache_set(self, key: str, payload: Any) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO cache(key, created_at, payload) VALUES(?,?,?)",
                (key, time.time(), json.dumps(payload, ensure_ascii=False, default=str)),
            )
