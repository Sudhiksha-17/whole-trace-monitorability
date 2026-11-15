# /// script
# requires-python = ">=3.11"
# dependencies = ["pyarrow==26.0.1"]
# ///
"""Step 2 of the methodology: break each episode into steps and pick test steps.

For each of the 210 episodes in sample/episodes.csv, parse the steps, list the
eligible ones, split them into four quarters by eligible rank, and pick one step
per quarter with a seed derived by SHA-256. Rules: docs/step_rules.md.

Only IDs, indices, counts, booleans, lengths and tool names are written.

Run from the repo root:
    uv run scripts/sample_steps.py
"""

import csv
import hashlib
import io
import json
import os
import random
import re
import statistics
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.parse_steps import load_episodes  # noqa: E402

GLOBAL_SEED = 20261113
SEED_FORMAT = "{GLOBAL_SEED}|{config}|{task_id}|{q}"
QUARTERS = ["early", "early_mid", "late_mid", "late"]
MIN_ELIGIBLE = len(QUARTERS)

DATA_DIR = "data"
EPISODES_CSV = os.path.join("sample", "episodes.csv")
STEPS_CSV = os.path.join("sample", "steps.csv")
EPISODE_STEPS_CSV = os.path.join("sample", "episode_steps.csv")
SUMMARY_JSON = os.path.join("sample", "steps_summary.json")

STEPS_HEADER = [
    "config", "task_id", "episode_id", "split", "quarter", "step_index", "message_index",
    "eligible_rank", "n_total", "n_eligible", "relative_position", "n_tool_calls", "target_tool_name",
]
EPISODE_STEPS_HEADER = [
    "config", "task_id", "episode_id", "step_index", "message_index", "eligible", "is_final",
    "n_tool_calls", "reasoning_chars", "visible_text_chars", "has_output",
]

# Patterns every committed value must match: IDs, numbers, booleans, tool names.
ID_RE = re.compile(r"^[0-9]+$")
CONFIG_RE = re.compile(r"^basharena-[a-z0-9.-]+$")
EPISODE_ID_RE = re.compile(r"^[0-9]+__honest$")
TOOL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
FLOAT_RE = re.compile(r"^[0-9]+(\.[0-9]+)?(e-?[0-9]+)?$")
BOOL_VALUES = {"true", "false"}
COLUMN_CHECKS = {
    "config": CONFIG_RE.match,
    "task_id": ID_RE.match,
    "episode_id": EPISODE_ID_RE.match,
    "split": {"main", "pilot"}.__contains__,
    "quarter": set(QUARTERS).__contains__,
    "relative_position": FLOAT_RE.match,
    "target_tool_name": TOOL_NAME_RE.match,
    "eligible": BOOL_VALUES.__contains__,
    "is_final": BOOL_VALUES.__contains__,
    "has_output": BOOL_VALUES.__contains__,
}
INT_COLUMNS = {
    "step_index", "message_index", "eligible_rank", "n_total", "n_eligible", "n_tool_calls",
    "reasoning_chars", "visible_text_chars",
}


def pick_seed(config, task_id, q):
    seed_str = SEED_FORMAT.format(GLOBAL_SEED=GLOBAL_SEED, config=config, task_id=task_id, q=q)
    return int(hashlib.sha256(seed_str.encode()).hexdigest(), 16)


def quarter_of(eligible_rank, n_eligible):
    return (4 * eligible_rank) // n_eligible


def b(value):
    return "true" if value else "false"


