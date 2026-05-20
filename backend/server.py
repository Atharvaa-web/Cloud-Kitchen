#!/usr/bin/env python3
"""
Cloud Kitchen Operations Backend
Pure Python stdlib — no external dependencies.
Run: python3 server.py
"""
import sqlite3
import json
import uuid
import datetime
import math
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

DB_PATH = "kitchen.db"
import os
PORT = int(os.environ.get("PORT", 8000))
# ─────────────────────────────────────────────
#  CONSTANTS & CONFIG
# ─────────────────────────────────────────────

STATIONS = {
    "tandoor":      {"capacity": 1},
    "burner":       {"capacity": 2},
    "fryer":        {"capacity": 1},
    "cold_station": {"capacity": 1},
}

MENU = [
    {"id": "dish_1",  "name": "Chicken Biryani",    "prep_time": 25, "station": "tandoor"},
    {"id": "dish_2",  "name": "Paneer Tikka",       "prep_time": 18, "station": "tandoor"},
    {"id": "dish_3",  "name": "Dal Makhani",        "prep_time": 20, "station": "burner"},
    {"id": "dish_4",  "name": "Veg Wrap",           "prep_time":  6, "station": "burner"},
    {"id": "dish_5",  "name": "Chicken Wrap",       "prep_time":  8, "station": "burner"},
    {"id": "dish_6",  "name": "Samosa",             "prep_time": 10, "station": "fryer"},
    {"id": "dish_7",  "name": "Chicken 65",         "prep_time": 14, "station": "fryer"},
    {"id": "dish_8",  "name": "Raita",              "prep_time":  3, "station": "cold_station"},
    {"id": "dish_9",  "name": "Mango Lassi",        "prep_time":  4, "station": "cold_station"},
    {"id": "dish_10", "name": "Butter Chicken",     "prep_time": 22, "station": "burner"},
]

# Zone SLA: minutes after order_placed by which the full order should be READY
ZONE_SLA = {
    "zone_1": 35,  # nearby — 35 min total
    "zone_2": 45,  # mid-distance
    "zone_3": 55,  # far
}

MENU_BY_ID = {d["id"]: d for d in MENU}


