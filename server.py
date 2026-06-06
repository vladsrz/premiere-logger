"""
server.py — dashboard server, zero external dependencies.
Data lives in data/YYYY-MM-DD.jsonl  (tick log)
             data/sessions-YYYY-MM-DD.json  (persisted session summaries)
"""
import json
import socket
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BASE        = Path(__file__).parent
DATA_DIR    = BASE / "data"
TEMPLATE    = BASE / "templates" / "index.html"
TICK_SEC    = 10
SESSION_GAP = 600   # 10 min gap = new session
SHORT_SECS  = 300   # < 5 min = "short", hidden by default


# ── normalisation ─────────────────────────────────────────────────────────

_PREMIERE = {"Adobe Premiere", "Adobe Premiere Pro"}
_BAD_PROJ = {"Adobe Premiere", "Adobe Premiere Pro", "(no project open)", ""}

_BROWSER_APPS = {"Firefox", "Chrome", "Edge", "Brave", "Opera", "Vivaldi"}
_KNOWN_SITES  = {
    "YouTube", "Instagram", "Twitter", "Reddit", "Facebook", "TikTok",
    "Netflix", "Twitch", "Spotify", "SoundCloud", "Bandcamp",
    "Artgrid", "Pexels", "Unsplash", "Shutterstock", "Getty Images",
    "Storyblocks", "Motion Array", "Envato", "Epidemic Sound",
    "Google Drive", "Google Docs", "Gmail", "Google",
    "Notion", "Figma", "GitHub", "ChatGPT", "Claude",
    "Frame.io", "Stack Overflow", "Wikipedia",
}


def _norm(t: dict) -> dict:
    """Return a copy of t with the app name normalised to 'Adobe Premiere'."""
    if t.get("app") in _PREMIERE:
        return dict(t, app="Adobe Premiere")
    return t


def _proj_key(t: dict) -> str:
    """Stable dedup key: file path (lower) when available, else display name."""
    return (t.get("project_path") or t.get("project") or "").lower()


# ── data helpers ──────────────────────────────────────────────────────────

def read_day(ds: str) -> list:
    f = DATA_DIR / f"{ds}.jsonl"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def read_range(days: int) -> list:
    out = []
    for i in range(days - 1, -1, -1):
        ds = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        out.extend(read_day(ds))
    return out


def read_cats() -> dict:
    f = DATA_DIR / "categories.json"
    defaults = {
        "Adobe Premiere": "work", "After Effects": "work",
        "DaVinci Resolve": "work", "Photoshop": "work", "Audition": "work",
    }
    if not f.exists():
        return defaults
    try:
        return json.loads(f.read_text())
    except Exception:
        return defaults


def save_cats(cats: dict):
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "categories.json").write_text(json.dumps(cats, indent=2))


def read_aliases() -> dict:
    f = DATA_DIR / "aliases.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_aliases(aliases: dict):
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "aliases.json").write_text(json.dumps(aliases, indent=2), encoding="utf-8")


# ── aggregation ───────────────────────────────────────────────────────────

def by_project(ticks: list) -> list:
    """Aggregate Premiere ticks by project. Merges path-keyed + name-keyed."""
    acc: dict   = {}  # key  -> {active, idle}
    names: dict = {}  # key  -> display name
    for t in (_norm(t) for t in ticks):
        if t.get("app") != "Adobe Premiere":
            continue
        display = t.get("project", "")
        if display in _BAD_PROJ:
            continue
        key = _proj_key(t)
        if not key or key in _BAD_PROJ:
            continue
        acc.setdefault(key, {"active": 0, "idle": 0})
        acc[key][t.get("status", "active")] += TICK_SEC
        names[key] = display

    # Second pass: merge any keys that share the same display name
    merged: dict = {}
    for key, v in acc.items():
        n = names[key]
        merged.setdefault(n, {"active": 0, "idle": 0})
        merged[n]["active"] += v["active"]
        merged[n]["idle"]   += v["idle"]

    return sorted(
        [{"name": n, "active": v["active"], "idle": v["idle"],
          "short": (v["active"] + v["idle"]) < SHORT_SECS}
         for n, v in merged.items()],
        key=lambda x: -x["active"],
    )


