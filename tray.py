"""
Premiere Logger — system tray status icon.
Started by the CEP extension when Premiere opens.
Shows live tracking status; double-click opens the dashboard.
"""
import sys
import threading
import time
import webbrowser
from pathlib import Path

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    print("pystray/Pillow not found. Run: pip install pystray pillow")
    sys.exit(1)

DASHBOARD_URL  = "http://localhost:5757"
POLL_SECONDS   = 10

COLORS = {
    "active": (76,  195, 120),
    "idle":   (220, 165,  30),
    "off":    (80,   80,  90),
}


def make_icon(state: str) -> Image.Image:
    fill = COLORS.get(state, COLORS["off"])
    img  = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d    = ImageDraw.Draw(img)
    d.ellipse([2, 2, 61, 61], fill=fill)
    ink = (18, 18, 22)
    d.rectangle([20, 16, 27, 48], fill=ink)
    d.rectangle([20, 16, 38, 23], fill=ink)
    d.rectangle([20, 30, 38, 37], fill=ink)
    d.ellipse(  [27, 16, 44, 37], fill=ink)
    d.ellipse(  [29, 19, 41, 34], fill=fill)
    return img


def get_tick_status() -> tuple[str | None, str | None]:
    try:
        import json
        from datetime import datetime
        data_dir = Path(__file__).parent / "data"
        day_file = data_dir / (datetime.now().strftime("%Y-%m-%d") + ".jsonl")
        if not day_file.exists():
            return None, None
        cutoff = datetime.now().timestamp() - 15
        last = None
        with open(day_file, encoding="utf-8") as f:
            for line in f:
                try:
                    t = json.loads(line)
                    ts = datetime.strptime(t["ts"], "%Y-%m-%d %H:%M:%S").timestamp()
                    if ts >= cutoff:
                        last = t
                except Exception:
                    pass
        if last:
            return last.get("project"), last.get("status")
    except Exception:
        pass
    return None, None


def _monitor(icon: pystray.Icon):
    while True:
        project, status = get_tick_status()
        if status == "active":
            icon.icon  = make_icon("active")
            icon.title = f"● {project}"
        elif status == "idle":
            icon.icon  = make_icon("idle")
            icon.title = f"◔ {project}  (idle)"
        else:
            icon.icon  = make_icon("off")
            icon.title = "Premiere Logger"
        time.sleep(POLL_SECONDS)


def _open_dashboard(icon, _item):
    webbrowser.open(DASHBOARD_URL)


def _quit(icon, _item):
    icon.stop()


def main():
    menu = pystray.Menu(
        pystray.MenuItem("Open Dashboard", _open_dashboard, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", _quit),
    )
    icon = pystray.Icon(
        name="premiere-logger",
        icon=make_icon("off"),
        title="Premiere Logger",
        menu=menu,
    )
    threading.Thread(target=_monitor, args=(icon,), daemon=True).start()
    icon.run()


if __name__ == "__main__":
    main()
