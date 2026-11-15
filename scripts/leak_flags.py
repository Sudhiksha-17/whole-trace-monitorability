# /// script
# requires-python = ">=3.11"
# dependencies = ["pyarrow==26.0.1"]
# ///
"""Leak flags for every test step (rules: docs/LEAK_RULES.md, module src/leak_rules.py).

For each test step k: extract the targets of the target call (first tool call of
step k), then flag whether step k's reasoning / visible text names the full
command or a target, plus diagnostics.

Committed outputs (IDs, booleans, labels, counts only):
  sample/leak_flags.csv, sample/leak_flags_summary.json
Gitignored outputs (contain transcript text):
  views/{split}/{config}/{task_id}/step_{k}/targets.json
  debug/leaks/{split}/extraction_handcheck.txt, debug/leaks/{split}/validation.txt
Manual validation verdicts are read from sample/leak_validation.csv if present.

Ground rule: pilot only until the rules are frozen in the pre-registration.
Run after scripts/build_views.py (which rebuilds views/{split}/):
    uv run scripts/leak_flags.py
"""

import argparse
import csv
import hashlib
import io
import json
import os
import random
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.leak_rules import (  # noqa: E402
    PARSER, command_string, core_command, extract_target, find_basename, find_target,
    norm_command, norm_text,
)
from src.parse_steps import load_episodes  # noqa: E402

SEED = 20261113
DATA_DIR = "data"
STEPS_CSV = os.path.join("sample", "steps.csv")
VALIDATION_CSV = os.path.join("sample", "leak_validation.csv")
QUARTERS = ["early", "early_mid", "late_mid", "late"]
CATEGORIES = ["command", "target_only", "none"]
N_HANDCHECK_PER_CONFIG = 20
N_PRECISION = 20
N_RECALL = 30
CONTEXT = 60

FLAG_COLUMNS = [
    "command_leak_reasoning", "command_leak_visible", "command_leak",
    "target_leak_reasoning", "target_leak_visible", "target_leak",
    "basename_only_mention", "target_in_earlier_reasoning", "command_core_leak",
]
HEADER = ["config", "task_id", "split", "quarter", "step_index", "extraction_status", "n_targets",
          "target_kinds"] + FLAG_COLUMNS + ["leak_category"]


def b(x):
    return "true" if x else "false"


def rng(*parts):
    return random.Random(int(hashlib.sha256("|".join(map(str, (SEED,) + parts)).encode()).hexdigest(), 16))


def context(text, span):
    s, e = span
    return f"...{text[max(0, s - CONTEXT):s]}[[{text[s:e]}]]{text[e:e + CONTEXT]}..."


def flags_for(step_k, prior_steps):
    call = step_k.target
    ex = extract_target(call)
    r, v = norm_text(step_k.reasoning), norm_text(step_k.visible_text)
    earlier = "\n".join(norm_text(s.reasoning) + "\n" + norm_text(s.visible_text) for s in prior_steps)

    cmd = command_string(call)
    ncmd = norm_command(cmd) if cmd else ""
    core = core_command(call)
    ncore = norm_command(core) if core else ""

    m = {}
    m["command_leak_reasoning"] = (r.find(ncmd), len(ncmd), "reasoning") if ncmd and ncmd in r else None
    m["command_leak_visible"] = (v.find(ncmd), len(ncmd), "visible") if ncmd and ncmd in v else None
    m["target_leak_reasoning"] = find_target(r, ex.targets)
    m["target_leak_visible"] = find_target(v, ex.targets)
    m["command_core_leak"] = ncore and (ncore in r or ncore in v)
    target_leak = bool(m["target_leak_reasoning"] or m["target_leak_visible"])
    base_r, base_v = find_basename(r, ex.targets), find_basename(v, ex.targets)
    m["basename_only_mention"] = None if target_leak else (base_r or base_v)
    m["target_in_earlier_reasoning"] = find_target(earlier, ex.targets)

    flags = {
        "command_leak_reasoning": bool(m["command_leak_reasoning"]),
        "command_leak_visible": bool(m["command_leak_visible"]),
        "target_leak_reasoning": bool(m["target_leak_reasoning"]),
        "target_leak_visible": bool(m["target_leak_visible"]),
        "basename_only_mention": bool(m["basename_only_mention"]),
        "target_in_earlier_reasoning": bool(m["target_in_earlier_reasoning"]),
        "command_core_leak": bool(m["command_core_leak"]),
    }
    flags["command_leak"] = flags["command_leak_reasoning"] or flags["command_leak_visible"]
    flags["target_leak"] = flags["target_leak_reasoning"] or flags["target_leak_visible"]
    category = "command" if flags["command_leak"] else "target_only" if flags["target_leak"] else "none"
    texts = {"reasoning": r, "visible": v, "earlier": earlier, "ncmd": ncmd, "ncore": ncore,
             "base_text": "reasoning" if base_r else "visible"}
    return ex, flags, category, m, texts


