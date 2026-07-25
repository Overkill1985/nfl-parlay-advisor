"""Local sqlite storage for user-generated data (tracked picks, parlay slips,
and the engine's own leg-generation history for model calibration).

This is deliberately separate from cache/*.json, which holds re-fetchable ESPN
API responses. Everything in here is either user input or a record of what
the model said, and both are worth protecting - see `init_db` for backups.
"""
import datetime
import os
import shutil
import sqlite3
import threading

DB_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_PATH = os.path.join(DB_DIR, "parlay_advisor.db")
BACKUP_DIR = os.path.join(DB_DIR, "backups")

PICK_STATUSES = {"considered", "placed", "graded_win", "graded_loss", "graded_push"}
MARKET_TYPES = {"player_prop", "moneyline", "spread", "total"}
# "Yes" covers anytime-TD-style picks, which have no line to be over/under.
PICK_DIRECTIONS = {"Over", "Under", "Yes"}

_init_lock = threading.Lock()


def _now():
    return datetime.datetime.utcnow().isoformat()


def get_connection():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Each entry is (target_user_version, list_of_sql_statements). Applied in
# order, starting from whatever PRAGMA user_version currently reports.
MIGRATIONS = [
    (1, [
        """
        CREATE TABLE parlay_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            sportsbook TEXT,
            total_stake REAL,
            notes TEXT
        )
        """,
        """
        CREATE TABLE model_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            season INTEGER NOT NULL,
            week INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            player_name TEXT,
            team TEXT,
            position TEXT,
            stat TEXT NOT NULL,
            label TEXT,
            direction TEXT NOT NULL,
            line REAL,
            model_probability REAL NOT NULL,
            projected_avg REAL,
            source TEXT,
            risk_tier TEXT NOT NULL,
            actual_value REAL,
            grade_result TEXT,
            graded_at TEXT,
            UNIQUE(season, week, player_id, stat, direction, risk_tier)
        )
        """,
        """
        CREATE TABLE picks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'considered',
            market_type TEXT NOT NULL,
            parlay_group_id INTEGER REFERENCES parlay_groups(id),
            source_snapshot_id INTEGER REFERENCES model_snapshots(id),
            player_id INTEGER,
            player_name TEXT,
            team TEXT,
            opponent TEXT,
            season INTEGER,
            week INTEGER,
            stat TEXT,
            label TEXT,
            direction TEXT,
            model_line REAL,
            model_probability REAL,
            sportsbook TEXT,
            odds_american INTEGER,
            opening_odds_american INTEGER,
            closing_odds_american INTEGER,
            line_entered REAL,
            stake REAL,
            result_profit_loss REAL,
            actual_value REAL,
            graded_at TEXT,
            notes TEXT
        )
        """,
        "CREATE INDEX idx_picks_week ON picks(season, week)",
        "CREATE INDEX idx_picks_group ON picks(parlay_group_id)",
        "CREATE INDEX idx_snapshots_week ON model_snapshots(season, week)",
        "CREATE INDEX idx_snapshots_prob ON model_snapshots(model_probability)",
    ]),
]


def init_db():
    """Creates the db (if needed) and applies any pending migrations.

    Safe to call on every server startup - a no-op once the schema is current.
    """
    with _init_lock:
        os.makedirs(DB_DIR, exist_ok=True)
        db_existed = os.path.exists(DB_PATH)

        conn = get_connection()
        try:
            current_version = conn.execute("PRAGMA user_version").fetchone()[0]
            pending = [m for m in MIGRATIONS if m[0] > current_version]
            if not pending:
                return

            if db_existed:
                os.makedirs(BACKUP_DIR, exist_ok=True)
                stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                shutil.copy2(DB_PATH, os.path.join(BACKUP_DIR, f"parlay_advisor.db.bak-{stamp}"))

            for version, statements in pending:
                with conn:
                    for stmt in statements:
                        conn.execute(stmt)
                    conn.execute(f"PRAGMA user_version={version}")
                current_version = version
        finally:
            conn.close()