def read_selected():
    with open(EPISODES_CSV, newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 210, f"expected 210 selected episodes, found {len(rows)}"
    return rows


def pick_steps(config, task_id, eligible):
    """Return [(q, eligible_rank, step)] with one pick per quarter."""
    n = len(eligible)
    by_quarter = {q: [] for q in range(4)}
    for rank, step in enumerate(eligible):
        by_quarter[quarter_of(rank, n)].append((rank, step))
    picks = []
    for q in range(4):
        rank, step = random.Random(pick_seed(config, task_id, q)).choice(by_quarter[q])
        picks.append((q, rank, step))
    return picks


def to_csv(header, rows):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue()


def five_numbers(values):
    q1, med, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return {"min": min(values), "q1": q1, "median": med, "q3": q3, "max": max(values), "n_episodes": len(values)}


def build_outputs(selected, episodes):
    step_rows, episode_step_rows = [], []
    n_eligible_by_config = {}
    short_episodes, no_final_submit, multi_submit = [], [], []
    multi_call_steps = Counter()
    no_reasoning_block_steps = Counter()
    zero_call_steps = Counter()

    for sel in selected:
        config, task_id, episode_id, split = sel["config"], sel["task_id"], sel["episode_id"], sel["split"]
        ep = episodes[config][episode_id]
        assert ep.task_id == task_id, f"{config} {episode_id}: task_id mismatch"
        steps = ep.steps
        n_total = len(steps)
        eligible = [s for s in steps if s.eligible]
        n_eligible = len(eligible)
        n_eligible_by_config.setdefault(config, []).append(n_eligible)

        if not ep.has_final_submit:
            no_final_submit.append({"config": config, "episode_id": episode_id})
        if ep.n_submit_steps > 1:
            multi_submit.append({"config": config, "episode_id": episode_id, "n_submit_steps": ep.n_submit_steps})

        for s in steps:
            multi_call_steps[config] += s.n_tool_calls > 1
            no_reasoning_block_steps[config] += not s.has_reasoning_block
            zero_call_steps[config] += s.n_tool_calls == 0
            episode_step_rows.append((
                config, task_id, episode_id, s.step_index, s.message_index, b(s.eligible), b(s.is_final),
                s.n_tool_calls, len(s.reasoning), len(s.visible_text), b(s.has_output),
            ))

        if n_eligible < MIN_ELIGIBLE:
            short_episodes.append({"config": config, "episode_id": episode_id, "split": split, "n_eligible": n_eligible})
            continue

        picks = pick_steps(config, task_id, eligible)

        # Checks: one pick per quarter, 4 distinct eligible non-final steps that exist.
        assert [q for q, _, _ in picks] == [0, 1, 2, 3]
        assert len({s.step_index for _, _, s in picks}) == 4, f"{config} {episode_id}: repeated pick"
        by_index = {s.step_index: s for s in steps}
        for q, rank, s in picks:
            assert s.eligible and not s.is_final, f"{config} {episode_id}: picked ineligible step"
            assert by_index.get(s.step_index) is s and s.message_index == steps[s.step_index - 1].message_index
            assert quarter_of(rank, n_eligible) == q
            step_rows.append((
                config, task_id, episode_id, split, QUARTERS[q], s.step_index, s.message_index, rank,
                n_total, n_eligible, repr(s.step_index / n_total), s.n_tool_calls, s.target.name,
            ))

    picked_multi = sum(1 for r in step_rows if r[11] > 1)
    summary = {
        "global_seed": GLOBAL_SEED,
        "seed_string_format": SEED_FORMAT,
        "seed_derivation": "int(hashlib.sha256(seed_str.encode()).hexdigest(), 16) -> random.Random(seed).choice(steps_in_quarter)",
        "quarter_formula": "q = floor(4 * eligible_rank / n_eligible), eligible_rank 0-indexed",
        "quantile_method": "statistics.quantiles(n=4, method='inclusive')",
        "n_episodes": len(selected),
        "n_picked_steps": len(step_rows),
        "picked_steps_by_split_and_config": {
            f"{split}|{config}": n
            for (split, config), n in sorted(Counter((r[3], r[0]) for r in step_rows).items())
        },
        "n_eligible_distribution": {c: five_numbers(v) for c, v in sorted(n_eligible_by_config.items())},
        "episodes_with_n_eligible_below_4": short_episodes,
        "steps_with_multiple_tool_calls": {
            "all_steps_by_config": dict(sorted(multi_call_steps.items())),
            "all_steps_total": sum(multi_call_steps.values()),
            "picked_steps": picked_multi,
        },
        "steps_without_reasoning_block_by_config": dict(sorted(no_reasoning_block_steps.items())),
        "steps_with_zero_tool_calls_by_config": dict(sorted(zero_call_steps.items())),
        "episodes_without_final_submit": len(no_final_submit),
        "episodes_without_final_submit_ids": no_final_submit,
        "episodes_with_more_than_one_submit_step": multi_submit,
        "target_tool_names": dict(sorted(Counter(r[12] for r in step_rows).items())),
    }

    return {
        STEPS_CSV: to_csv(STEPS_HEADER, step_rows),
        EPISODE_STEPS_CSV: to_csv(EPISODE_STEPS_HEADER, episode_step_rows),
        SUMMARY_JSON: json.dumps(summary, indent=2, sort_keys=True) + "\n",
    }


def scan_csv(content):
    """Assert every value is an ID, number, boolean or tool name."""
    rows = list(csv.DictReader(io.StringIO(content)))
    for row in rows:
        for col, value in row.items():
            if col in INT_COLUMNS:
                assert ID_RE.match(value), f"{col}: non-integer value"
            else:
                assert COLUMN_CHECKS[col](value), f"{col}: value fails the no-text check"
    return len(rows)


def scan_json(value, path="$"):
    """Assert every JSON string is an ID, tool name, config, label or one of the fixed rule strings."""
    if isinstance(value, dict):
        for k, v in value.items():
            assert TOOL_NAME_RE.match(k) or CONFIG_RE.match(k) or re.match(r"^(main|pilot)\|basharena-[a-z0-9.-]+$", k), f"{path}: key fails check"
            if k in ("seed_string_format", "seed_derivation", "quarter_formula", "quantile_method"):
                continue  # fixed strings defined in this script
            scan_json(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            scan_json(v, f"{path}[{i}]")
    elif isinstance(value, str):
        assert CONFIG_RE.match(value) or EPISODE_ID_RE.match(value) or value in ("main", "pilot"), f"{path}: string fails check"
    else:
        assert value is None or isinstance(value, (int, float, bool)), f"{path}: unexpected type"


def main():
    if not os.path.isdir(os.path.join(DATA_DIR, "data")):
        sys.exit(f"run from the repo root; expected the dataset under ./{DATA_DIR}/data")

    selected = read_selected()
    configs = sorted({r["config"] for r in selected})
    episodes = {
        c: load_episodes(DATA_DIR, c, [r["episode_id"] for r in selected if r["config"] == c])
        for c in configs
    }

    outputs = build_outputs(selected, episodes)
    assert outputs == build_outputs(selected, episodes), "sampling is not deterministic within a run"

    scan_csv(outputs[STEPS_CSV])
    scan_csv(outputs[EPISODE_STEPS_CSV])
    scan_json(json.loads(outputs[SUMMARY_JSON]))

    for path, content in outputs.items():
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
