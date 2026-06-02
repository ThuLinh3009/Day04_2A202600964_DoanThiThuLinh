"""Run grader without LLM judge to save quota."""
from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
# Allow `from core.xxx import` inside src/
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from grade.scoring import coerce_result, grade_result, load_cases, summarize_scores

MODULE   = sys.argv[1] if len(sys.argv) > 1 else "simple_solution.agent.graph"
DELAY    = int(sys.argv[2]) if len(sys.argv) > 2 else 5
PROVIDER = sys.argv[3] if len(sys.argv) > 3 else "google"

module = importlib.import_module(MODULE)
cases = load_cases(ROOT_DIR / "data" / "graded_cases.json")

print(f"Module: {MODULE}  Provider: {PROVIDER}  Delay: {DELAY}s", flush=True)

def run_case_with_retry(module, case, max_retries=5):
    for attempt in range(max_retries):
        try:
            raw = module.run_agent(case["query"], provider=PROVIDER, today="2026-06-01")
            result = coerce_result(raw, query=case["query"], provider=PROVIDER, model_name=None)
            return grade_result(result, case, judge_provider=PROVIDER)
        except Exception as e:
            msg = str(e)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                wait = 60 * (attempt + 1)
                print(f"  429 rate limit, waiting {wait}s (attempt {attempt+1}/{max_retries})...", flush=True)
                time.sleep(wait)
            else:
                raise
    raise RuntimeError(f"Failed after {max_retries} retries")


scores = []
for i, case in enumerate(cases):
    if i > 0:
        print(f"  Waiting {DELAY}s...", flush=True)
        time.sleep(DELAY)
    cid = case["id"]
    print(f"[{i+1}/{len(cases)}] {cid}", flush=True)
    try:
        score = run_case_with_retry(module, case)
        scores.append(score)
        print(f"  => {score.score}/{score.max_score}  feedback={score.feedback}", flush=True)
    except Exception as e:
        print(f"  ERROR: {e}", flush=True)
        break

if scores:
    summary = summarize_scores(scores)
    output = json.dumps(summary, indent=2, ensure_ascii=True)
    sys.stdout.buffer.write(("\n" + output + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