# ─────────────────────────────────────────────
#  DATABASE SETUP
# ─────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    # NOTE: existing DBs may already have the base schema; we still run ALTER TABLE safely.
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS orders (
        id          TEXT PRIMARY KEY,
        customer    TEXT NOT NULL,
        zone        TEXT NOT NULL,
        status      TEXT NOT NULL DEFAULT 'pending',
        ordered_at  TEXT NOT NULL,
        deadline_at TEXT,
        est_ready_at TEXT,
        at_risk     INTEGER DEFAULT 0
    );

    -- Stage timestamps for real-time automation (added via ALTER TABLE if missing)
    CREATE TABLE IF NOT EXISTS order_items (
        id          TEXT PRIMARY KEY,
        order_id    TEXT NOT NULL REFERENCES orders(id),
        dish_id     TEXT NOT NULL,
        dish_name   TEXT NOT NULL,
        station     TEXT NOT NULL,
        prep_time   INTEGER NOT NULL,
        state       TEXT NOT NULL DEFAULT 'queued',
        started_at  TEXT,
        done_at     TEXT,
        sched_start TEXT,
        sched_end   TEXT,
        queue_pos   INTEGER DEFAULT 999
    );

    CREATE INDEX IF NOT EXISTS idx_items_order  ON order_items(order_id);
    CREATE INDEX IF NOT EXISTS idx_items_state  ON order_items(state);
    CREATE INDEX IF NOT EXISTS idx_items_station ON order_items(station);
    CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
    """)

    # Add columns if they don't exist (SQLite doesn't support IF NOT EXISTS for ADD COLUMN)
    def ensure_col(table, col, ddl):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

    ensure_col("orders", "accepted_at", "accepted_at TEXT")
    ensure_col("orders", "cooking_done_at", "cooking_done_at TEXT")
    ensure_col("orders", "packed_at", "packed_at TEXT")
    ensure_col("orders", "served_at", "served_at TEXT")
    ensure_col("orders", "completed_at", "completed_at TEXT")
    ensure_col("orders", "stage_anchor_at", "stage_anchor_at TEXT")

    conn.commit()
    conn.close()




# ─────────────────────────────────────────────
#  SEED DATA
# ─────────────────────────────────────────────

SAMPLE_ORDERS = [
    {"customer": "Arjun Mehta",      "zone": "zone_1", "items": ["dish_1", "dish_8"],         "mins_ago": 5},
    {"customer": "Priya Sharma",     "zone": "zone_2", "items": ["dish_3", "dish_4"],         "mins_ago": 8},
    {"customer": "Rohan Verma",      "zone": "zone_1", "items": ["dish_6", "dish_9"],         "mins_ago": 3},
    {"customer": "Ananya Iyer",      "zone": "zone_3", "items": ["dish_2", "dish_7", "dish_8"], "mins_ago": 12},
    {"customer": "Karan Patel",      "zone": "zone_2", "items": ["dish_10"],                  "mins_ago": 15},
    {"customer": "Sneha Nair",       "zone": "zone_1", "items": ["dish_5", "dish_9"],         "mins_ago": 2},
    {"customer": "Vikram Rao",       "zone": "zone_3", "items": ["dish_1", "dish_6"],         "mins_ago": 20},
    {"customer": "Divya Singh",      "zone": "zone_2", "items": ["dish_4", "dish_4"],         "mins_ago": 7},
    {"customer": "Aditya Kumar",     "zone": "zone_1", "items": ["dish_7", "dish_8", "dish_9"], "mins_ago": 10},
    {"customer": "Pooja Desai",      "zone": "zone_3", "items": ["dish_3", "dish_2"],         "mins_ago": 18},
    {"customer": "Raj Malhotra",     "zone": "zone_2", "items": ["dish_6"],                   "mins_ago": 1},
    {"customer": "Meera Krishnan",   "zone": "zone_1", "items": ["dish_10", "dish_4", "dish_9"], "mins_ago": 6},
    {"customer": "Suresh Gupta",     "zone": "zone_3", "items": ["dish_1"],                   "mins_ago": 25},
    {"customer": "Asha Bose",        "zone": "zone_2", "items": ["dish_5", "dish_7"],         "mins_ago": 4},
    {"customer": "Nikhil Joshi",     "zone": "zone_1", "items": ["dish_2", "dish_8"],         "mins_ago": 9},
    {"customer": "Kavya Reddy",      "zone": "zone_2", "items": ["dish_3", "dish_6", "dish_9"], "mins_ago": 14},
    {"customer": "Sanjay Tiwari",    "zone": "zone_3", "items": ["dish_10", "dish_7"],        "mins_ago": 22},
    {"customer": "Lakshmi Pillai",   "zone": "zone_1", "items": ["dish_4", "dish_8"],         "mins_ago": 11},
]


def seed_db():
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    if count > 0:
        conn.close()
        return
    now = datetime.datetime.utcnow()
    for s in SAMPLE_ORDERS:
        order_id = "ORD-" + str(uuid.uuid4())[:8].upper()
        ordered_at = (now - datetime.timedelta(minutes=s["mins_ago"])).isoformat() + "Z"
        deadline_at = (now - datetime.timedelta(minutes=s["mins_ago"]) +
                       datetime.timedelta(minutes=ZONE_SLA[s["zone"]])).isoformat() + "Z"
        conn.execute(
            "INSERT INTO orders(id,customer,zone,status,ordered_at,deadline_at) VALUES(?,?,?,?,?,?)",
            (order_id, s["customer"], s["zone"], "pending", ordered_at, deadline_at)
        )
        for dish_id in s["items"]:
            dish = MENU_BY_ID[dish_id]
            item_id = "ITM-" + str(uuid.uuid4())[:8].upper()
            conn.execute(
                """INSERT INTO order_items(id,order_id,dish_id,dish_name,station,prep_time,state)
                   VALUES(?,?,?,?,?,?,?)""",
                (item_id, order_id, dish_id, dish["name"], dish["station"], dish["prep_time"], "queued")
            )
    conn.commit()
    conn.close()
    print(f"[seed] Seeded {len(SAMPLE_ORDERS)} orders")


# ─────────────────────────────────────────────
#  SCHEDULER ENGINE
# ─────────────────────────────────────────────
#
#  Algorithm: Hybrid EDF (Earliest Deadline First) + Station-aware slot assignment
#  + Backward-synchronisation for multi-item orders.
#
#  Step 1: Compute deadline for every pending order (ordered_at + zone SLA).
#  Step 2: Sort pending orders by deadline ascending (soonest deadline first = EDF).
#  Step 3: For each order, find the LONGEST item (by prep_time). That sets the
#          "anchor" — everything in the order needs to finish when the anchor finishes.
#  Step 4: Assign station slots respecting capacity. Each station has a "next free at"
#          pointer. An item's sched_start = max(now, station_next_free).
#  Step 5: For multi-item orders, shorter items get a DELAYED start so they finish
#          at the same time as the longest item (backward scheduling).
#  Step 6: Order est_ready_at = max(sched_end) across all its items.
#  Step 7: at_risk = est_ready_at > deadline_at.

def run_scheduler():
    conn = get_db()
    now = datetime.datetime.utcnow()

    # Station free-at pointers (minutes from now)
    station_free = {s: 0 for s in STATIONS}

    # Respect currently COOKING items — they hold a station slot
    cooking = conn.execute(
        "SELECT station, started_at, prep_time FROM order_items WHERE state='cooking'"
    ).fetchall()
    for item in cooking:
        if item["started_at"]:
            started = datetime.datetime.fromisoformat(item["started_at"].replace("Z", ""))
            elapsed = (now - started).total_seconds() / 60
            remaining = max(0, item["prep_time"] - elapsed)
            # For burner (capacity 2) we track how many slots are taken
            station_free[item["station"]] = max(station_free[item["station"]], remaining)

    # Fetch all pending orders with their items
    pending_orders = conn.execute(
        "SELECT * FROM orders WHERE status='pending' ORDER BY deadline_at ASC"
    ).fetchall()

    for order in pending_orders:
        items = conn.execute(
            "SELECT * FROM order_items WHERE order_id=? AND state='queued'",
            (order["id"],)
        ).fetchall()
        if not items:
            continue

        # Find anchor: longest prep_time item
        anchor = max(items, key=lambda i: i["prep_time"])

        # Compute earliest possible finish for anchor based on station queue
        anchor_station = anchor["station"]
        anchor_start_min = station_free[anchor_station]
        anchor_finish_min = anchor_start_min + anchor["prep_time"]

        # Advance the station pointer
        cap = STATIONS[anchor_station]["capacity"]
        if cap == 1:
            station_free[anchor_station] = anchor_finish_min
        else:
            # For burner (cap=2): simple approach — use the slot that's free soonest
            station_free[anchor_station] = anchor_finish_min

        # Schedule all items in this order using backward sync
        order_finish_min = anchor_finish_min
        for item in items:
            if item["id"] == anchor["id"]:
                item_start_min = anchor_start_min
                item_finish_min = anchor_finish_min
            else:
                # Non-anchor items: start LATE enough to finish at order_finish_min
                # But also respect station availability
                desired_start = order_finish_min - item["prep_time"]
                station_available = station_free[item["station"]]
                item_start_min = max(desired_start, station_available)
                item_finish_min = item_start_min + item["prep_time"]
                # Advance this station too
                station_free[item["station"]] = item_finish_min

            sched_start = (now + datetime.timedelta(minutes=item_start_min)).isoformat() + "Z"
            sched_end   = (now + datetime.timedelta(minutes=item_finish_min)).isoformat() + "Z"
            conn.execute(
                "UPDATE order_items SET sched_start=?, sched_end=? WHERE id=?",
                (sched_start, sched_end, item["id"])
            )

        # Order est_ready_at = latest sched_end across all items
        est_ready_at = (now + datetime.timedelta(minutes=order_finish_min)).isoformat() + "Z"

        # at_risk: est_ready_at > deadline_at
        deadline = datetime.datetime.fromisoformat(order["deadline_at"].replace("Z", ""))
        est_ready = now + datetime.timedelta(minutes=order_finish_min)
        at_risk = 1 if est_ready > deadline else 0

        conn.execute(
            "UPDATE orders SET est_ready_at=?, at_risk=? WHERE id=?",
            (est_ready_at, at_risk, order["id"])
        )

    conn.commit()
    conn.close()


# ─────────────────────────────────────────────
#  ORDER STATUS BUILDER  (for GET /api/orders/status)
# ─────────────────────────────────────────────

# Real-time automation settings (seconds)
# Prep/cooking time per item shown on the Agents page.
STAGE_PREP_S = 30
STAGE_PACK_S = 20
STAGE_SERVE_S = 20
STAGE_TOTAL_S = STAGE_PREP_S + STAGE_PACK_S + STAGE_SERVE_S


def _parse_utc(iso):
    if not iso:
        return None
    return datetime.datetime.fromisoformat(iso.replace("Z", ""))

def _now_utc():
    return datetime.datetime.utcnow()

def _ensure_stage_anchor(conn, order_id, ordered_at):
    # stage_anchor_at is the definitive t0 for the stage timeline
    stage_anchor = conn.execute("SELECT stage_anchor_at FROM orders WHERE id=?", (order_id,)).fetchone()[0]
    if stage_anchor:
        return _parse_utc(stage_anchor)
    conn.execute(
        "UPDATE orders SET stage_anchor_at=? WHERE id=?",
        (ordered_at.isoformat() + "Z", order_id)
    )
    conn.commit()
    return ordered_at

def run_realtime_automation_step():
    """Advance all non-completed orders through: prepare->packing->serving->completed.

    This is driven by stage_anchor_at/ordered_at and fixed 20s per stage.
    """
    conn = get_db()
    now = _now_utc()
    # Avoid crashing the UI when multiple requests hit the DB.
    conn.execute("PRAGMA busy_timeout=3000")


    # Select orders that are not completed and have at least one item
    open_orders = conn.execute(
        """SELECT o.*
           FROM orders o
           WHERE o.status!='completed'"""
    ).fetchall()

    for o in open_orders:
        order_id = o["id"]
        ordered_at = _parse_utc(o["ordered_at"])
        if not ordered_at:
            continue

        stage_anchor = _ensure_stage_anchor(conn, order_id, ordered_at)
        t_ready = stage_anchor + datetime.timedelta(seconds=STAGE_TOTAL_S)

        # at_risk: compare estimated ready time vs deadline
        deadline = _parse_utc(o["deadline_at"])
        if deadline:
            at_risk = 1 if t_ready > deadline else 0
            conn.execute("UPDATE orders SET at_risk=? WHERE id=?", (at_risk, order_id))

        # Determine stage based on elapsed
        elapsed_s = (now - stage_anchor).total_seconds()

        # Stage 1: PREP (items queued->cooking->done)
        if elapsed_s < STAGE_PREP_S:
            # accepted stage; cooking should be running after immediate start
            # For simplicity + real-time: once order appears, we start cooking immediately.
            if o["status"] != "accepted":
                conn.execute("UPDATE orders SET status='accepted', accepted_at=? WHERE id=?", (now.isoformat()+"Z", order_id))

            conn.execute(
                """UPDATE order_items
                   SET state='cooking', started_at=COALESCE(started_at, ?)
                   WHERE order_id=? AND state='queued'""",
                (now.isoformat()+"Z", order_id)
            )

        # Stage 2 boundary: cooking done exactly at +20s
        elif elapsed_s < STAGE_PREP_S + STAGE_PACK_S:
            # Ensure cooking done and order moves to packing
            conn.execute(
                "UPDATE order_items SET state='done', done_at=COALESCE(done_at, ?) WHERE order_id=? AND state!='done'",
                ( (stage_anchor + datetime.timedelta(seconds=STAGE_PREP_S)).isoformat()+"Z", order_id)
            )

            if o["status"] != "packing":
                conn.execute(
                    "UPDATE orders SET status='packing', cooking_done_at=?, packed_at=NULL WHERE id=?",
                    ( (stage_anchor + datetime.timedelta(seconds=STAGE_PREP_S)).isoformat()+"Z", order_id)
                )

            # Mark packed_at when packing stage completes
            # Note: `o` is a sqlite3.Row, so use indexing not .get().
            if o["packed_at"] is None and elapsed_s >= STAGE_PREP_S + STAGE_PACK_S:
                pass


        # Stage 3: SERVE window (+20s packing +20s serve)
        elif elapsed_s < STAGE_TOTAL_S:
            # Mark packed_at when entering serve stage
            packed_done_at = stage_anchor + datetime.timedelta(seconds=STAGE_PREP_S + STAGE_PACK_S)
            conn.execute(
                "UPDATE orders SET packed_at=? WHERE id=? AND packed_at IS NULL",
                (packed_done_at.isoformat()+"Z", order_id)
            )
            # During serving we keep status as packing (UI shows packing badge). We'll complete at end.
            if o["status"] != "packing":
                conn.execute("UPDATE orders SET status='packing' WHERE id=?", (order_id,))

        # Completion
        else:
            completed_at = stage_anchor + datetime.timedelta(seconds=STAGE_TOTAL_S)
            conn.execute(
                """UPDATE orders
                   SET status='completed',
                       served_at=COALESCE(served_at, ?),
                       completed_at=COALESCE(completed_at, ?),
                       at_risk=0
                   WHERE id=?""",
                ( (stage_anchor + datetime.timedelta(seconds=STAGE_PREP_S + STAGE_PACK_S)).isoformat()+"Z",
                  completed_at.isoformat()+"Z",
                  order_id)
            )

    conn.commit()
    conn.close()


# Throttle real-time automation so GET requests don't block UI.
LAST_AUTOMATION_TS = 0.0
AUTOMATION_MIN_INTERVAL_S = 2.0


def build_status():
    # Real-time automation step before rendering.
    # Throttled to keep server responsive (UI polls frequently).
    global LAST_AUTOMATION_TS
    now_ts = datetime.datetime.utcnow().timestamp()
    if (now_ts - LAST_AUTOMATION_TS) >= AUTOMATION_MIN_INTERVAL_S:
        # run_realtime_automation_step() opens/closes its own connection
        run_realtime_automation_step()
        LAST_AUTOMATION_TS = now_ts

    conn = get_db()
    try:
        now = datetime.datetime.utcnow()
        orders = conn.execute("SELECT * FROM orders ORDER BY deadline_at ASC").fetchall()
        result = []
        for order in orders:
            items = conn.execute(
                "SELECT * FROM order_items WHERE order_id=? ORDER BY queue_pos",
                (order["id"],)
            ).fetchall()

            deadline = datetime.datetime.fromisoformat(order["deadline_at"].replace("Z", ""))
            mins_to_deadline = (deadline - now).total_seconds() / 60

            order_dict = dict(order)
            order_dict["items"] = [dict(i) for i in items]
            order_dict["mins_to_deadline"] = round(mins_to_deadline, 1)
            result.append(order_dict)

        return result
    finally:
        conn.close()




def build_station_load():
    conn = get_db()
    try:
        load = {}
        for station in STATIONS:
            cooking = conn.execute(
                """SELECT oi.*, o.customer
                   FROM order_items oi JOIN orders o ON oi.order_id=o.id
                   WHERE oi.station=? AND oi.state='cooking'""",
                (station,)
            ).fetchall()
            queued = conn.execute(
                """SELECT oi.*, o.customer
                   FROM order_items oi JOIN orders o ON oi.order_id=o.id
                   WHERE oi.station=? AND oi.state='queued'
                   ORDER BY oi.sched_start ASC LIMIT 5""",
                (station,)
            ).fetchall()
            load[station] = {
                "capacity": STATIONS[station]["capacity"],
                "cooking": [dict(r) for r in cooking],
                "queued_next": [dict(r) for r in queued],
            }
        return load
    finally:
        conn.close()


def build_completed_orders():
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT o.*
               FROM orders o
               WHERE o.status='completed' AND o.completed_at IS NOT NULL
               ORDER BY o.completed_at DESC"""
        ).fetchall()
        completed = []
        for o in rows:
            items = conn.execute(
                """SELECT dish_id, dish_name, station, prep_time, state
                   FROM order_items
                   WHERE order_id=?
                   ORDER BY dish_name ASC""",
                (o["id"],)
            ).fetchall()
            completed.append({
                **dict(o),
                "items": [dict(i) for i in items],
            })
        return completed
    finally:
        conn.close()


