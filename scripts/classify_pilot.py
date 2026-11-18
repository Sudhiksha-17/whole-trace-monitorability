# /// script
# requires-python = ">=3.11"
# dependencies = ["pyarrow==26.0.1"]
# ///
"""Run the gold-action classifier (src/action_classifier.py) on the pilot test steps and draw a
stratified sample for hand-checking. Pilot only.

Run from the repo root, after the dataset is in ./data (see docs/FORMAT.md):

    uv run scripts/classify_pilot.py                    # 1. classify + write the hand-check pack
    (fill in debug/classifier/pilot/handcheck_verdicts.csv by hand, reading handcheck.txt)
    uv run scripts/classify_pilot.py --score            # 2. score the filled-in verdicts
    uv run scripts/classify_pilot.py --str-replace-keys # print the KEY NAMES of the one str_replace call

Committed outputs (IDs, labels, booleans, counts only; no commands, no targets):
  sample/classifier_pilot.csv, sample/classifier_pilot_summary.json, sample/classifier_validation.csv (after --score)
Gitignored outputs (CONTAIN TRANSCRIPT TEXT; never commit, share only with people who accepted the dataset terms):
  debug/classifier/pilot/handcheck.txt, debug/classifier/pilot/handcheck_verdicts.csv

Sample: up to --per-cell steps from every (config, action_type) cell (the stratified draw, used for the
weighted accuracy), plus extra steps that are most likely to be wrong: unknown commands, disagreements with
the leak extractor, and chains whose parts have different types. Extras are reported separately.
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
from src.action_classifier import TYPES, classify_tool_call, targets_match  # noqa: E402
from src.leak_rules import command_string, extract_target  # noqa: E402
from src.parse_steps import load_episodes  # noqa: E402

SEED = 20261113
DATA_DIR = "data"
STEPS_CSV = os.path.join("sample", "steps.csv")
OUT_CSV = os.path.join("sample", "classifier_pilot.csv")
OUT_JSON = os.path.join("sample", "classifier_pilot_summary.json")
VALIDATION_CSV = os.path.join("sample", "classifier_validation.csv")
DEBUG_DIR = os.path.join("debug", "classifier", "pilot")
HANDCHECK_TXT = os.path.join(DEBUG_DIR, "handcheck.txt")
VERDICTS_CSV = os.path.join(DEBUG_DIR, "handcheck_verdicts.csv")
NO_TARGET = ("none", "inline")

HEADER = ["config", "task_id", "split", "quarter", "step_index", "tool", "action_type", "target_is_none",
          "n_parts", "part_types", "mixed_chain", "chained", "piped", "heredoc", "unknown_cmd",
          "leak_extractor_agrees"]
VERDICT_HEADER = ["config", "task_id", "step_index", "reason", "auto_type", "auto_target",
                  "type_ok", "target_ok", "parts_ok", "correct_type", "correct_target", "note"]


def b(x):
    return "true" if x else "false"


def rng(*parts):
    return random.Random(int(hashlib.sha256("|".join(map(str, (SEED,) + parts)).encode()).hexdigest(), 16))


def agreement(our_target, extraction):
    """'na' when we give no target or the extractor found none; else whether any extracted target matches ours.
    The leak extractor records a URL as its host only, so a host equal to the host of our host/path agrees."""
    if our_target in NO_TARGET or not extraction.targets:
        return "na"
    for t in extraction.targets:
        if targets_match(t.value, our_target):
            return "true"
        if t.kind == "host" and our_target.split("/", 1)[0].split(":", 1)[0] == t.value.split(":", 1)[0]:
            return "true"
    return "false"


def load_picks(split="pilot", tool=None):
    with open(STEPS_CSV, newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["split"] == split]
    return [r for r in rows if tool is None or r["target_tool_name"] == tool]


def load_target_calls(picks):
    """(pick, step) for each pick, with the same consistency checks as scripts/leak_flags.py."""
    out = []
    for config in sorted({p["config"] for p in picks}):
        cp = [p for p in picks if p["config"] == config]
        episodes = load_episodes(DATA_DIR, config, sorted({p["episode_id"] for p in cp}))
        for p in cp:
            k = int(p["step_index"])
            step = episodes[p["episode_id"]].steps[k - 1]
            assert step.step_index == k and step.message_index == int(p["message_index"])
            assert step.target.name == p["target_tool_name"]
            out.append((p, step))
    return out


def classify(picks):
    rows, details = [], []
    for p, step in load_target_calls(picks):
        call = step.target
        r = classify_tool_call(call.name, call.arguments)
        ex = extract_target(call)
        part_types = sorted({t for t, _ in r["parts"]}, key=TYPES.index)
        row = {
            "config": p["config"], "task_id": p["task_id"], "split": p["split"], "quarter": p["quarter"],
            "step_index": int(p["step_index"]), "tool": call.name, "action_type": r["action_type"],
            "target_is_none": b(r["target"] in NO_TARGET), "n_parts": len(r["parts"]),
            "part_types": ";".join(part_types), "mixed_chain": b(len(part_types) > 1),
            "chained": b(r["chained"]), "piped": b(r["piped"]), "heredoc": b(r["heredoc"]),
            "unknown_cmd": b(r["unknown_cmd"]), "leak_extractor_agrees": agreement(r["target"], ex),
        }
        rows.append(row)
        details.append({"row": row, "call": call, "result": r, "ex": ex})
    return rows, details


def draw_sample(details, per_cell, extra_cap):
    """Stratified draw over (config, action_type), then the risky extras. Returns [(detail, reason)]."""
    chosen, seen = [], set()

    def key(d):
        return (d["row"]["config"], d["row"]["task_id"], d["row"]["step_index"])

    cells = defaultdict(list)
    for d in details:
        cells[(d["row"]["config"], d["row"]["action_type"])].append(d)
    for cell in sorted(cells):
        pool = cells[cell]
        for d in rng("stratum", *cell).sample(pool, min(per_cell, len(pool))):
            chosen.append((d, "stratum"))
            seen.add(key(d))
    extras = [
        ("unknown_cmd", lambda d: d["row"]["unknown_cmd"] == "true"),
        ("leak_disagree", lambda d: d["row"]["leak_extractor_agrees"] == "false"),
        ("mixed_chain", lambda d: d["row"]["mixed_chain"] == "true"),
    ]
    for name, pred in extras:
        pool = [d for d in details if pred(d) and key(d) not in seen]
        for d in rng("extra", name).sample(pool, min(extra_cap, len(pool))):
            chosen.append((d, name))
            seen.add(key(d))
    return chosen


def write_csv(path, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow([r[h] for h in header])
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())


def write_handcheck(sample):
    os.makedirs(DEBUG_DIR, exist_ok=True)
    with open(HANDCHECK_TXT, "w", encoding="utf-8") as f:
        f.write("# CONTAINS TRANSCRIPT TEXT. Do not commit. Rules: docs/ACTION_TAXONOMY.md\n")
        f.write("# For each step, fill the matching row of handcheck_verdicts.csv:\n")
        f.write("#   type_ok / target_ok / parts_ok = y or n; if n, write the right answer in correct_type / correct_target.\n")
        for i, (d, reason) in enumerate(sample, start=1):
            r, row, call = d["result"], d["row"], d["call"]
            raw = command_string(call) or json.dumps(call.arguments, ensure_ascii=False)
            f.write("=" * 100 + f"\n[{i}] {row['config']} task {row['task_id']} step {row['step_index']} "
                    f"| {row['quarter']} | tool={call.name} | sampled as: {reason}\n")
            f.write("--- raw call:\n" + raw[:3000] + ("\n[... cut]" if len(raw) > 3000 else "") + "\n")
            f.write(f"--- classifier: type={r['action_type']} target={r['target']}\n")
            f.write("    parts: " + " | ".join(f"{t} {g}" for t, g in r["parts"]) + "\n")
            f.write(f"    flags: chained={row['chained']} piped={row['piped']} heredoc={row['heredoc']} "
                    f"unknown_cmd={row['unknown_cmd']}\n")
            f.write("--- leak extractor targets: " + (", ".join(f"{t.kind}:{t.value}" for t in d["ex"].targets) or "(none)")
                    + f"  | agrees={row['leak_extractor_agrees']}\n")
    if os.path.exists(VERDICTS_CSV):
        print(f"WARNING: kept your existing {VERDICTS_CSV}. If the classifier changed since you filled it, "
              f"handcheck.txt may now list different steps or types: match rows by config/task_id/step_index, "
              f"or move the old file away to start a fresh hand-check.")
        return
    write_csv(VERDICTS_CSV, VERDICT_HEADER, [{
        "config": d["row"]["config"], "task_id": d["row"]["task_id"], "step_index": d["row"]["step_index"],
        "reason": reason, "auto_type": d["result"]["action_type"], "auto_target": d["result"]["target"],
        "type_ok": "", "target_ok": "", "parts_ok": "", "correct_type": "", "correct_target": "", "note": "",
    } for d, reason in sample])


def run(per_cell, extra_cap):
    rows, details = classify(load_picks("pilot"))
    write_csv(OUT_CSV, HEADER, rows)
    sample = draw_sample(details, per_cell, extra_cap)
    write_handcheck(sample)
    agree = Counter(r["leak_extractor_agrees"] for r in rows)
    summary = {
        "split": "pilot", "n_test_steps": len(rows), "seed": SEED, "per_cell": per_cell, "extra_cap": extra_cap,
        "action_type_by_config": {c: dict(sorted(Counter(r["action_type"] for r in rows if r["config"] == c).items()))
                                  for c in sorted({r["config"] for r in rows})},
        "action_type_overall": dict(sorted(Counter(r["action_type"] for r in rows).items())),
        "tool_overall": dict(sorted(Counter(r["tool"] for r in rows).items())),
        "flags": {f: sum(r[f] == "true" for r in rows) for f in ("chained", "piped", "heredoc", "unknown_cmd", "mixed_chain")},
        "target_is_none": sum(r["target_is_none"] == "true" for r in rows),
        "leak_extractor_agreement": {"agree": agree["true"], "disagree": agree["false"], "not_comparable": agree["na"]},
        "handcheck_sample": dict(sorted(Counter(reason for _, reason in sample).items())),
    }
    with open(OUT_JSON, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT_CSV} ({len(rows)} rows), {OUT_JSON}")
    print(f"wrote {HANDCHECK_TXT} and {VERDICTS_CSV} ({len(sample)} steps to check)  [transcript text: do not commit]")
    print(json.dumps({k: summary[k] for k in ("action_type_overall", "flags", "leak_extractor_agreement",
                                              "handcheck_sample")}, indent=2))


def score():
    """Read the filled verdicts; weighted accuracy over the stratified draw, plain counts for the extras."""
    with open(OUT_CSV, newline="") as f:
        population = Counter((r["config"], r["action_type"]) for r in csv.DictReader(f))
    with open(VERDICTS_CSV, newline="", encoding="utf-8") as f:
        verdicts = list(csv.DictReader(f))
    yes = {"y", "yes", "true", "1"}
    no = {"n", "no", "false", "0"}
    todo = [v for v in verdicts if v["type_ok"].strip().lower() not in yes | no]
    if todo:
        sys.exit(f"{len(todo)} of {len(verdicts)} rows have no type_ok verdict yet; fill them in first")

    def ok(v, col):
        x = v[col].strip().lower()
        return None if x not in yes | no else x in yes

    out = {}
    for col in ("type_ok", "target_ok", "parts_ok"):
        strat = [v for v in verdicts if v["reason"] == "stratum" and ok(v, col) is not None]
        cells = defaultdict(list)
        for v in strat:
            cells[(v["config"], v["auto_type"])].append(ok(v, col))
        total = sum(population[c] for c in cells)
        weighted = sum(population[c] * (sum(x) / len(x)) for c, x in cells.items()) / total if total else None
        out[col] = {
            "stratified": {"n_checked": len(strat), "n_ok": sum(ok(v, col) for v in strat),
                           "weighted_accuracy": None if weighted is None else round(weighted, 4),
                           "by_type": {t: {"n": sum(1 for v in strat if v["auto_type"] == t),
                                           "ok": sum(ok(v, col) for v in strat if v["auto_type"] == t)}
                                       for t in TYPES if any(v["auto_type"] == t for v in strat)}},
            "extras": {reason: {"n": sum(1 for v in verdicts if v["reason"] == reason and ok(v, col) is not None),
                                "ok": sum(1 for v in verdicts if v["reason"] == reason and ok(v, col))}
                       for reason in sorted({v["reason"] for v in verdicts} - {"stratum"})},
        }
    # committed copy: IDs and booleans only (no targets, no notes)
    write_csv(VALIDATION_CSV, ["config", "task_id", "step_index", "reason", "auto_type", "type_ok", "target_ok", "parts_ok"],
              [{**{k: v[k] for k in ("config", "task_id", "step_index", "reason", "auto_type")},
                **{c: "" if ok(v, c) is None else b(ok(v, c)) for c in ("type_ok", "target_ok", "parts_ok")}}
               for v in verdicts])
    with open(OUT_JSON, encoding="utf-8") as f:
        summary = json.load(f)
    summary["validation"] = out
    with open(OUT_JSON, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(out, indent=2))
    print(f"wrote {VALIDATION_CSV} and added 'validation' to {OUT_JSON}")
    print("Every n verdict is a classifier bug or a taxonomy gap: fix the rule, add a test, rerun.")


def str_replace_keys():
    """Print only the tool name and argument KEY names of every picked str_replace call (any split)."""
    picks = load_picks("pilot", "str_replace") + load_picks("main", "str_replace")
    if not picks:
        print("no picked step uses str_replace")
        return
    for p, step in load_target_calls(picks):
        call = step.target
        r = classify_tool_call(call.name, call.arguments)
        print(f"{p['config']} task {p['task_id']} step {p['step_index']} ({p['split']}): tool={call.name} "
              f"argument keys={sorted(call.arguments)} -> classifier type={r['action_type']}, "
              f"target found={'path' in call.arguments}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-cell", type=int, default=3, help="steps per (config, action_type) cell")
    ap.add_argument("--extra-cap", type=int, default=10, help="max extra steps per risk bucket")
    ap.add_argument("--score", action="store_true", help="score the filled-in hand-check verdicts")
    ap.add_argument("--str-replace-keys", action="store_true", help="print argument key names of str_replace calls")
    args = ap.parse_args()
    if not os.path.isfile(STEPS_CSV):
        sys.exit("run from the repo root")
    if args.score:
        return score()
    if not os.path.isdir(os.path.join(DATA_DIR, "data")):
        sys.exit("expected the dataset under ./data/data (see docs/FORMAT.md)")
    if args.str_replace_keys:
        return str_replace_keys()
    run(args.per_cell, args.extra_cap)


if __name__ == "__main__":
    main()
