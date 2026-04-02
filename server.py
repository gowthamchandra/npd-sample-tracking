#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).parent
STATIC_DIR = ROOT / "static"
DB_PATH = ROOT / "sample_tracking.db"
ROLES = {"admin", "quality", "logistics", "marketing"}
USERS = {
    "admin": {"password": "Admin@123", "role": "admin", "name": "Admin User"},
    "quality": {"password": "Quality@123", "role": "quality", "name": "Quality Team"},
    "logistics": {"password": "Logistics@123", "role": "logistics", "name": "Logistics Team"},
    "marketing": {"password": "Marketing@123", "role": "marketing", "name": "Marketing Team"},
}
SESSIONS: dict[str, dict] = {}


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


def should_seed_demo_data() -> bool:
    value = (os.getenv("SEED_DEMO_DATA") or "").strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


SEED: list[tuple[str, list[tuple]]] = []


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    should_seed = not DB_PATH.exists()
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        if should_seed and should_seed_demo_data() and SEED:
            for sql, rows in SEED:
                conn.executemany(sql, rows)


def query_all(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def query_one(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> dict | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def normalize_role(value: str | None) -> str:
    role = (value or "admin").strip().lower()
    return role if role in ROLES else "admin"


def can_access(role: str, zone: str) -> bool:
    permissions = {
        "admin": {"quality", "logistics", "marketing"},
        "quality": {"quality"},
        "logistics": {"logistics"},
        "marketing": {"marketing"},
    }
    return zone in permissions.get(role, set())


class AppHandler(BaseHTTPRequestHandler):
    server_version = "SampleTracking/1.0"

    @property
    def current_role(self) -> str:
        return normalize_role((self.current_user or {}).get("role"))

    @property
    def current_user(self) -> dict | None:
        token = self.headers.get("X-Auth-Token", "").strip()
        if not token:
            return None
        return SESSIONS.get(token)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            if not self.require_auth():
                return
            self.handle_api_get(parsed)
            return
        self.serve_static(parsed.path)

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            # For simplicity, treat HEAD like GET for auth/errors.
            if not self.require_auth():
                return
            self.send_response(HTTPStatus.OK)
            self.end_headers()
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
        if parsed.path not in {"/api/login"} and not self.require_auth():
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
        if not self.require_auth():
            return
        self.handle_api_patch(parsed.path, body)

    def log_message(self, fmt: str, *args) -> None:
        return

    def read_json(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            self.send_json({"error": "Invalid JSON payload"}, HTTPStatus.BAD_REQUEST)
            return None

    def send_json(self, payload: dict | list, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def require_zone(self, zone: str) -> bool:
        if can_access(self.current_role, zone):
            return True
        self.send_json({"error": f"{self.current_role.title()} role cannot access {zone} operations"}, HTTPStatus.FORBIDDEN)
        return False

    def require_auth(self) -> bool:
        if self.current_user:
            return True
        self.send_json({"error": "Authentication required"}, HTTPStatus.UNAUTHORIZED)
        return False

    def serve_static(self, path: str, head_only: bool = False) -> None:
        target = "index.html" if path in ("/", "") else path.lstrip("/")
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
            ".json": "application/json; charset=utf-8",
        }.get(file_path.suffix, "application/octet-stream")
        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        # Avoid stale bundles during development; also makes debugging user reports easier.
        if file_path.suffix in {".html", ".css", ".js"}:
            self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def handle_api_get(self, parsed) -> None:
        qs = parse_qs(parsed.query)
        role = self.current_role
        with get_connection() as conn:
            if parsed.path == "/api/session":
                user = self.current_user
                self.send_json(
                    {
                        "user": {"username": user["username"], "name": user["name"]},
                        "role": role,
                        "access": {
                            "quality": can_access(role, "quality"),
                            "logistics": can_access(role, "logistics"),
                            "marketing": can_access(role, "marketing"),
                        },
                    }
                )
                return
            if parsed.path == "/api/dashboard":
                user = self.current_user
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
                ) if can_access(role, "quality") else []
                inventory = query_all(
                    conn,
                    "SELECT * FROM lots WHERE status != 'Closed' ORDER BY datetime(created_at) DESC",
                ) if can_access(role, "logistics") else []
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
                ) if can_access(role, "marketing") else []
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
                    ).fetchone()[0] if can_access(role, "marketing") else None,
                }
                self.send_json(
                    {
                        "user": {"username": user["username"], "name": user["name"]},
                        "role": role,
                        "access": {
                            "quality": can_access(role, "quality"),
                            "logistics": can_access(role, "logistics"),
                            "marketing": can_access(role, "marketing"),
                        },
                        "metrics": metrics,
                        "lots": lots,
                        "inventory": inventory,
                        "marketing": marketing,
                    }
                )
                return

            if parsed.path == "/api/analyses":
                if not self.require_zone("quality"):
                    return
                lot_id = int(qs.get("lot_id", ["0"])[0] or 0)
                data = query_all(conn, "SELECT * FROM analyses WHERE lot_id = ? ORDER BY date(test_date) DESC, id DESC", (lot_id,))
                self.send_json(data)
                return

            if parsed.path == "/api/dispatches":
                if not self.require_zone("logistics"):
                    return
                lot_id = int(qs.get("lot_id", ["0"])[0] or 0)
                data = query_all(conn, "SELECT * FROM dispatches WHERE lot_id = ? ORDER BY date(dispatch_date) DESC, id DESC", (lot_id,))
                self.send_json(data)
                return

            if parsed.path == "/api/feedback":
                if not self.require_zone("marketing"):
                    return
                dispatch_id = int(qs.get("dispatch_id", ["0"])[0] or 0)
                data = query_one(conn, "SELECT * FROM feedback WHERE dispatch_id = ?", (dispatch_id,))
                self.send_json(data or {})
                return

        self.send_error(HTTPStatus.NOT_FOUND)

    def handle_api_post(self, path: str, body: dict) -> None:
        if path == "/api/login":
            username = (body.get("username") or "").strip().lower()
            password = body.get("password") or ""
            user = USERS.get(username)
            if not user or user["password"] != password:
                self.send_json({"error": "Invalid username or password"}, HTTPStatus.UNAUTHORIZED)
                return
            token = secrets.token_urlsafe(24)
            SESSIONS[token] = {
                "username": username,
                "name": user["name"],
                "role": user["role"],
            }
            self.send_json(
                {
                    "token": token,
                    "user": {"username": username, "name": user["name"]},
                    "role": user["role"],
                    "access": {
                        "quality": can_access(user["role"], "quality"),
                        "logistics": can_access(user["role"], "logistics"),
                        "marketing": can_access(user["role"], "marketing"),
                    },
                }
            )
            return

        if path == "/api/logout":
            token = self.headers.get("X-Auth-Token", "").strip()
            if token:
                SESSIONS.pop(token, None)
            self.send_json({"ok": True})
            return

        with get_connection() as conn:
            if path == "/api/lots":
                if not self.require_zone("quality"):
                    return
                required = ["lot_number", "product_name", "initial_quantity", "unit_measure"]
                missing = [field for field in required if str(body.get(field, "")).strip() == ""]
                if missing:
                    self.send_json({"error": f"Missing fields: {', '.join(missing)}"}, HTTPStatus.BAD_REQUEST)
                    return
                cursor = conn.execute(
                    """
                    INSERT INTO lots (lot_number, product_name, initial_quantity, unit_measure, npd_project_ref, notes, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        body["lot_number"].strip(),
                        body["product_name"].strip(),
                        float(body["initial_quantity"]),
                        body["unit_measure"].strip(),
                        body.get("npd_project_ref", "").strip(),
                        body.get("notes", "").strip(),
                        body.get("status", "Draft").strip() or "Draft",
                    ),
                )
                record = query_one(conn, "SELECT * FROM lots WHERE id = ?", (cursor.lastrowid,))
                self.send_json(record or {}, HTTPStatus.CREATED)
                return

            if path == "/api/analyses":
                if not self.require_zone("quality"):
                    return
                cursor = conn.execute(
                    """
                    INSERT INTO analyses (lot_id, test_type, spec_value, result_value, is_pass, analyst_name, test_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(body["lot_id"]),
                        body["test_type"].strip(),
                        body["spec_value"].strip(),
                        body["result_value"].strip(),
                        1 if body.get("is_pass") else 0,
                        body.get("analyst_name", "Lab Team").strip() or "Lab Team",
                        body.get("test_date") or now_iso()[:10],
                    ),
                )
                record = query_one(conn, "SELECT * FROM analyses WHERE id = ?", (cursor.lastrowid,))
                self.send_json(record or {}, HTTPStatus.CREATED)
                return

            if path == "/api/dispatches":
                if not self.require_zone("logistics"):
                    return
                cursor = conn.execute(
                    """
                    INSERT INTO dispatches (lot_id, customer_name, quantity_sent, courier_name, awb_number, dispatch_date, delivery_status)
                    VALUES (?, ?, ?, ?, ?, ?, 'Dispatched')
                    """,
                    (
                        int(body["lot_id"]),
                        body["customer_name"].strip(),
                        float(body["quantity_sent"]),
                        body["courier_name"].strip(),
                        body["awb_number"].strip(),
                        body["dispatch_date"],
                    ),
                )
                record = query_one(conn, "SELECT * FROM dispatches WHERE id = ?", (cursor.lastrowid,))
                self.send_json(record or {}, HTTPStatus.CREATED)
                return

            if path == "/api/feedback":
                if not self.require_zone("marketing"):
                    return
                cursor = conn.execute(
                    """
                    INSERT INTO feedback (dispatch_id, rating, technical_notes, action_required, next_steps, marketing_person, feedback_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(body["dispatch_id"]),
                        float(body["rating"]),
                        body["technical_notes"].strip(),
                        1 if body.get("action_required") else 0,
                        body.get("next_steps", "").strip(),
                        body["marketing_person"].strip(),
                        body.get("feedback_date") or now_iso()[:10],
                    ),
                )
                record = query_one(conn, "SELECT * FROM feedback WHERE id = ?", (cursor.lastrowid,))
                self.send_json(record or {}, HTTPStatus.CREATED)
                return

        self.send_error(HTTPStatus.NOT_FOUND)

    def handle_api_patch(self, path: str, body: dict) -> None:
        if path != "/api/dispatch-status":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not self.require_zone("logistics"):
            return
        with get_connection() as conn:
            conn.execute(
                "UPDATE dispatches SET delivery_status = ? WHERE id = ?",
                (body["delivery_status"].strip(), int(body["dispatch_id"])),
            )
            record = query_one(conn, "SELECT * FROM dispatches WHERE id = ?", (int(body["dispatch_id"]),))
            self.send_json(record or {})


def main() -> None:
    global DB_PATH
    # Allow cloud hosts to mount a persistent volume and point SQLite there.
    db_override = (os.getenv("SAMPLE_TRACKING_DB_PATH") or "").strip()
    if db_override:
        DB_PATH = Path(db_override)
    init_db()
    host = (os.getenv("HOST") or "0.0.0.0").strip()
    port = int(os.getenv("PORT") or "8000")
    print(f"Sample Tracking app running on http://{host}:{port}")
    ThreadingHTTPServer((host, port), AppHandler).serve_forever()


if __name__ == "__main__":
    main()