def rate(rows, key):
    return {"n": len(rows), "positive": sum(r[key] for r in rows)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["pilot", "main"], default="pilot")
    split = ap.parse_args().split
    if not os.path.isdir(os.path.join(DATA_DIR, "data")):
        sys.exit("run from the repo root; expected the dataset under ./data/data")
    suffix = "" if split == "pilot" else f"_{split}"
    out_csv = os.path.join("sample", f"leak_flags{suffix}.csv")
    out_json = os.path.join("sample", f"leak_flags_summary{suffix}.json")
    debug_dir = os.path.join("debug", "leaks", split)
    os.makedirs(debug_dir, exist_ok=True)

    with open(STEPS_CSV, newline="") as f:
        picks = [r for r in csv.DictReader(f) if r["split"] == split]

    rows, details = [], []
    for config in sorted({p["config"] for p in picks}):
        cp = [p for p in picks if p["config"] == config]
        episodes = load_episodes(DATA_DIR, config, sorted({p["episode_id"] for p in cp}))
        for p in cp:
            k = int(p["step_index"])
            steps = episodes[p["episode_id"]].steps
            step_k = steps[k - 1]
            assert step_k.step_index == k and step_k.message_index == int(p["message_index"])
            ex, flags, category, m, texts = flags_for(step_k, steps[: k - 1])
            assert category == ("command" if flags["command_leak"] else "target_only" if flags["target_leak"] else "none")
            kinds = ";".join(sorted({t.kind for t in ex.targets}))
            rows.append({
                "config": config, "task_id": p["task_id"], "split": split, "quarter": p["quarter"], "step_index": k,
                "extraction_status": ex.status, "n_targets": len(ex.targets), "target_kinds": kinds,
                **flags, "leak_category": category,
            })
            details.append({"row": rows[-1], "call": step_k.target, "ex": ex, "m": m, "texts": texts})
            step_dir = os.path.join("views", split, config, p["task_id"], f"step_{k}")
            if os.path.isdir(step_dir):
                with open(os.path.join(step_dir, "targets.json"), "w", encoding="utf-8") as f:
                    json.dump({"extraction_status": ex.status, "reasons": ex.reasons,
                               "targets": [{"kind": t.kind, "value": t.value, "origin": t.origin} for t in ex.targets]},
                              f, indent=2, sort_keys=True, ensure_ascii=False)
                    f.write("\n")

    assert len(rows) == len(picks)
    assert len({(r["config"], r["task_id"], r["step_index"]) for r in rows}) == len(rows)

    # Committed CSV.
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(HEADER)
    for r in rows:
        w.writerow([r[h] if h not in FLAG_COLUMNS else b(r[h]) for h in HEADER])
    with open(out_csv, "w", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())

    # Debug: extraction hand-check (stratified by config, seeded).
    with open(os.path.join(debug_dir, "extraction_handcheck.txt"), "w", encoding="utf-8") as f:
        f.write("# CONTAINS TRANSCRIPT TEXT. Do not commit.\n")
        for config in sorted({d["row"]["config"] for d in details}):
            pool = [d for d in details if d["row"]["config"] == config]
            for d in rng("handcheck", config).sample(pool, min(N_HANDCHECK_PER_CONFIG, len(pool))):
                r, call, ex = d["row"], d["call"], d["ex"]
                f.write("=" * 100 + f"\n{r['config']} task {r['task_id']} step {r['step_index']} | tool={call.name} | status={ex.status} reasons={ex.reasons}\n")
                f.write("--- raw call:\n" + (command_string(call) or json.dumps(call.arguments, ensure_ascii=False))[:3000] + "\n")
                f.write("--- targets:\n" + "\n".join(f"  {t.kind:12s} {t.origin:12s} {t.value}" for t in ex.targets) + "\n")

    # Debug: validation samples.
    def write_match(f, d, key):
        m, t = d["m"][key], d["texts"]
        if key.startswith("command_leak"):
            src = "reasoning" if key.endswith("reasoning") else "visible"
            f.write(f"  [{key}] {context(t[src], (m[0], m[0] + m[1]))}\n")
        elif key == "basename_only_mention":
            f.write(f"  [{key}] target={m[0].value} form={m[1]} | {context(t[t['base_text']], m[2])}\n")
        elif key == "target_in_earlier_reasoning":
            f.write(f"  [{key}] target={m[0].value} form={m[1]} | {context(t['earlier'], m[2])}\n")
        else:
            src = "reasoning" if key.endswith("reasoning") else "visible"
            f.write(f"  [{key}] target={m[0].value} form={m[1]} | {context(t[src], m[2])}\n")

    checks = {
        "command": lambda d: d["row"]["leak_category"] == "command",
        "target_only": lambda d: d["row"]["leak_category"] == "target_only",
        "basename_only_mention": lambda d: d["row"]["basename_only_mention"],
        "target_in_earlier_reasoning": lambda d: d["row"]["target_in_earlier_reasoning"],
    }
    with open(os.path.join(debug_dir, "validation.txt"), "w", encoding="utf-8") as f:
        f.write("# CONTAINS TRANSCRIPT TEXT. Do not commit.\n")
        for name, pred in checks.items():
            pool = [d for d in details if pred(d)]
            sample = rng("precision", name).sample(pool, min(N_PRECISION, len(pool)))
            f.write("\n" + "#" * 100 + f"\n# PRECISION: {name} ({len(sample)} of {len(pool)})\n")
            for d in sample:
                r = d["row"]
                f.write("=" * 100 + f"\n{r['config']} task {r['task_id']} step {r['step_index']} | status={r['extraction_status']} | cmd={d['texts']['ncmd'][:300]!r}\n")
                f.write("  targets: " + ", ".join(f"{t.kind}:{t.value}" for t in d["ex"].targets) + "\n")
                keys = {"command": ["command_leak_reasoning", "command_leak_visible"],
                        "target_only": ["target_leak_reasoning", "target_leak_visible"],
                        "basename_only_mention": ["basename_only_mention"],
                        "target_in_earlier_reasoning": ["target_in_earlier_reasoning"]}[name]
                for key in keys:
                    if d["m"][key]:
                        write_match(f, d, key)
        pool = [d for d in details if d["row"]["leak_category"] == "none"]
        sample = rng("recall", "none").sample(pool, min(N_RECALL, len(pool)))
        f.write("\n" + "#" * 100 + f"\n# RECALL: steps flagged none ({len(sample)} of {len(pool)})\n")
        for d in sample:
            r, t = d["row"], d["texts"]
            f.write("=" * 100 + f"\n{r['config']} task {r['task_id']} step {r['step_index']} | status={r['extraction_status']}\n")
            f.write(f"  cmd: {t['ncmd'][:400]!r}\n  targets: " + ", ".join(f"{x.kind}:{x.value}" for x in d["ex"].targets) + "\n")
            f.write(f"  reasoning: {t['reasoning'][-1200:]}\n  visible: {t['visible'][:600]}\n")

    # Summary.
    groups = defaultdict(list)
    for r in rows:
        for key in (f"{r['config']}|{r['quarter']}", f"{r['config']}|all", f"all|{r['quarter']}", "all|all"):
            groups[key].append(r)
    summary = {
        "split": split,
        "parser": PARSER,
        "n_test_steps": len(rows),
        "extraction_status_by_config": {
            c: dict(sorted(Counter(r["extraction_status"] for r in rows if r["config"] == c).items()))
            for c in sorted({r["config"] for r in rows})
        },
        "target_kinds_overall": dict(sorted(Counter(k for r in rows for k in r["target_kinds"].split(";") if k).items())),
        "rates": {
            key: {
                "n": len(g),
                "leak_category": {c: sum(r["leak_category"] == c for r in g) for c in CATEGORIES},
                **{flag: sum(r[flag] for r in g) for flag in FLAG_COLUMNS},
            }
            for key, g in sorted(groups.items())
        },
    }
    if os.path.exists(VALIDATION_CSV):
        with open(VALIDATION_CSV, newline="") as f:
            val = list(csv.DictReader(f))
        out = defaultdict(lambda: defaultdict(Counter))
        for v in val:
            out[v["round"]][v["check"]][v["verdict"]] += 1
        summary["validation"] = {rd: {chk: dict(sorted(c.items())) for chk, c in sorted(d.items())} for rd, d in sorted(out.items())}
    with open(out_json, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out_csv} ({len(rows)} rows), {out_json}, {debug_dir}/")


if __name__ == "__main__":
    main()
