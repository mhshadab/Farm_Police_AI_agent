import os
import json
import hashlib
import sqlite3
import datetime as dt
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


# =========================
# CONFIG
# =========================

# IBM Orchestrate
SERVICE_URL = "https://api.jp-tok.watson-orchestrate.cloud.ibm.com/instances/0a0266f4-baa9-406b-9571-de10472446a8"
AGENT_ID = "762cb728-4f80-438c-9ec9-f100f10c68b0"
IAM_TOKEN_URL = "https://iam.cloud.ibm.com/identity/token"
RUNS_STREAM_URL = f"{SERVICE_URL}/v1/orchestrate/runs/stream"

HTTP_TIMEOUT: Tuple[int, int] = (10, 60)  # connect, read

# Google Apps Script Web App (email sender)
GOOGLE_WEBAPP_URL = (
    "https://script.google.com/macros/s/AKfycbxyHB-0wJBakRWdyvQTgelfILRDxpkmhXUOT79jkyHQYMEW8GDPK98CRK1_3sp1KpgjgQ/exec"
)

# Recipients (per your requirement: send both to Shadab)
OPS_EMAIL_TO = "shadab@ualberta.ca"
MAINT_EMAIL_TO = "shadab@ualberta.ca"

# Local DB (PERSISTENT — does NOT get cleared)
DB_PATH = "workorders.db"

# Default user (created once; reused every run/session)
DEFAULT_USER_EMAIL = "default_user@farm.local"
DEFAULT_USER_NAME = "Farm Ops Default"


# =========================
# TIME HELPERS
# =========================

def utc_now_iso() -> str:
    """Timezone-aware UTC ISO string ending with Z."""
    return dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")


# =========================
# AUTH + ORCHESTRATE API
# =========================

def read_apikey() -> str:
    return (Path(__file__).resolve().parent / "apikey.txt").read_text(encoding="utf-8").strip()


def get_iam_access_token(api_key: str) -> str:
    r = requests.post(
        IAM_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
            "apikey": api_key,
        },
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def run_agent_stream_get_tool_args(prompt: str, api_key: Optional[str] = None) -> Dict[str, Any]:
    """
    Streams a run and extracts the incident args payload from tool_calls (e.g., log_incident).
    For your flow-based agent, this is the "main result".
    """
    if api_key is None:
        api_key = read_apikey()

    token = get_iam_access_token(api_key)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
    }

    payload = {"agent_id": AGENT_ID, "message": {"role": "user", "content": prompt}}

    tool_args: Dict[str, Any] = {}
    tool_name: Optional[str] = None

    with requests.post(
        RUNS_STREAM_URL,
        headers=headers,
        json=payload,
        stream=True,
        timeout=HTTP_TIMEOUT,
    ) as r:
        if not r.ok:
            print("Stream HTTP ERROR:", r.status_code)
            print(r.text)
            r.raise_for_status()

        for raw in r.iter_lines(decode_unicode=False):
            if raw is None:
                continue

            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            # Each line is JSON like {"id": "...", "event": "...", "data": {...}}
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = obj.get("event")
            data = obj.get("data")
            if not isinstance(data, dict):
                continue

            # Extract tool args from run.step.delta
            if event_type == "run.step.delta":
                delta = data.get("delta")
                if isinstance(delta, dict):
                    step_details = delta.get("step_details", [])
                    if isinstance(step_details, list):
                        for sd in step_details:
                            if not isinstance(sd, dict):
                                continue
                            if sd.get("type") == "tool_calls":
                                for tc in sd.get("tool_calls", []) or []:
                                    if not isinstance(tc, dict):
                                        continue
                                    name = tc.get("name")
                                    args = tc.get("args")
                                    if name and isinstance(args, dict):
                                        tool_name = name
                                        tool_args = args  # keep latest

            if event_type in ("run.completed", "done"):
                break

    if not tool_args:
        raise RuntimeError("No tool args captured from stream. Flow may not have emitted tool_calls.")

    tool_args["_tool_name"] = tool_name or ""
    return tool_args


# =========================
# EMAIL (via Google Web App)
# =========================

