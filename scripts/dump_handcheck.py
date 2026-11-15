# /// script
# requires-python = ">=3.11"
# dependencies = ["pyarrow==26.0.1"]
# ///
"""Write readable per-step dumps of a few pilot episodes for hand-checking the parser.

Output goes to debug/handcheck/ (gitignored). It CONTAINS TRANSCRIPT TEXT: never
commit it, and share it only with people who have accepted the dataset terms.

Picks are read from sample/steps.csv, so run scripts/sample_steps.py first.

Run from the repo root:
    uv run scripts/dump_handcheck.py            # 3 pilot episodes per config
    uv run scripts/dump_handcheck.py --n 5
"""

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.parse_steps import load_episodes  # noqa: E402

DATA_DIR = "data"
OUT_DIR = os.path.join("debug", "handcheck")
OUTPUT_CHARS = 500


def truncate(text, limit=OUTPUT_CHARS):
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... truncated, {len(text)} chars total]"


def render(ep, split, picks):
    eligible = [s for s in ep.steps if s.eligible]
    lines = [
        f"# {ep.config} | task {ep.task_id} | {ep.episode_id} | split={split}",
        f"# n_total={len(ep.steps)} n_eligible={len(eligible)}",
        "# picked: " + ", ".join(f"step {i} ({q})" for i, q in sorted(picks.items())),
        "",
    ]
    for s in ep.steps:
        mark = f"  <<< PICKED ({picks[s.step_index]})" if s.step_index in picks else ""
        lines.append("=" * 100)
        lines.append(
            f"STEP {s.step_index} | message_index={s.message_index} | eligible={s.eligible} | "
            f"is_final={s.is_final} | n_tool_calls={s.n_tool_calls} | "
            f"reasoning_block={s.has_reasoning_block}{mark}"
        )
        lines.append("-" * 40 + " reasoning")
        lines.append(s.reasoning if s.reasoning else "(empty)")
        lines.append("-" * 40 + " visible text")
        lines.append(s.visible_text if s.visible_text.strip() else "(empty)")
        for j, (call, out) in enumerate(zip(s.tool_calls, s.outputs), start=1):
            target = "  [TARGET]" if j == 1 and s.step_index in picks else ""
            lines.append("-" * 40 + f" tool call {j}/{s.n_tool_calls}: {call.name} id={call.id}{target}")
            lines.append(json.dumps(call.arguments, ensure_ascii=False, indent=2))
            if out is None:
                lines.append("-" * 40 + f" output {j}: (MISSING)")
            else:
                err = f" error={out.error_type}: {out.error_message}" if out.error_type else ""
                lines.append("-" * 40 + f" output {j} (tool_call_id={out.tool_call_id}){err}")
                lines.append(truncate(out.content) if out.content else "(empty)")
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=3, help="pilot episodes per config")
    args = parser.parse_args()

    with open(os.path.join("sample", "tasks.csv"), newline="") as f:
        pilot_tasks = [r["task_id"] for r in csv.DictReader(f) if r["split"] == "pilot"][: args.n]
    with open(os.path.join("sample", "steps.csv"), newline="") as f:
        picks_rows = list(csv.DictReader(f))
    with open(os.path.join("sample", "episodes.csv"), newline="") as f:
        sel = [r for r in csv.DictReader(f) if r["task_id"] in pilot_tasks]

    for config in sorted({r["config"] for r in sel}):
        rows = [r for r in sel if r["config"] == config]
        episodes = load_episodes(DATA_DIR, config, [r["episode_id"] for r in rows])
        out_dir = os.path.join(OUT_DIR, config)
        os.makedirs(out_dir, exist_ok=True)
        for r in rows:
            picks = {
                int(p["step_index"]): p["quarter"]
                for p in picks_rows
                if p["config"] == config and p["episode_id"] == r["episode_id"]
            }
            path = os.path.join(out_dir, f"{r['task_id']}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(render(episodes[r["episode_id"]], r["split"], picks))
            print(f"wrote {path}")


if __name__ == "__main__":
    main()
