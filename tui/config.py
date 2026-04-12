from pathlib import Path

MODULARIO_DIR = Path(__file__).resolve().parent.parent
THRESHOLDS_PATH = MODULARIO_DIR / 'configs' / 'thresholds.json'
ANALYZER_PATH = MODULARIO_DIR / 'scripts' / 'modulario-analyze.py'
STATE_DIR = MODULARIO_DIR / 'data' / 'state'
CURRENT_TARGET = MODULARIO_DIR / 'configs' / 'current-target.txt'