def build_ops_email(args: Dict[str, Any]) -> Dict[str, str]:
    asset = args.get("asset", "Unknown asset")
    severity = args.get("severity", "Unknown")
    incident_type = args.get("incident_type", "unknown")
    location = args.get("location", "")
    incident_ts = args.get("timestamp", "")
    summary = args.get("summary", "")
    hypotheses = args.get("hypotheses", []) or []
    actions = args.get("actions", []) or []

    subject = f"{severity} {asset} {incident_type} — incident notification"

    lines = []
    lines.append(f"Incident logged: {asset}")
    lines.append("")
    if summary:
        lines.append("Summary")
        lines.append(summary)
        lines.append("")
    lines.append("Details")
    lines.append(f"- Classification: {incident_type}")
    lines.append(f"- Severity: {severity}")
    if location:
        lines.append(f"- Location: {location}")
    if incident_ts:
        lines.append(f"- Incident Timestamp: {incident_ts}")

    if hypotheses:
        lines.append("")
        lines.append("Likely causes (ranked)")
        for i, h in enumerate(hypotheses, 1):
            lines.append(f"{i}. {h}")

    if actions:
        lines.append("")
        lines.append("Immediate action checklist")
        for a in actions:
            lines.append(f"- {a}")

    body = "\n".join(lines).strip()
    return {"to": OPS_EMAIL_TO, "subject": subject, "body": body}


def build_maint_email(args: Dict[str, Any]) -> Dict[str, str]:
    asset = args.get("asset", "Unknown asset")
    severity = args.get("severity", "Unknown")
    incident_type = args.get("incident_type", "unknown")
    location = args.get("location", "")
    incident_ts = args.get("timestamp", "")
    summary = args.get("summary", "")
    actions = args.get("actions", []) or []

    subject = f"MAINT: {severity} {asset} {incident_type} — maintenance action"

    lines = []
    lines.append("Maintenance request")
    lines.append("")
    lines.append(f"Asset: {asset}")
    lines.append(f"Type: {incident_type}")
    lines.append(f"Severity: {severity}")
    if location:
        lines.append(f"Location: {location}")
    if incident_ts:
        lines.append(f"Incident Timestamp: {incident_ts}")

    if summary:
        lines.append("")
        lines.append("Summary")
        lines.append(summary)

    if actions:
        lines.append("")
        lines.append("Recommended actions")
        for a in actions:
            lines.append(f"- {a}")

    body = "\n".join(lines).strip()
    return {"to": MAINT_EMAIL_TO, "subject": subject, "body": body}


def send_via_google_webapp(email_payload: Dict[str, str]) -> None:
    r = requests.post(
        GOOGLE_WEBAPP_URL,
        headers={"Content-Type": "application/json"},
        data=json.dumps(email_payload),
        timeout=HTTP_TIMEOUT,
    )
    if not r.ok:
        print("Google WebApp HTTP ERROR:", r.status_code)
        print(r.text)
        r.raise_for_status()