def build_agent_log():
    """Generate agent activity for the UI.

    UI reads dashData.agent_log and renders latest events.
    """
    conn = get_db()
    now = _now_utc()
    # Simple deterministic risk snapshot + counts.
    risk_orders = conn.execute(
        "SELECT id, customer FROM orders WHERE at_risk=1 AND status!='completed' ORDER BY deadline_at ASC LIMIT 3"
    ).fetchall()
    active_items = conn.execute(
        "SELECT COUNT(*) FROM order_items WHERE state='cooking'"
    ).fetchone()[0]
    packing_orders = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE status='packing'"
    ).fetchone()[0]
    completed_cnt = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE status='completed'"
    ).fetchone()[0]
    risk_names = ", ".join([r["customer"] for r in risk_orders]) if risk_orders else "none"

    # Build a short “what is each agent working on” snapshot.
    # We derive it from current order/item states in the DB.
    cooking_rows = conn.execute(


        """SELECT o.id as order_id, o.customer, o.zone, oi.dish_name, oi.station, oi.state
           FROM orders o
           JOIN order_items oi ON oi.order_id=o.id
           WHERE oi.state IN ('cooking','done')
           ORDER BY o.deadline_at ASC, oi.prep_time DESC
           LIMIT 6"""
    ).fetchall()

    cooking_summary = ", ".join([
        f"{r['customer']} ({r['order_id']}) → {r['dish_name']} [{r['station']}]"
        for r in cooking_rows
    ]) if cooking_rows else "No active items." 

    packing_rows = conn.execute(
        """SELECT o.id as order_id, o.customer, o.zone
           FROM orders o WHERE o.status='packing' ORDER BY o.deadline_at ASC LIMIT 3"""
    ).fetchall()

    packing_summary = ", ".join([f"{r['customer']} ({r['order_id']})" for r in packing_rows]) if packing_rows else "No orders in packing." 

    # Note: the UI currently uses a deterministic 4-log payload. We enhance each log with a one-line order detail.
    return [
        {
            "agent": "[Boss Agent]",
            "event": "risk_monitor",
            "detail": f"checked {len(risk_orders)} risky orders: {risk_names}",
            "order_details": cooking_summary
        },
        {
            "agent": "[Order Agent]",
            "event": "order_intake",
            "detail": f"active cooking items: {active_items}",
            "order_details": cooking_summary
        },
        {
            "agent": "[Scheduler Agent]",
            "event": "edf_longest_first",
            "detail": "maintaining deadlines & stage automation",
            "order_details": cooking_summary
        },
        {
            "agent": "[Packing Agent]",
            "event": "auto_packing",
            "detail": f"orders in packing: {packing_orders}; total completed: {completed_cnt}",
            "order_details": packing_summary
        },
    ]