def _row_to_dict(row):
    return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# Parlay groups
# ---------------------------------------------------------------------------

def create_parlay_group(sportsbook=None, total_stake=None, notes=None):
    now = _now()
    conn = get_connection()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO parlay_groups (created_at, updated_at, sportsbook, total_stake, notes) "
                "VALUES (?, ?, ?, ?, ?)",
                (now, now, sportsbook, total_stake, notes),
            )
            return cur.lastrowid
    finally:
        conn.close()


def get_parlay_group(group_id):
    conn = get_connection()
    try:
        group = _row_to_dict(conn.execute(
            "SELECT * FROM parlay_groups WHERE id = ?", (group_id,)
        ).fetchone())
        if group is None:
            return None
        group["picks"] = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM picks WHERE parlay_group_id = ? ORDER BY id", (group_id,)
        ).fetchall()]
        return group
    finally:
        conn.close()


def list_parlay_groups():
    conn = get_connection()
    try:
        return [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM parlay_groups ORDER BY created_at DESC"
        ).fetchall()]
    finally:
        conn.close()


def update_parlay_group(group_id, **fields):
    if not fields:
        return False
    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn = get_connection()
    try:
        with conn:
            cur = conn.execute(
                f"UPDATE parlay_groups SET {set_clause} WHERE id = ?",
                (*fields.values(), group_id),
            )
            return cur.rowcount > 0
    finally:
        conn.close()


def delete_parlay_group(group_id):
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                "UPDATE picks SET parlay_group_id = NULL WHERE parlay_group_id = ?",
                (group_id,),
            )
            cur = conn.execute("DELETE FROM parlay_groups WHERE id = ?", (group_id,))
            return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Picks
# ---------------------------------------------------------------------------

PICK_FIELDS = [
    "status", "market_type", "parlay_group_id", "source_snapshot_id",
    "player_id", "player_name", "team", "opponent", "season", "week",
    "stat", "label", "direction", "model_line", "model_probability",
    "sportsbook", "odds_american", "opening_odds_american", "closing_odds_american",
    "line_entered", "stake", "result_profit_loss", "actual_value", "graded_at", "notes",
]


def create_pick(**fields):
    unknown = set(fields) - set(PICK_FIELDS)
    if unknown:
        raise ValueError(f"Unknown pick field(s): {sorted(unknown)}")

    now = _now()
    fields.setdefault("status", "considered")
    columns = ["created_at", "updated_at"] + list(fields.keys())
    values = [now, now] + list(fields.values())
    placeholders = ", ".join("?" for _ in columns)

    conn = get_connection()
    try:
        with conn:
            cur = conn.execute(
                f"INSERT INTO picks ({', '.join(columns)}) VALUES ({placeholders})",
                values,
            )
            return cur.lastrowid
    finally:
        conn.close()


def get_pick(pick_id):
    conn = get_connection()
    try:
        return _row_to_dict(conn.execute("SELECT * FROM picks WHERE id = ?", (pick_id,)).fetchone())
    finally:
        conn.close()


def list_picks(status=None, season=None, week=None, market_type=None, parlay_group_id=None):
    clauses = []
    params = []
    for col, val in (("status", status), ("season", season), ("week", week),
                      ("market_type", market_type), ("parlay_group_id", parlay_group_id)):
        if val is not None:
            clauses.append(f"{col} = ?")
            params.append(val)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    conn = get_connection()
    try:
        return [_row_to_dict(r) for r in conn.execute(
            f"SELECT * FROM picks {where} ORDER BY created_at DESC", params
        ).fetchall()]
    finally:
        conn.close()