def by_app(ticks: list) -> list:
    cats    = read_cats()
    aliases = read_aliases()
    acc: dict = {}
    for t in (_norm(t) for t in ticks):
        a    = t.get("app", "")
        proj = t.get("project") or ""
        # Split known sites out of their browser so they can be tagged individually.
        # Also split any context the user has already tagged (so tags persist).
        if a in _BROWSER_APPS and proj and (proj in _KNOWN_SITES or cats.get(proj) or aliases.get(proj)):
            key, browser = proj, a
        else:
            key, browser = a, None
        if key not in acc:
            acc[key] = {"active": 0, "idle": 0, "browser": browser}
        acc[key][t.get("status", "active")] += TICK_SEC

    return sorted([
        {"app": k, "alias": aliases.get(k) or None,
         "active": v["active"], "idle": v["idle"],
         "browser": v["browser"], "category": cats.get(k)}
        for k, v in acc.items()
    ], key=lambda x: -(x["active"] + x["idle"]))


def to_sessions(ticks: list, persist_date: str | None = None) -> list:
    """Convert ticks to sessions. Optionally persist to data/sessions-DATE.json."""
    cats = read_cats()
    rows = [_norm(t) for t in ticks if cats.get(_norm(t).get("app")) != "ignore"]

    sessions: list = []
    cur: dict | None = None

    for t in rows:
        ts = datetime.strptime(t["ts"], "%Y-%m-%d %H:%M:%S")
        same = (cur is not None
                and t.get("app") == cur["app"]
                and (ts - cur["_ts"]).total_seconds() <= SESSION_GAP)
        if not same:
            if cur:
                sessions.append(_flush(cur))
            cur = {
                "app":          t.get("app", ""),
                "project":      t.get("project", ""),
                "project_path": t.get("project_path"),
                "sequence":     t.get("seq"),
                "start":        t["ts"],
                "active":       0,
                "idle":         0,
                "_ts":          ts,
                "_end":         t["ts"],
            }
        cur[t.get("status", "active")] += TICK_SEC
        cur["_ts"]  = ts
        cur["_end"] = t["ts"]

    if cur:
        sessions.append(_flush(cur))

    # Mark short sessions
    for s in sessions:
        s["short"] = s["active"] < SHORT_SECS

    # Persist session summaries for this date
    if persist_date:
        _persist_sessions(sessions, persist_date)

    return list(reversed(sessions))


def _flush(cur: dict) -> dict:
    s = {k: v for k, v in cur.items() if not k.startswith("_")}
    s["end"]      = cur["_end"]
    s["duration"] = s["active"] + s["idle"]
    return s


def _persist_sessions(sessions: list, date_str: str):
    """Write session summaries to data/sessions-YYYY-MM-DD.json."""
    DATA_DIR.mkdir(exist_ok=True)
    f = DATA_DIR / f"sessions-{date_str}.json"
    # Only keep sessions whose start date matches
    day_sessions = [s for s in sessions if s.get("start", "").startswith(date_str)]
    f.write_text(json.dumps(day_sessions, indent=2), encoding="utf-8")


