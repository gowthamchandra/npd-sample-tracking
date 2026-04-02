#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).parent
STATIC_DIR = ROOT / "static"
DB_PATH = ROOT / "sample_tracking_v2.db"


def utc_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS lots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lot_number TEXT NOT NULL UNIQUE,
  product_name TEXT NOT NULL,
  initial_quantity REAL NOT NULL CHECK(initial_quantity >= 0),
  unit_measure TEXT NOT NULL,
  npd_project_ref TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'Draft',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS analyses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lot_id INTEGER NOT NULL,
  test_type TEXT NOT NULL,
  spec_value TEXT NOT NULL,
  result_value TEXT NOT NULL,
  is_pass INTEGER NOT NULL DEFAULT 0,
  analyst_name TEXT NOT NULL,
  test_date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (lot_id) REFERENCES lots(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS dispatches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lot_id INTEGER NOT NULL,
  customer_name TEXT NOT NULL,
  quantity_sent REAL NOT NULL CHECK(quantity_sent > 0),
  courier_name TEXT NOT NULL,
  awb_number TEXT NOT NULL,
  dispatch_date TEXT NOT NULL,
  delivery_status TEXT NOT NULL DEFAULT 'Dispatched',
  FOREIGN KEY (lot_id) REFERENCES lots(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dispatch_id INTEGER NOT NULL UNIQUE,
  rating REAL NOT NULL CHECK(rating >= 0 AND rating <= 5),
  technical_notes TEXT NOT NULL,
  action_required INTEGER NOT NULL DEFAULT 0,
  next_steps TEXT DEFAULT '',
  marketing_person TEXT NOT NULL,
  feedback_date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (dispatch_id) REFERENCES dispatches(id) ON DELETE CASCADE
);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def query_all(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def query_one(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> dict[str, Any] | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def init_db() -> None:
    should_seed = not DB_PATH.exists()
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        if not should_seed:
            return
        conn.executemany(
            """
            INSERT INTO lots (lot_number, product_name, initial_quantity, unit_measure, npd_project_ref, notes, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("LOT-24001", "Hydra Protein Bar", 240, "kg", "NPD-ALPHA", "Pilot batch for sensory review", "In Review"),
                ("LOT-24002", "Sparkling Matcha", 180, "L", "NPD-BETA", "Awaiting dispatch to Delhi customer panel", "Approved"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO analyses (lot_id, test_type, spec_value, result_value, is_pass, analyst_name, test_date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "GD", "Moisture < 7%", "6.4%", 1, "Ananya Rao", "2026-03-26"),
                (1, "XRF", "Metal trace ND", "ND", 1, "Ananya Rao", "2026-03-27"),
                (2, "PSD", "Mean 18-22um", "21um", 1, "Karthik Menon", "2026-03-28"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO dispatches (lot_id, customer_name, quantity_sent, courier_name, awb_number, dispatch_date, delivery_status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (2, "Retail Insights Lab", 35, "BlueDart", "AWB-993183", "2026-03-29", "Delivered"),
                (1, "North Pilot Kitchen", 20, "Delhivery", "AWB-993184", "2026-03-31", "In-Transit"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO feedback (dispatch_id, rating, technical_notes, action_required, next_steps, marketing_person, feedback_date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 4.5, "Excellent carbonation hold. Packaging needs a clearer flavor cue.", 1, "Revise sleeve artwork before next dispatch", "Meera Shah", "2026-04-01"),
            ],
        )


ROLES = {"admin", "quality", "logistics", "marketing"}


def can_access(role: str, zone: str) -> bool:
    perms = {
        "admin": {"quality", "logistics", "marketing"},
        "quality": {"quality"},
        "logistics": {"logistics"},
        "marketing": {"marketing"},
    }
    return zone in perms.get(role, set())


def access_map(role: str) -> dict[str, bool]:
    return {z: can_access(role, z) for z in ("quality", "logistics", "marketing")}


def pbkdf2_hash(password: str, salt: bytes) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return dk.hex()


@dataclass(frozen=True)
class User:
    username: str
    name: str
    role: str
    salt_hex: str
    pw_hash_hex: str

    def verify_password(self, password: str) -> bool:
        salt = bytes.fromhex(self.salt_hex)
        check = pbkdf2_hash(password, salt)
        return hmac.compare_digest(check, self.pw_hash_hex)


def make_user(username: str, name: str, role: str, password: str) -> User:
    salt = secrets.token_bytes(16)
    return User(
        username=username,
        name=name,
        role=role,
        salt_hex=salt.hex(),
        pw_hash_hex=pbkdf2_hash(password, salt),
    )


USERS: dict[str, User] = {
    "admin": make_user("admin", "Admin User", "admin", "Admin@123"),
    "quality": make_user("quality", "Quality Team", "quality", "Quality@123"),
    "logistics": make_user("logistics", "Logistics Team", "logistics", "Logistics@123"),
    "marketing": make_user("marketing", "Marketing Team", "marketing", "Marketing@123"),
}


SESSIONS: dict[str, dict[str, Any]] = {}
SESSION_TTL_SECONDS = 60 * 60 * 12  # 12 hours


def new_token() -> str:
    return secrets.token_urlsafe(24)


def session_get(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    session = SESSIONS.get(token)
    if not session:
        return None
    if session["exp"] < time.time():
        SESSIONS.pop(token, None)
        return None
    return session


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "SampleTrackingV2/1.0"

    def log_message(self, fmt: str, *args) -> None:  # quiet
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.handle_api_get(parsed)
            return
        self.serve_static(parsed.path, head_only=False)

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.serve_static(parsed.path, head_only=True)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = self.read_json()
        if body is None:
            return
        self.handle_api_post(parsed.path, body)

    def do_PATCH(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = self.read_json()
        if body is None:
            return
        self.handle_api_patch(parsed.path, body)

    def read_json(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            self.send_json({"error": "Invalid JSON"}, HTTPStatus.BAD_REQUEST)
            return None

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def serve_static(self, path: str, *, head_only: bool) -> None:
        target = "index.html" if path in ("", "/") else path.lstrip("/")
        file_path = (STATIC_DIR / target).resolve()
        if STATIC_DIR.resolve() not in file_path.parents and file_path != STATIC_DIR.resolve():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
        }.get(file_path.suffix, "application/octet-stream")
        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def auth_session(self) -> dict[str, Any] | None:
        token = (self.headers.get("X-Auth-Token") or "").strip()
        return session_get(token)

    def require_auth(self) -> dict[str, Any] | None:
        session = self.auth_session()
        if session:
            return session
        self.send_json({"error": "Authentication required"}, HTTPStatus.UNAUTHORIZED)
        return None

    def require_zone(self, session: dict[str, Any], zone: str) -> bool:
        if can_access(session["role"], zone):
            return True
        self.send_json({"error": f"{session['role'].title()} role cannot access {zone}"}, HTTPStatus.FORBIDDEN)
        return False

    def handle_api_get(self, parsed) -> None:
        if parsed.path == "/api/health":
            self.send_json({"ok": True, "ts": utc_now_iso()})
            return

        session = self.require_auth()
        if not session:
            return

        qs = parse_qs(parsed.query)
        role = session["role"]

        if parsed.path == "/api/me":
            self.send_json({"user": {"username": session["username"], "name": session["name"]}, "role": role, "access": access_map(role)})
            return

        with get_connection() as conn:
            if parsed.path == "/api/dashboard":
                lots = []
                inventory = []
                marketing = []
                if can_access(role, "quality"):
                    lots = query_all(
                        conn,
                        """
                        SELECT
                          l.*,
                          COALESCE(COUNT(DISTINCT a.id), 0) AS analysis_count,
                          COALESCE(COUNT(DISTINCT d.id), 0) AS shipment_count
                        FROM lots l
                        LEFT JOIN analyses a ON a.lot_id = l.id
                        LEFT JOIN dispatches d ON d.lot_id = l.id
                        GROUP BY l.id
                        ORDER BY datetime(l.created_at) DESC
                        """,
                    )
                if can_access(role, "logistics"):
                    inventory = query_all(conn, "SELECT * FROM lots WHERE status != 'Closed' ORDER BY datetime(created_at) DESC")
                if can_access(role, "marketing"):
                    marketing = query_all(
                        conn,
                        """
                        SELECT
                          d.id AS dispatch_id,
                          d.customer_name,
                          d.courier_name,
                          d.dispatch_date,
                          l.lot_number,
                          l.product_name,
                          d.delivery_status,
                          f.id AS feedback_id,
                          f.rating,
                          f.action_required,
                          f.next_steps,
                          f.marketing_person
                        FROM dispatches d
                        JOIN lots l ON l.id = d.lot_id
                        LEFT JOIN feedback f ON f.dispatch_id = d.id
                        WHERE d.delivery_status = 'Delivered'
                        ORDER BY date(d.dispatch_date) DESC, d.id DESC
                        """,
                    )
                metrics = {
                    "totalLots": conn.execute("SELECT COUNT(*) FROM lots").fetchone()[0] if can_access(role, "quality") else None,
                    "openLots": conn.execute("SELECT COUNT(*) FROM lots WHERE status != 'Closed'").fetchone()[0] if can_access(role, "logistics") else None,
                    "deliveredShipments": conn.execute("SELECT COUNT(*) FROM dispatches WHERE delivery_status = 'Delivered'").fetchone()[0] if can_access(role, "marketing") else None,
                    "feedbackPending": conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM dispatches d
                        LEFT JOIN feedback f ON f.dispatch_id = d.id
                        WHERE d.delivery_status = 'Delivered' AND f.id IS NULL
                        """
                    ).fetchone()[0]
                    if can_access(role, "marketing")
                    else None,
                }
                self.send_json(
                    {
                        "user": {"username": session["username"], "name": session["name"]},
                        "role": role,
                        "access": access_map(role),
                        "metrics": metrics,
                        "lots": lots,
                        "inventory": inventory,
                        "marketing": marketing,
                    }
                )
                return

            if parsed.path == "/api/analyses":
                if not self.require_zone(session, "quality"):
                    return
                lot_id = int(qs.get("lot_id", ["0"])[0] or 0)
                self.send_json(query_all(conn, "SELECT * FROM analyses WHERE lot_id = ? ORDER BY date(test_date) DESC, id DESC", (lot_id,)))
                return

            if parsed.path == "/api/dispatches":
                if not self.require_zone(session, "logistics"):
                    return
                lot_id = int(qs.get("lot_id", ["0"])[0] or 0)
                self.send_json(query_all(conn, "SELECT * FROM dispatches WHERE lot_id = ? ORDER BY date(dispatch_date) DESC, id DESC", (lot_id,)))
                return

            if parsed.path == "/api/feedback":
                if not self.require_zone(session, "marketing"):
                    return
                dispatch_id = int(qs.get("dispatch_id", ["0"])[0] or 0)
                self.send_json(query_one(conn, "SELECT * FROM feedback WHERE dispatch_id = ?", (dispatch_id,)) or {})
                return

        self.send_error(HTTPStatus.NOT_FOUND)

    def handle_api_post(self, path: str, body: dict[str, Any]) -> None:
        if path == "/api/login":
            username = (body.get("username") or "").strip().lower()
            password = body.get("password") or ""
            user = USERS.get(username)
            if not user or not user.verify_password(password):
                self.send_json({"error": "Invalid username or password"}, HTTPStatus.UNAUTHORIZED)
                return
            token = new_token()
            SESSIONS[token] = {
                "username": user.username,
                "name": user.name,
                "role": user.role,
                "exp": time.time() + SESSION_TTL_SECONDS,
            }
            self.send_json({"token": token, "user": {"username": user.username, "name": user.name}, "role": user.role, "access": access_map(user.role)})
            return

        session = self.require_auth()
        if not session:
            return

        if path == "/api/logout":
            token = (self.headers.get("X-Auth-Token") or "").strip()
            if token:
                SESSIONS.pop(token, None)
            self.send_json({"ok": True})
            return

        with get_connection() as conn:
            if path == "/api/lots":
                if not self.require_zone(session, "quality"):
                    return
                required = ["lot_number", "product_name", "initial_quantity", "unit_measure"]
                missing = [f for f in required if str(body.get(f, "")).strip() == ""]
                if missing:
                    self.send_json({"error": f"Missing fields: {', '.join(missing)}"}, HTTPStatus.BAD_REQUEST)
                    return
                cur = conn.execute(
                    """
                    INSERT INTO lots (lot_number, product_name, initial_quantity, unit_measure, npd_project_ref, notes, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(body["lot_number"]).strip(),
                        str(body["product_name"]).strip(),
                        float(body["initial_quantity"]),
                        str(body["unit_measure"]).strip(),
                        str(body.get("npd_project_ref", "")).strip(),
                        str(body.get("notes", "")).strip(),
                        str(body.get("status", "Draft")).strip() or "Draft",
                    ),
                )
                self.send_json(query_one(conn, "SELECT * FROM lots WHERE id = ?", (cur.lastrowid,)) or {}, HTTPStatus.CREATED)
                return

            if path == "/api/analyses":
                if not self.require_zone(session, "quality"):
                    return
                cur = conn.execute(
                    """
                    INSERT INTO analyses (lot_id, test_type, spec_value, result_value, is_pass, analyst_name, test_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(body["lot_id"]),
                        str(body["test_type"]).strip(),
                        str(body["spec_value"]).strip(),
                        str(body["result_value"]).strip(),
                        1 if body.get("is_pass") else 0,
                        str(body.get("analyst_name") or session["name"]).strip() or session["name"],
                        str(body.get("test_date") or utc_date()),
                    ),
                )
                self.send_json(query_one(conn, "SELECT * FROM analyses WHERE id = ?", (cur.lastrowid,)) or {}, HTTPStatus.CREATED)
                return

            if path == "/api/dispatches":
                if not self.require_zone(session, "logistics"):
                    return
                cur = conn.execute(
                    """
                    INSERT INTO dispatches (lot_id, customer_name, quantity_sent, courier_name, awb_number, dispatch_date, delivery_status)
                    VALUES (?, ?, ?, ?, ?, ?, 'Dispatched')
                    """,
                    (
                        int(body["lot_id"]),
                        str(body["customer_name"]).strip(),
                        float(body["quantity_sent"]),
                        str(body["courier_name"]).strip(),
                        str(body["awb_number"]).strip(),
                        str(body["dispatch_date"]).strip(),
                    ),
                )
                self.send_json(query_one(conn, "SELECT * FROM dispatches WHERE id = ?", (cur.lastrowid,)) or {}, HTTPStatus.CREATED)
                return

            if path == "/api/feedback":
                if not self.require_zone(session, "marketing"):
                    return
                cur = conn.execute(
                    """
                    INSERT INTO feedback (dispatch_id, rating, technical_notes, action_required, next_steps, marketing_person, feedback_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(body["dispatch_id"]),
                        float(body["rating"]),
                        str(body["technical_notes"]).strip(),
                        1 if body.get("action_required") else 0,
                        str(body.get("next_steps", "")).strip(),
                        str(body.get("marketing_person") or session["name"]).strip() or session["name"],
                        str(body.get("feedback_date") or utc_date()),
                    ),
                )
                self.send_json(query_one(conn, "SELECT * FROM feedback WHERE id = ?", (cur.lastrowid,)) or {}, HTTPStatus.CREATED)
                return

        self.send_error(HTTPStatus.NOT_FOUND)

    def handle_api_patch(self, path: str, body: dict[str, Any]) -> None:
        session = self.require_auth()
        if not session:
            return
        if path != "/api/dispatch-status":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not self.require_zone(session, "logistics"):
            return
        with get_connection() as conn:
            conn.execute("UPDATE dispatches SET delivery_status = ? WHERE id = ?", (str(body["delivery_status"]).strip(), int(body["dispatch_id"])))
            self.send_json(query_one(conn, "SELECT * FROM dispatches WHERE id = ?", (int(body["dispatch_id"]),)) or {})


def main() -> None:
    init_db()
    port = 8010
    print(f"Sample Tracking v2 running on http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()

