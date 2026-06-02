import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
for p in [str(ROOT_DIR), str(ROOT_DIR / "src")]:
    if p not in sys.path:
        sys.path.insert(0, p)