# ── HTTP handler ──────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        url = self.path.split("?")[0]

        if url == "/":
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(TEMPLATE.read_bytes())
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return

        today = datetime.now().strftime("%Y-%m-%d")
        try:
            if url == "/api/status":
                cutoff = datetime.now().timestamp() - 15
                last = None
                for t in read_day(today):
                    if datetime.strptime(t["ts"], "%Y-%m-%d %H:%M:%S").timestamp() >= cutoff:
                        last = t
                if last:
                    t = _norm(last)
                    self._json({"tracking": True, "project": t.get("project"),
                                "status": t.get("status"), "last_tick": t["ts"]})
                else:
                    self._json({"tracking": False})

            elif url == "/api/today":
                self._json({"date": today, "projects": by_project(read_day(today))})

            elif url == "/api/week":
                all_names: dict = {}
                days = []
                for i in range(6, -1, -1):
                    ds    = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
                    projs = by_project(read_day(ds))
                    d_map = {p["name"]: p["active"] for p in projs}
                    for n in d_map:
                        all_names[n] = True
                    days.append({
                        "date":         ds,
                        "projects":     d_map,
                        "total_active": sum(d_map.values()),
                    })
                self._json({"days": days, "all_projects": sorted(all_names)})

            elif url == "/api/sessions":
                all_ticks = read_range(7)
                sessions  = to_sessions(all_ticks, persist_date=today)
                self._json(sessions)

            elif url == "/api/alltime":
                self._json(by_project(read_range(365)))

            elif url == "/api/apps_alltime":
                self._json(by_app(read_range(365)))

            elif url == "/api/projects":
                ticks = read_range(365)
                acc:   dict = {}
                names: dict = {}
                for t in (_norm(t) for t in ticks):
                    if t.get("app") != "Adobe Premiere":
                        continue
                    display = t.get("project", "")
                    if display in _BAD_PROJ:
                        continue
                    key = _proj_key(t)
                    if not key or key in _BAD_PROJ:
                        continue
                    if key not in acc:
                        acc[key] = {"active": 0, "idle": 0,
                                    "first": t["ts"][:10], "last": t["ts"][:10]}
                    acc[key][t.get("status", "active")] += TICK_SEC
                    acc[key]["last"] = t["ts"][:10]
                    names[key] = display
                merged: dict = {}
                for key, v in acc.items():
                    n = names[key]
                    if n not in merged:
                        merged[n] = {"active": 0, "idle": 0,
                                     "first": v["first"], "last": v["last"]}
                    merged[n]["active"] += v["active"]
                    merged[n]["idle"]   += v["idle"]
                    if v["first"] < merged[n]["first"]:
                        merged[n]["first"] = v["first"]
                    if v["last"]  > merged[n]["last"]:
                        merged[n]["last"]  = v["last"]
                self._json(sorted([
                    {"name": n, "active": v["active"], "idle": v["idle"],
                     "first_date": v["first"], "last_date": v["last"],
                     "short": (v["active"] + v["idle"]) < SHORT_SECS}
                    for n, v in merged.items()
                ], key=lambda x: -x["active"]))

            elif url == "/api/project":
                name = self.path.split("name=")[-1].split("&")[0]
                import urllib.parse
                name = urllib.parse.unquote_plus(name)
                ticks = [_norm(t) for t in read_range(365)
                         if _norm(t).get("app") == "Adobe Premiere"
                         and t.get("project") == name]
                # sequences
                seq_acc: dict = {}
                for t in ticks:
                    s = t.get("seq") or "(no sequence)"
                    seq_acc.setdefault(s, {"active": 0, "idle": 0})
                    seq_acc[s][t.get("status","active")] += TICK_SEC
                sequences = sorted(
                    [{"name": k, "active": v["active"], "idle": v["idle"]}
                     for k, v in seq_acc.items()],
                    key=lambda x: -x["active"]
                )
                # sessions
                sessions = to_sessions(ticks)
                proj_sessions = [s for s in sessions if s.get("project") == name]
                total_active = sum(t.get("active",0) for t in proj_sessions)
                total_idle   = sum(t.get("idle",0)   for t in proj_sessions)
                first = ticks[0]["ts"][:10]  if ticks else None
                last  = ticks[-1]["ts"][:10] if ticks else None
                self._json({
                    "name": name, "total_active": total_active,
                    "total_idle": total_idle, "first_date": first,
                    "last_date": last, "sequences": sequences,
                    "sessions": proj_sessions,
                })

            elif url == "/api/apps_today":
                self._json(by_app(read_day(today)))

            elif url == "/api/categories":
                self._json(read_cats())

            elif url == "/api/aliases":
                self._json(read_aliases())

            else:
                self._json({"error": "not found"}, 404)

        except Exception as e:
            self._json({"error": str(e)}, 500)

    def do_POST(self):
        url    = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length) or b"{}")
        today  = datetime.now().strftime("%Y-%m-%d")

        try:
            if url == "/api/tick":
                project = body.get("project")
                if not project:
                    self._json({"ok": False, "error": "missing project"}, 400)
                    return
                DATA_DIR.mkdir(exist_ok=True)
                tick = {
                    "ts":           datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "app":          "Adobe Premiere",
                    "project":      project,
                    "project_path": body.get("project_path"),
                    "seq":          body.get("sequence"),
                    "status":       body.get("status", "active"),
                    "source":       "cep",
                }
                with open(DATA_DIR / f"{today}.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(tick) + "\n")
                self._json({"ok": True})

            elif url == "/api/categories":
                cats = read_cats()
                if body.get("category"):
                    cats[body["app"]] = body["category"]
                else:
                    cats.pop(body.get("app", ""), None)
                save_cats(cats)
                self._json({"ok": True})

            elif url == "/api/aliases":
                aliases = read_aliases()
                key   = body.get("app", "")
                alias = (body.get("alias") or "").strip()
                if alias and alias != key:
                    aliases[key] = alias
                else:
                    aliases.pop(key, None)
                save_aliases(aliases)
                self._json({"ok": True})

            else:
                self._json({"error": "not found"}, 404)

        except Exception as e:
            self._json({"error": str(e)}, 500)


# ── entry point ───────────────────────────────────────────────────────────

def main():
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", 47824))
    except OSError:
        return  # already running
    DATA_DIR.mkdir(exist_ok=True)
    HTTPServer(("127.0.0.1", 5757), Handler).serve_forever()


if __name__ == "__main__":
    main()
