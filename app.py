from datetime import date, datetime, timedelta


from flask import Flask, jsonify, render_template, request

from db import TICK_SECONDS, get_conn, init_db

SESSION_GAP = 600  # 10 minutes — covers brief alt-tabs during editing


def ticks_to_sessions(rows):
    """Convert a list of tick rows (timestamp, project, status) into sessions.
    Each session: {project, start, end, duration, active, idle}
    """
    sessions = []
    if not rows:
        return sessions

    cur = None
    for r in rows:
        ts = datetime.fromisoformat(r["timestamp"])
        if (
            cur is None
            or r["app"] != cur["app"]
            or (ts - cur["_last_ts"]).total_seconds() > SESSION_GAP
        ):
            if cur:
                cur["end"] = cur["_last_ts"].strftime("%Y-%m-%d %H:%M:%S")
                cur["duration"] = cur["active"] + cur["idle"]
                del cur["_last_ts"]
                sessions.append(cur)
            cur = {
                "app":      r["app"],
                "project":  r["project"],
                "sequence": r["sequence"] if "sequence" in r.keys() else None,
                "start":    r["timestamp"],
                "active":   0,
                "idle":     0,
                "_last_ts": ts,
            }
        cur[r["status"]] += TICK_SECONDS
        cur["_last_ts"] = ts

    if cur:
        cur["end"] = cur["_last_ts"].strftime("%Y-%m-%d %H:%M:%S")
        cur["duration"] = cur["active"] + cur["idle"]
        del cur["_last_ts"]
        sessions.append(cur)

    return sessions

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    """Most recent tick within the last 15 seconds — used for the live indicator."""
    conn = get_conn()
    row = conn.execute("""
        SELECT project, status, timestamp FROM ticks
        WHERE timestamp >= datetime('now', '-15 seconds', 'localtime')
        ORDER BY timestamp DESC LIMIT 1
    """).fetchone()
    conn.close()
    if row:
        return jsonify(
            {
                "tracking": True,
                "project": row["project"],
                "status": row["status"],
                "last_tick": row["timestamp"],
            }
        )
    return jsonify({"tracking": False})


@app.route("/api/today")
def api_today():
    today = date.today().isoformat()
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT project, status, COUNT(*) * ? AS seconds
        FROM ticks WHERE timestamp >= ?
        GROUP BY project, status
        """,
        (TICK_SECONDS, today + " 00:00:00"),
    ).fetchall()
    conn.close()

    projects: dict[str, dict] = {}
    for r in rows:
        p = r["project"]
        if p not in projects:
            projects[p] = {"active": 0, "idle": 0}
        projects[p][r["status"]] += r["seconds"]

    return jsonify(
        {
            "date": today,
            "projects": sorted(
                [{"name": n, "active": d["active"], "idle": d["idle"]} for n, d in projects.items()],
                key=lambda x: -x["active"],
            ),
        }
    )


@app.route("/api/week")
def api_week():
    week_start = (date.today() - timedelta(days=6)).isoformat()
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT date(timestamp) AS day, project, status, COUNT(*) * ? AS seconds
        FROM ticks WHERE timestamp >= ?
        GROUP BY day, project, status ORDER BY day
        """,
        (TICK_SECONDS, week_start + " 00:00:00"),
    ).fetchall()
    conn.close()

    days_map: dict[str, dict] = {}
    all_projects: set[str] = set()
    for r in rows:
        d, p = r["day"], r["project"]
        all_projects.add(p)
        days_map.setdefault(d, {}).setdefault(p, {"active": 0, "idle": 0})
        days_map[d][p][r["status"]] += r["seconds"]

    days = []
    for i in range(7):
        d = (date.today() - timedelta(days=6 - i)).isoformat()
        day_data = days_map.get(d, {})
        days.append(
            {
                "date": d,
                "projects": {n: v["active"] for n, v in day_data.items()},
                "total_active": sum(v["active"] for v in day_data.values()),
                "total_idle": sum(v["idle"] for v in day_data.values()),
            }
        )

    return jsonify({"days": days, "all_projects": sorted(all_projects)})


@app.route("/api/alltime")
def api_alltime():
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT project, status, COUNT(*) * ? AS seconds
        FROM ticks GROUP BY project, status
        """,
        (TICK_SECONDS,),
    ).fetchall()
    conn.close()

    projects: dict[str, dict] = {}
    for r in rows:
        p = r["project"]
        if p not in projects:
            projects[p] = {"active": 0, "idle": 0}
        projects[p][r["status"]] += r["seconds"]

    return jsonify(
        sorted(
            [{"name": n, "active": d["active"], "idle": d["idle"]} for n, d in projects.items()],
            key=lambda x: -x["active"],
        )
    )


@app.route("/api/tick", methods=["POST"])
def api_tick():
    """Receives a single tick from the CEP extension."""
    data = request.get_json(silent=True) or {}
    project = data.get("project")
    if not project:
        return jsonify({"ok": False, "error": "missing project"}), 400
    conn = get_conn()
    conn.execute(
        "INSERT INTO ticks (timestamp, app, project, sequence, status, source) VALUES (?, ?, ?, ?, ?, ?)",
        (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            data.get("app", "Adobe Premiere"),
            project,
            data.get("sequence"),
            data.get("status", "active"),
            "cep",
        ),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/apps_today")
def api_apps_today():
    today = date.today().isoformat()
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT app, status, COUNT(*) * ? AS seconds
        FROM ticks WHERE timestamp >= ?
        GROUP BY app, status
        """,
        (TICK_SECONDS, today + " 00:00:00"),
    ).fetchall()
    cats = {r["app"]: r["category"] for r in conn.execute("SELECT app, category FROM categories").fetchall()}
    conn.close()

    apps: dict[str, dict] = {}
    for r in rows:
        a = r["app"]
        if a not in apps:
            apps[a] = {"active": 0, "idle": 0}
        apps[a][r["status"]] += r["seconds"]

    return jsonify(sorted([
        {
            "app": a,
            "active": d["active"],
            "idle": d["idle"],
            "category": cats.get(a),
        }
        for a, d in apps.items()
    ], key=lambda x: -(x["active"] + x["idle"])))


@app.route("/api/categories", methods=["GET", "POST"])
def api_categories():
    conn = get_conn()
    if request.method == "POST":
        data = request.get_json()
        app_name = data["app"]
        category = data.get("category")
        if category:
            conn.execute(
                "INSERT OR REPLACE INTO categories (app, category) VALUES (?, ?)",
                (app_name, category),
            )
        else:
            conn.execute("DELETE FROM categories WHERE app = ?", (app_name,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    rows = conn.execute("SELECT app, category FROM categories").fetchall()
    conn.close()
    return jsonify({r["app"]: r["category"] for r in rows})


@app.route("/api/sessions")
def api_sessions():
    """Recent work sessions derived from tick gaps. ?days=N to control range (default 7)."""
    days = min(int(request.args.get("days", 7)), 90)
    since = (date.today() - timedelta(days=days - 1)).isoformat() + " 00:00:00"
    conn = get_conn()
    ignored = {r["app"] for r in conn.execute(
        "SELECT app FROM categories WHERE category = 'ignore'"
    ).fetchall()}
    rows = conn.execute(
        "SELECT timestamp, app, project, sequence, status FROM ticks WHERE timestamp >= ? ORDER BY timestamp",
        (since,),
    ).fetchall()
    rows = [r for r in rows if r["app"] not in ignored]
    conn.close()
    sessions = ticks_to_sessions(rows)
    sessions.reverse()  # newest first
    return jsonify(sessions)


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5757, debug=False)