# ─────────────────────────────────────────────
#  HTTP HANDLER
# ─────────────────────────────────────────────

def json_response(handler, data, status=200):
    try:
        body = json.dumps(data, default=str).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
        handler.send_header("Access-Control-Allow-Headers", "Content-Type")
        handler.end_headers()
        handler.wfile.write(body)
    except ConnectionAbortedError:
        # Client disconnected (browser often does during refresh). Ignore.
        return
    except Exception:
        # Best-effort: if writing fails, avoid crashing the server.
        return



class KitchenHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {fmt % args}")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

   def do_GET(self):
    parsed = urlparse(self.path)
    path = parsed.path.rstrip("/")

            # FRONTEND ROUTES

    if path == "":
        path = "/"

    # Serve homepage
    if path == "/":
        try:
            with open("../frontend/index.html", "rb") as file:
                content = file.read()

            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            self.wfile.write(content)
            return

        except Exception as e:
            json_response(self, {
                "error": "Failed to load frontend",
                "detail": str(e)
            }, 500)
            return

            # Serve static files
    try:
        static_extensions = (
            ".css", ".js", ".png", ".jpg",
            ".jpeg", ".gif", ".svg", ".ico"
        )

        if path.endswith(static_extensions):

            file_path = "../frontend" + path

            with open(file_path, "rb") as file:
                content = file.read()

            content_type = "text/plain"

            if path.endswith(".css"):
                content_type = "text/css"

            elif path.endswith(".js"):
                content_type = "application/javascript"

            elif path.endswith(".png"):
                content_type = "image/png"

            elif path.endswith(".jpg") or path.endswith(".jpeg"):
                content_type = "image/jpeg"

            elif path.endswith(".gif"):
                content_type = "image/gif"

            elif path.endswith(".svg"):
                content_type = "image/svg+xml"

            elif path.endswith(".ico"):
                content_type = "image/x-icon"

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            self.wfile.write(content)
            return

    except Exception:
        pass

    # API ROUTES

    if path == "/api/ping":
        json_response(self, {
            "ok": True,
            "time": datetime.datetime.utcnow().isoformat() + "Z",
            "server": "cloud-kitchen"
        })
        return

    if path == "/api/orders/status":
        try:
            json_response(self, build_status())
        except Exception as e:
            json_response(self, {
                "error": "orders/status failed",
                "detail": str(e)
            }, 500)
        return

    if path == "/api/dashboard":
        try:
            orders = build_status()
            stations = build_station_load()
            completed = build_completed_orders()
            agent_log = build_agent_log()

            data = {
                "orders": orders,
                "stations": stations,
                "menu": MENU,
                "completed": completed,
                "agent_log": agent_log,
            }

            json_response(self, data)

        except Exception as e:
            import traceback
            print("[error] /api/dashboard exception:", repr(e))
            traceback.print_exc()

            json_response(self, {
                "error": "dashboard failed",
                "detail": str(e)
            }, 500)

        return

    if path == "/api/menu":
        try:
            json_response(self, MENU)

        except Exception as e:
            json_response(self, {
                "error": "menu failed",
                "detail": str(e)
            }, 500)

        return

    json_response(self, {"error": "Not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/orders":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))

            required = ["customer_name", "delivery_zone", "items"]
            for f in required:
                if f not in body:
                    return json_response(self, {"error": f"Missing field: {f}"}, 400)

            zone = body["delivery_zone"]
            if zone not in ZONE_SLA:
                return json_response(self, {"error": f"Unknown zone: {zone}"}, 400)

            for dish_id in body["items"]:
                if dish_id not in MENU_BY_ID:
                    return json_response(self, {"error": f"Unknown dish: {dish_id}"}, 400)

            now = datetime.datetime.utcnow()
            order_id = "ORD-" + str(uuid.uuid4())[:8].upper()
            ordered_at = now.isoformat() + "Z"
            deadline_at = (now + datetime.timedelta(minutes=ZONE_SLA[zone])).isoformat() + "Z"

            conn = get_db()
            conn.execute(
                "INSERT INTO orders(id,customer,zone,status,ordered_at,deadline_at) VALUES(?,?,?,?,?,?)",
                (order_id, body["customer_name"], zone, "pending", ordered_at, deadline_at)
            )
            item_ids = []
            for dish_id in body["items"]:
                dish = MENU_BY_ID[dish_id]
                item_id = "ITM-" + str(uuid.uuid4())[:8].upper()
                conn.execute(
                    "INSERT INTO order_items(id,order_id,dish_id,dish_name,station,prep_time,state) VALUES(?,?,?,?,?,?,?)",
                    (item_id, order_id, dish_id, dish["name"], dish["station"], dish["prep_time"], "queued")
                )
                item_ids.append(item_id)
            conn.commit()
            conn.close()

            run_scheduler()
            json_response(self, {"order_id": order_id, "item_ids": item_ids, "deadline_at": deadline_at}, 201)
            return

        json_response(self, {"error": "Not found"}, 404)

    def do_PATCH(self):
        parsed = urlparse(self.path)
        parts = parsed.path.rstrip("/").split("/")

        if len(parts) == 5 and parts[1] == "api" and parts[2] == "items" and parts[4] == "state":
            item_id = parts[3]
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            new_state = body.get("state")

            if new_state not in ("cooking", "done"):
                return json_response(self, {"error": "state must be 'cooking' or 'done'"}, 400)

            conn = get_db()
            item = conn.execute("SELECT * FROM order_items WHERE id=?", (item_id,)).fetchone()
            if not item:
                conn.close()
                return json_response(self, {"error": "Item not found"}, 404)

            now_iso = datetime.datetime.utcnow().isoformat() + "Z"
            if new_state == "cooking":
                conn.execute(
                    "UPDATE order_items SET state='cooking', started_at=? WHERE id=?",
                    (now_iso, item_id)
                )
            else:
                conn.execute(
                    "UPDATE order_items SET state='done', done_at=? WHERE id=?",
                    (now_iso, item_id)
                )
                order_id = item["order_id"]
                remaining = conn.execute(
                    "SELECT COUNT(*) FROM order_items WHERE order_id=? AND state != 'done'",
                    (order_id,)
                ).fetchone()[0]
                if remaining == 0:
                    conn.execute(
                        "UPDATE orders SET status='completed' WHERE id=?", (order_id,)
                    )

            conn.commit()
            conn.close()
            run_scheduler()
            json_response(self, {"ok": True, "item_id": item_id, "state": new_state})
            return

        json_response(self, {"error": "Not found"}, 404)



# ─────────────────────────────────────────────
#  ENTRYPOINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    seed_db()
    run_scheduler()
    server = HTTPServer(("0.0.0.0", PORT), KitchenHandler)
    print(f"[server] Cloud Kitchen API running on http://localhost:{PORT}")
    print(f"[server] Dashboard:     GET  http://localhost:{PORT}/api/dashboard")
    print(f"[server] Order status:  GET  http://localhost:{PORT}/api/orders/status")
    print(f"[server] New order:     POST http://localhost:{PORT}/api/orders")
    server.serve_forever()