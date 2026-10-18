import os
import sys
from pathlib import Path

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
os.environ.setdefault("PYDANTIC_AI_GATEWAY_API_KEY", "")
EVALS = Path(__file__).resolve().parents[3] / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))