# =========================
# DB (SQLite, persistent)
# =========================

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def db_init(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS work_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_order_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            created_by_user_id INTEGER NOT NULL,
            asset TEXT,
            incident_type TEXT,
            severity TEXT,
            location TEXT,
            incident_timestamp TEXT,
            summary TEXT,
            raw_json TEXT NOT NULL,
            FOREIGN KEY(created_by_user_id) REFERENCES users(id)
        );
        """
    )
    conn.commit()


def ensure_default_user(conn: sqlite3.Connection) -> int:
    cur = conn.execute("SELECT id FROM users WHERE email = ?", (DEFAULT_USER_EMAIL,))
    row = cur.fetchone()
    if row:
        return int(row[0])

    conn.execute(
        "INSERT INTO users (email, name, created_at) VALUES (?, ?, ?)",
        (DEFAULT_USER_EMAIL, DEFAULT_USER_NAME, utc_now_iso()),
    )
    conn.commit()

    cur = conn.execute("SELECT id FROM users WHERE email = ?", (DEFAULT_USER_EMAIL,))
    return int(cur.fetchone()[0])


def make_work_order_key(args: Dict[str, Any]) -> str:
    """
    Deterministic key to prevent duplicates.
    Uses asset + incident_type + severity + incident_timestamp + summary.
    """
    asset = str(args.get("asset", "")).strip().lower()
    incident_type = str(args.get("incident_type", "")).strip().lower()
    severity = str(args.get("severity", "")).strip().lower()
    summary = str(args.get("summary", "")).strip().lower()
    incident_ts = str(args.get("timestamp", "")).strip().lower()

    base = f"{asset}|{incident_type}|{severity}|{incident_ts}|{summary}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def upsert_work_order(conn: sqlite3.Connection, user_id: int, args: Dict[str, Any]) -> bool:
    """
    Inserts a work order if not already present.
    Returns True if inserted, False if it was a duplicate.
    """
    wo_key = make_work_order_key(args)

    created_at = utc_now_iso()
    asset = args.get("asset")
    incident_type = args.get("incident_type")
    severity = args.get("severity")
    location = args.get("location")
    incident_timestamp = args.get("timestamp")
    summary = args.get("summary")
    raw_json = json.dumps(args, ensure_ascii=False)

    try:
        conn.execute(
            """
            INSERT INTO work_orders (
                work_order_key, created_at, created_by_user_id,
                asset, incident_type, severity, location, incident_timestamp,
                summary, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                wo_key, created_at, user_id,
                asset, incident_type, severity, location, incident_timestamp,
                summary, raw_json
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def fetch_work_orders(conn: sqlite3.Connection, limit: int = 50) -> list[tuple]:
    cur = conn.execute(
        """
        SELECT created_at, asset, incident_type, severity, location, incident_timestamp, summary
        FROM work_orders
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    return cur.fetchall()


# =========================
# CHART: Severity over time
# =========================

def plot_work_orders_severity_timeline(conn: sqlite3.Connection) -> None:
    """
    Timeline chart:
    - X axis: work order created_at
    - Y axis: severity level (P1=3, P2=2, P3=1, unknown=0)
    - Each work order appears over time (helps even if all P2).
    """
    cur = conn.execute(
        """
        SELECT created_at, COALESCE(severity, 'unknown') AS severity
        FROM work_orders
        ORDER BY created_at ASC
        """
    )
    rows = cur.fetchall()
    if not rows:
        print("No work orders yet to chart.")
        return

    severity_map = {"P1": 3, "P2": 2, "P3": 1, "unknown": 0}

    xs = []
    ys = []
    for created_at, sev in rows:
        try:
            t = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except Exception:
            continue
        sev_key = sev if sev in severity_map else "unknown"
        xs.append(t)
        ys.append(severity_map[sev_key])

    plt.figure()
    plt.scatter(xs, ys)
    plt.title("Work Orders Severity Over Time")
    plt.xlabel("Work Order Created At")
    plt.ylabel("Severity Level (P1=3, P2=2, P3=1)")

    ax = plt.gca()
    ax.yaxis.set_ticks([0, 1, 2, 3])
    ax.yaxis.set_ticklabels(["unknown", "P3", "P2", "P1"])
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    plt.xticks(rotation=30)
    plt.tight_layout()
    plt.show()


# =========================
# MAIN APP LOOP
# =========================

def main() -> None:
    conn = db_connect()
    db_init(conn)
    user_id = ensure_default_user(conn)

    print("\n=== Farm Incident Response CLI ===")
    print("Type an incident/sensor message (or press Enter to quit).")
    print("Example: Grain bin 7 temperature increased by 12°C in the last 24 hours.\n")

    while True:
        prompt = input("Input sensor data > ").strip()
        if not prompt:
            print("Exiting.")
            break

        print("\nCalling Orchestrate agent...")
        try:
            args = run_agent_stream_get_tool_args(prompt)
        except Exception as e:
            print("ERROR calling agent:", e)
            continue

        # Store in DB (persistent, deduped)
        inserted = upsert_work_order(conn, user_id, args)
        if inserted:
            print("✅ Work order recorded (new).")
        else:
            print("⚠️ Work order already exists (duplicate skipped).")

        # Build + send emails
        ops_email = build_ops_email(args)
        maint_email = build_maint_email(args)

        print("Sending Ops email...")
        try:
            send_via_google_webapp(ops_email)
            print("✅ Ops email sent.")
        except Exception as e:
            print("❌ Ops email failed:", e)

        print("Sending Maintenance email...")
        try:
            send_via_google_webapp(maint_email)
            print("✅ Maintenance email sent.")
        except Exception as e:
            print("❌ Maintenance email failed:", e)

        # Display recent work orders
        print("\n=== Recent Work Orders (latest 10) ===")
        rows = fetch_work_orders(conn, limit=10)
        for r in rows:
            created_at, asset, incident_type, severity, location, incident_ts, summary = r
            print(f"- [{created_at}] {severity} | {asset} | {incident_type} | {location or ''} | {incident_ts or ''}")
            if summary:
                print(f"  Summary: {summary}")

        # Chart
        print("\nShowing chart (severity over time)...")
        try:
            plot_work_orders_severity_timeline(conn)
        except Exception as e:
            print("Chart error:", e)

        print("\n--- Ready for next incident ---\n")

    conn.close()


if __name__ == "__main__":
    main()
