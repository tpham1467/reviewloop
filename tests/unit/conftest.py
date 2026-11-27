import sys
from pathlib import Path

# eval/ is a script directory outside the package; its tests import it by module name.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))
