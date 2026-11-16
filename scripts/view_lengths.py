# /// script
# requires-python = ">=3.11"
# dependencies = ["pyarrow==26.0.1", "tiktoken==0.14.0"]
# ///
"""Estimate token lengths of the T and T+R reader views for every picked test step.

No reader calls. Token counts use tiktoken cl100k_base as a proxy tokenizer; the
real reader's tokenizer will differ.

For picked step k of an episode:
- T    = task description + every tool call of steps 1..k-1 + each matched tool
         output, each output capped at OUTPUT_CAP_TOKENS tokens.
- T+R  = T + reasoning of steps 1..k-1 + reasoning of step k.
Visible (non-reasoning) text is NOT included in either view, because the Step 3
rule for it is undecided; it is counted in separate columns. Formatting and
separator tokens, the system prompt and harness user messages are not counted.

Writes sample/view_lengths.csv (IDs and token counts only) and
sample/view_lengths_summary.json. Run from the repo root:
    uv run scripts/view_lengths.py
"""

import csv
import glob
import io
import json
import os
import statistics
import sys
from collections import defaultdict
from importlib.metadata import version

import pyarrow.parquet as pq
import tiktoken

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.parse_steps import load_episodes  # noqa: E402

TOKENIZER = "cl100k_base"
OUTPUT_CAP_TOKENS = 2000
THRESHOLDS = [100_000, 200_000, 1_000_000]
QUARTERS = ["early", "early_mid", "late_mid", "late"]

DATA_DIR = "data"
STEPS_CSV = os.path.join("sample", "steps.csv")
OUT_CSV = os.path.join("sample", "view_lengths.csv")
OUT_SUMMARY = os.path.join("sample", "view_lengths_summary.json")

HEADER = [
    "config", "task_id", "split", "quarter", "step_index",
    "task_tokens", "n_prior_steps", "prior_action_tokens",
    "prior_output_tokens_capped", "prior_output_tokens_uncapped", "n_prior_outputs_over_cap",
    "prior_reasoning_tokens", "current_reasoning_tokens",
    "prior_visible_text_tokens", "current_visible_text_tokens",
    "t_tokens", "tr_tokens",
]

ENC = tiktoken.get_encoding(TOKENIZER)


def n_tokens(text):
    # disallowed_special=() so text that looks like a special token is counted as plain text.
    return len(ENC.encode(text, disallowed_special=())) if text else 0


def action_text(call):
    return call.name + "\n" + json.dumps(call.arguments, ensure_ascii=False)


def output_text(out):
    if out is None:
        return ""
    parts = [out.content]
    if out.error_message:
        parts.append(out.error_message)
    return "\n".join(p for p in parts if p)


def step_token_counts(step):
    out_tokens = [n_tokens(output_text(o)) for o in step.outputs]
    return {
        "action": sum(n_tokens(action_text(c)) for c in step.tool_calls),
        "output_capped": sum(min(t, OUTPUT_CAP_TOKENS) for t in out_tokens),
        "output_uncapped": sum(out_tokens),
        "over_cap": sum(t > OUTPUT_CAP_TOKENS for t in out_tokens),
        "reasoning": n_tokens(step.reasoning),
        "visible": n_tokens(step.visible_text),
    }


def load_task_descriptions(config, episode_ids):
    files = sorted(glob.glob(os.path.join(DATA_DIR, "data", config, "*.parquet")))
    t = pq.read_table(files, columns=["episode_id", "main_task_description"],
                      filters=[("arm", "=", "honest"), ("episode_id", "in", list(episode_ids))])
    return dict(zip(t.column("episode_id").to_pylist(), t.column("main_task_description").to_pylist()))


def stats(values):
    values = sorted(values)
    p90 = statistics.quantiles(values, n=10, method="inclusive")[8]
    out = {"n": len(values), "median": statistics.median(values), "p90": p90, "max": values[-1]}
    for th in THRESHOLDS:
        out[f"n_over_{th}"] = sum(v > th for v in values)
    return out


def main():
    if not os.path.isdir(os.path.join(DATA_DIR, "data")):
        sys.exit(f"run from the repo root; expected the dataset under ./{DATA_DIR}/data")

    with open(STEPS_CSV, newline="") as f:
        picks = list(csv.DictReader(f))

    rows = []
    for config in sorted({p["config"] for p in picks}):
        cp = [p for p in picks if p["config"] == config]
        ep_ids = sorted({p["episode_id"] for p in cp})
        episodes = load_episodes(DATA_DIR, config, ep_ids)
        tasks = load_task_descriptions(config, ep_ids)
        per_step = {eid: [step_token_counts(s) for s in ep.steps] for eid, ep in episodes.items()}
        for p in cp:
            k = int(p["step_index"])
            steps = episodes[p["episode_id"]].steps
            assert steps[k - 1].step_index == k and steps[k - 1].message_index == int(p["message_index"])
            prior = per_step[p["episode_id"]][: k - 1]
            cur = per_step[p["episode_id"]][k - 1]
            task = n_tokens(tasks[p["episode_id"]])
            pa = sum(s["action"] for s in prior)
            po = sum(s["output_capped"] for s in prior)
            pr = sum(s["reasoning"] for s in prior)
            t_tokens = task + pa + po
            tr_tokens = t_tokens + pr + cur["reasoning"]
            rows.append((
                config, p["task_id"], p["split"], p["quarter"], k,
                task, k - 1, pa, po, sum(s["output_uncapped"] for s in prior), sum(s["over_cap"] for s in prior),
                pr, cur["reasoning"], sum(s["visible"] for s in prior), cur["visible"],
                t_tokens, tr_tokens,
            ))

    assert len(rows) == len(picks)
    for r in rows:
        assert all(isinstance(v, int) for v in r[4:]), "non-integer count"

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(HEADER)
    w.writerows(rows)

    groups = defaultdict(lambda: {"t": [], "tr": []})
    for r in rows:
        for key in (f"{r[0]}|{r[3]}", f"{r[0]}|all", f"all|{r[3]}", "all|all"):
            groups[key]["t"].append(r[15])
            groups[key]["tr"].append(r[16])

    summary = {
        "tokenizer": f"tiktoken {TOKENIZER} (proxy, not the reader's tokenizer)",
        "tiktoken_version": version("tiktoken"),
        "output_cap_tokens_per_tool_output": OUTPUT_CAP_TOKENS,
        "t_view": "task description + all tool calls of steps 1..k-1 (name + JSON arguments) + each matched tool output (content + error message) capped per output",
        "tr_view": "t_view + reasoning of steps 1..k-1 + reasoning of step k",
        "excluded": "visible text (counted separately), system prompt, harness user messages, formatting tokens",
        "percentile_method": "statistics.quantiles(n=10, method='inclusive')[8] for p90; statistics.median",
        "thresholds_are_strictly_greater": True,
        "by_config_and_quarter": {
            key: {"t": stats(g["t"]), "tr": stats(g["tr"])} for key, g in sorted(groups.items())
        },
    }

    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())
    with open(OUT_SUMMARY, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT_CSV} ({len(rows)} rows)\nwrote {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
