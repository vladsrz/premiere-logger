"""
tracker.py — tracks non-Premiere app activity during an editing session.
Writes directly to daily .jsonl files in data/.
Premiere Pro itself is tracked by the CEP extension.
"""
import ctypes
import json
import logging
import os
import re
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import win32api
    import win32con
    import win32gui
    import win32process
except ImportError:
    print("pywin32 not found. Run: pip install pywin32")
    sys.exit(1)

BASE     = Path(__file__).parent
DATA_DIR = BASE / "data"
POLL_INTERVAL   = 10
IDLE_THRESHOLD  = 300

logging.basicConfig(
    filename=str(BASE / "tracker.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

# ── app name lookup ────────────────────────────────────────────────────────

APP_NAMES = {
    "adobe premiere.exe":     "Adobe Premiere",
    "adobe premiere pro.exe": "Adobe Premiere",
    "premierepro.exe":        "Adobe Premiere",
    "firefox.exe":   "Firefox",
    "chrome.exe":    "Chrome",
    "msedge.exe":    "Edge",
    "brave.exe":     "Brave",
    "opera.exe":     "Opera",
    "vivaldi.exe":   "Vivaldi",
    "discord.exe":   "Discord",
    "spotify.exe":   "Spotify",
    "code.exe":      "VS Code",
    "afterfx.exe":   "After Effects",
    "photoshop.exe": "Photoshop",
    "audition.exe":  "Audition",
    "resolve.exe":   "DaVinci Resolve",
    "slack.exe":     "Slack",
    "notion.exe":    "Notion",
    "telegram.exe":  "Telegram",
    "whatsapp.exe":  "WhatsApp",
}

BROWSER_EXES = {"firefox.exe","chrome.exe","msedge.exe","brave.exe","opera.exe","vivaldi.exe"}

BROWSER_SUFFIXES = [
    " — Mozilla Firefox", " - Google Chrome", " - Microsoft Edge",
    " - Brave", " - Opera", " - Vivaldi",
]

SKIP_EXES = {
    "explorer.exe","searchhost.exe","searchui.exe",
    "startmenuexperiencehost.exe","shellexperiencehost.exe",
    "lockapp.exe","logonui.exe","textinputhost.exe",
}

KNOWN_SITES = [
    "YouTube","Instagram","Twitter","Reddit","Facebook","TikTok",
    "Netflix","Twitch","Spotify","SoundCloud","Bandcamp",
    "Artgrid","Pexels","Unsplash","Shutterstock","Getty Images",
    "Storyblocks","Motion Array","Envato","Epidemic Sound",
    "Google Drive","Google Docs","Gmail","Google",
    "Notion","Figma","GitHub","ChatGPT","Claude",
]


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def _single_instance_lock():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 47823))
        return sock
    except OSError:
        logging.info("Another tracker instance already running — exiting.")
        sys.exit(0)


def get_idle_seconds() -> float:
    lii = _LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
    return (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) / 1000.0


def is_premiere_running() -> bool:
    found = []
    def _cb(hwnd, _):
        if "Adobe Premiere" in win32gui.GetWindowText(hwnd):
            found.append(True)
        return True
    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        pass
    return bool(found)


def get_exe_name(hwnd: int) -> str:
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        h = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid)
        path_ = win32process.GetModuleFileNameEx(h, 0)
        win32api.CloseHandle(h)
        return os.path.basename(path_).lower()
    except Exception:
        return ""


def clean_app_name(exe: str) -> str:
    return APP_NAMES.get(exe, exe.replace(".exe","").replace("_"," ").replace("-"," ").title())


def parse_premiere_project(title: str) -> tuple[str, str | None]:
    """Return (display_name, project_path).
    project_path is the canonical file path — stable across saves/asterisks.
    display_name is the friendly name shown in the dashboard.
    """
    # Extract the .prproj path directly from the title — most reliable
    path_match = re.search(r"([A-Za-z]:\\.+?\.prproj)", title, re.IGNORECASE)
    if path_match:
        raw_path = path_match.group(1).rstrip(" *")
        project_path = os.path.normpath(raw_path).lower()  # normalise for dedup
        display = os.path.splitext(os.path.basename(raw_path))[0].strip("* ").strip()
        return display or "(no project open)", project_path

    # Fallback for titles without a full path
    parts = [p.strip() for p in title.split(" - ")]
    idx = next((i for i, p in enumerate(parts) if "Adobe Premiere" in p), None)
    other = [p for i, p in enumerate(parts) if i != idx]
    if not other:
        return "(no project open)", None
    project = " - ".join(other)
    project = re.sub(r"\.prproj[\s*]*$", "", project, flags=re.IGNORECASE)
    return project.strip("* ").strip() or "(no project open)", None


def extract_browser_context(title: str) -> str:
    for s in BROWSER_SUFFIXES:
        if title.endswith(s):
            title = title[:-len(s)].strip()
            break
    for site in KNOWN_SITES:
        if site.lower() in title.lower():
            return site
    for sep in (" - ", " | ", " — "):
        if sep in title:
            last = title.split(sep)[-1].strip()
            if 2 < len(last) < 60:
                return last
    return title[:60] or "Browser"


def get_current_activity():
    try:
        hwnd  = win32gui.GetForegroundWindow()
        if not hwnd: return None
        title = win32gui.GetWindowText(hwnd)
        if not title.strip(): return None

        exe = get_exe_name(hwnd)
        if exe in SKIP_EXES: return None

        if "Adobe Premiere" in title:
            display, proj_path = parse_premiere_project(title)
            return "Adobe Premiere", display, proj_path

        app = clean_app_name(exe) if exe else title.split(" - ")[-1].strip()[:40]

        if exe in BROWSER_EXES:
            return app, extract_browser_context(title), None
        return app, app, None
    except Exception:
        return None


def write_tick(app: str, project: str, status: str, project_path: str | None = None):
    DATA_DIR.mkdir(exist_ok=True)
    tick = {
        "ts":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "app":     app,
        "project": project,
        "status":  status,
        "source":  "tracker",
    }
    if project_path:
        tick["project_path"] = project_path
    date_str  = tick["ts"][:10]
    day_file  = DATA_DIR / f"{date_str}.jsonl"
    with open(day_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(tick) + "\n")


def main():
    _lock = _single_instance_lock()  # noqa: F841
    DATA_DIR.mkdir(exist_ok=True)
    logging.info("Tracker started")

    while True:
        if not is_premiere_running():
            time.sleep(POLL_INTERVAL)
            continue

        activity = get_current_activity()
        if activity is not None:
            app, project, project_path = activity
            status = "idle" if get_idle_seconds() >= IDLE_THRESHOLD else "active"
            write_tick(app, project, status, project_path)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
