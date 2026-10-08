import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))       # project root, so tests can import app / classify
os.environ.setdefault("USD_INR", "88")                    # no network in tests
os.environ.setdefault("LLM_PRICE_IN_PER_M", "0")          # pinned -> no price lookup
os.environ.setdefault("LLM_PRICE_OUT_PER_M", "0")
os.environ.setdefault("LOG_FILE", "none")                 # tests must not write the real app log
os.environ.setdefault("COST_LOG_FILE", os.path.join(tempfile.gettempdir(), "rl_cost_test.log"))
os.environ.setdefault("MIN_CONFIDENCE", "0.80")                # tests must not depend on a developer's .env
os.environ.setdefault("HINT_OVERRIDE_CONFIDENCE", "0.95")
os.environ.setdefault("OTHER_AUTO_CONFIDENCE", "0.95")
