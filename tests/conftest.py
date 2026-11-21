import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
# GUI tests run without a visible window (works on CI and headless Linux too).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
