"""Manual launcher — normally the CEP extension starts everything automatically."""
import subprocess, sys
from pathlib import Path

base = Path(__file__).parent
py   = sys.executable
subprocess.Popen([py, str(base / "server.py")])
subprocess.Popen([py, str(base / "tracker.py")])
subprocess.Popen([py, str(base / "tray.py")])