def update_pick(pick_id, **fields):
    unknown = set(fields) - set(PICK_FIELDS)
    if unknown:
        raise ValueError(f"Unknown pick field(s): {sorted(unknown)}")
    if not fields:
        return False

    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn = get_connection()
    try:
        with conn:
            cur = conn.execute(
                f"UPDATE picks SET {set_clause} WHERE id = ?",
                (*fields.values(), pick_id),
            )
            return cur.rowcount > 0
    finally:
        conn.close()


def delete_pick(pick_id):
    conn = get_connection()
    try:
        with conn:
            cur = conn.execute("DELETE FROM picks WHERE id = ?", (pick_id,))
            return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Model snapshots (calibration tracking - see history.py for how these are
# used). Written automatically by the background refresh loop in server.py,
# not by the user, and are effectively append-only/immutable once graded.
# ---------------------------------------------------------------------------

MODEL_SNAPSHOT_FIELDS = [
    "season", "week", "player_id", "player_name", "team", "position",
    "stat", "label", "direction", "line", "model_probability", "projected_avg",
    "source", "risk_tier",
]


def upsert_model_snapshot(**fields):
    """Inserts a model-generated leg snapshot, silently skipping if one
    already exists for this (season, week, player_id, stat, direction,
    risk_tier) - the background refresh loop calls this every cycle and
    snapshots must stay immutable once written for calibration to mean
    anything."""
    unknown = set(fields) - set(MODEL_SNAPSHOT_FIELDS)
    if unknown:
        raise ValueError(f"Unknown model_snapshot field(s): {sorted(unknown)}")

    columns = ["created_at"] + list(fields.keys())
    values = [_now()] + list(fields.values())
    placeholders = ", ".join("?" for _ in columns)
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                f"INSERT OR IGNORE INTO model_snapshots ({', '.join(columns)}) VALUES ({placeholders})",
                values,
            )
    finally:
        conn.close()


def list_ungraded_snapshots(season, week):
    conn = get_connection()
    try:
        return [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM model_snapshots WHERE season = ? AND week = ? AND grade_result IS NULL",
            (season, week),
        ).fetchall()]
    finally:
        conn.close()


def grade_snapshot(snapshot_id, actual_value, grade_result):
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                "UPDATE model_snapshots SET actual_value = ?, grade_result = ?, graded_at = ? WHERE id = ?",
                (actual_value, grade_result, _now(), snapshot_id),
            )
    finally:
        conn.close()


def calibration_by_confidence_bucket(season=None):
    """Buckets graded snapshots by model_probability into 10-point bands and
    computes the realized hit rate per bucket - the concrete check of "did
    our 60%-confidence legs actually hit ~60% of the time?"."""
    where = "WHERE grade_result IS NOT NULL"
    params = []
    if season is not None:
        where += " AND season = ?"
        params.append(season)

    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT model_probability, grade_result FROM model_snapshots {where}", params
        ).fetchall()
    finally:
        conn.close()

    buckets = {}
    for row in rows:
        bucket = min(90, int(row["model_probability"] * 10) * 10)
        b = buckets.setdefault(bucket, {"total": 0, "hits": 0})
        b["total"] += 1
        if row["grade_result"] == "hit":
            b["hits"] += 1

    return [
        {
            "bucket_low": k, "bucket_high": k + 10,
            "total": v["total"], "hits": v["hits"],
            "realized_hit_rate": round(v["hits"] / v["total"], 4) if v["total"] else None,
        }
        for k, v in sorted(buckets.items())
    ]


# ---------------------------------------------------------------------------
# Export (human-readable escape hatch independent of sqlite)
# ---------------------------------------------------------------------------

def export_all():
    conn = get_connection()
    try:
        return {
            "picks": [_row_to_dict(r) for r in conn.execute("SELECT * FROM picks").fetchall()],
            "parlay_groups": [_row_to_dict(r) for r in conn.execute("SELECT * FROM parlay_groups").fetchall()],
            "model_snapshots": [_row_to_dict(r) for r in conn.execute("SELECT * FROM model_snapshots").fetchall()],
        }
    finally:
        conn.close()
