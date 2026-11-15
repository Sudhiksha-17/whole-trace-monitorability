# /// script
# requires-python = ">=3.11"
# dependencies = ["pyarrow==26.0.1", "tiktoken==0.14.0"]
# ///
"""Step 3 of the methodology: build the four reader views for each test step.

Views (k = the test step's 1-indexed step_index; step k's action = ALL its tool
calls and outputs):
- T0:     task description only.
- R_only: task + reasoning and visible text of steps 1..k.
- T:      task + tool calls, outputs and harness messages of steps 1..k-1.
- T_plus_R: task + steps 1..k-1 in full + reasoning and visible text of step k.
Every view ends with the line "This is step {k} of the episode."

Rules and format: docs/DECISIONS.md section 4.

Views contain transcript text and are written to the gitignored views/ folder.
Only sample/views_manifest*.csv and sample/views_summary*.json (IDs, numbers,
labels and hashes) are meant to be committed.

Ground rule: build the pilot split only until the pilot is done and the
pre-registration is frozen. `--split main` exists but must not be run before then.

Run from the repo root:
    uv run scripts/build_views.py               # pilot (default)
"""

import argparse
import csv
import glob
import hashlib
import io
import json
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass

import pyarrow.parquet as pq
import tiktoken

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.parse_steps import load_episodes  # noqa: E402

DATA_DIR = "data"
STEPS_CSV = os.path.join("sample", "steps.csv")
VIEW_LENGTHS_CSV = os.path.join("sample", "view_lengths.csv")
VIEWS_ROOT = "views"
EXAMPLES_DIR = os.path.join(VIEWS_ROOT, "examples")

TOKENIZER = "cl100k_base"
OUTPUT_CAP_TOKENS = 2000
TRUNCATION_MARKER = "[output truncated]"
FINAL_LINE = "This is step {k} of the episode."
VIEWS = ["T0", "R_only", "T", "T_plus_R"]
VIEW_FILES = {"T0": "T0.txt", "R_only": "R_only.txt", "T": "T.txt", "T_plus_R": "T_plus_R.txt"}
QUARTERS = ["early", "early_mid", "late_mid", "late"]

# Kinds of rendered elements. Every element records where it came from.
TASK, REASONING, VISIBLE, ACTION, OUTPUT, HARNESS = "task", "reasoning", "visible_text", "action", "output", "harness"
AGENT_TEXT = {REASONING, VISIBLE}
ENV_HISTORY = {ACTION, OUTPUT, HARNESS}

# Which element kinds each view takes, and from which steps ("upto" k-1 or k).
VIEW_RULES = {
    "T0": {},
    "R_only": {REASONING: "k", VISIBLE: "k"},
    "T": {ACTION: "k-1", OUTPUT: "k-1", HARNESS: "k-1"},
    "T_plus_R": {REASONING: "k", VISIBLE: "k", ACTION: "k-1", OUTPUT: "k-1", HARNESS: "k-1"},
}

ENC = tiktoken.get_encoding(TOKENIZER)


def n_tokens(text):
    return len(ENC.encode(text, disallowed_special=()))


@dataclass(frozen=True)
class Element:
    kind: str
    step: int | None  # step_index the element belongs to (None for task)
    message_index: int  # position in messages_json, used for ordering and the harness rule
    order: int  # tie-break within a message (call j, output j)
    text: str
    tool: str | None = None
    truncated: bool = False

    @property
    def key(self):
        return (self.kind, self.message_index, self.order)


def render_call(call):
    """bash/python as their command/code string; anything else as canonical JSON."""
    args = call.arguments
    if call.name == "bash" and set(args) == {"command"} and isinstance(args["command"], str):
        return args["command"]
    if call.name == "python" and set(args) == {"code"} and isinstance(args["code"], str):
        return args["code"]
    return json.dumps(args, sort_keys=True, ensure_ascii=False)


def cap_output(text):
    tokens = ENC.encode(text, disallowed_special=())
    if len(tokens) <= OUTPUT_CAP_TOKENS:
        return text, False
    return ENC.decode(tokens[:OUTPUT_CAP_TOKENS]) + "\n" + TRUNCATION_MARKER, True


def output_text(out):
    parts = [out.content]
    if out.error_message:
        parts.append(out.error_message)
    return "\n".join(p for p in parts if p)


def episode_elements(episode, messages, task_text):
    """All renderable elements of an episode, each tagged with its source."""
    elements = [Element(TASK, None, 1, 0, task_text)]
    for s in episode.steps:
        if s.reasoning.strip():
            elements.append(Element(REASONING, s.step_index, s.message_index, 0, s.reasoning))
        if s.visible_text.strip():
            elements.append(Element(VISIBLE, s.step_index, s.message_index, 1, s.visible_text))
        for j, (call, out) in enumerate(zip(s.tool_calls, s.outputs)):
            elements.append(Element(ACTION, s.step_index, s.message_index, 2 + 2 * j, render_call(call), tool=call.name))
            if out is not None:
                text = output_text(out)
                if text:
                    capped, truncated = cap_output(text)
                    elements.append(Element(OUTPUT, s.step_index, s.message_index, 3 + 2 * j, capped, truncated=truncated))
    # Harness messages: user messages after the first one. They belong to the step of
    # the assistant turn that precedes them, and sit after that step's block.
    step_of_msg = {s.message_index: s.step_index for s in episode.steps}
    for i, m in enumerate(messages):
        if i <= 1 or m.get("role") != "user":
            continue
        prev = max(idx for idx in step_of_msg if idx < i)
        content = m["content"] if isinstance(m["content"], str) else json.dumps(m["content"], sort_keys=True)
        elements.append(Element(HARNESS, step_of_msg[prev], i, 0, content))
    for m in messages[2:]:
        if m.get("role") not in ("assistant", "tool", "user"):
            raise ValueError(f"{episode.episode_id}: unexpected mid-episode role {m.get('role')!r}")
    return sorted(elements, key=lambda e: (e.message_index, e.order))


def select(elements, view, k, step_k_message_index):
    """Pick the elements a view shows. Pure function of kinds, step numbers and positions."""
    rules = VIEW_RULES[view]
    chosen = [elements[0]]  # task
    for e in elements[1:]:
        upto = rules.get(e.kind)
        if upto is None:
            continue
        if e.kind == HARNESS:
            if e.message_index < step_k_message_index:
                chosen.append(e)
        elif upto == "k" and e.step <= k:
            chosen.append(e)
        elif upto == "k-1" and e.step <= k - 1:
            chosen.append(e)
    return chosen


def render(chosen, k):
    """Render selected elements in message order."""
    lines = []
    open_step = None

    def close():
        if open_step is not None:
            lines.append("</step>")

    for e in chosen:
        if e.kind == TASK:
            lines += ["<task>", e.text, "</task>"]
            continue
        if e.kind == HARNESS:
            close()
            open_step = None
            lines += ["<harness>", e.text, "</harness>"]
            continue
        if e.step != open_step:
            close()
            lines.append(f'<step n="{e.step}">')
            open_step = e.step
        if e.kind == ACTION:
            lines += [f'<action tool="{e.tool}">', e.text, "</action>"]
        else:
            lines += [f"<{e.kind}>", e.text, f"</{e.kind}>"]
    close()
    lines.append(FINAL_LINE.format(k=k))
    return "\n".join(lines) + "\n"


def check_view(view, chosen, k, step_k_message_index, t_keys, agent_keys_upto_k):
    kinds = {e.kind for e in chosen}
    for e in chosen:
        assert not (e.step == k and e.kind in (ACTION, OUTPUT)), f"{view}: step k action/output present"
        assert not (e.kind == HARNESS and e.message_index > step_k_message_index), f"{view}: harness after step k"
        assert e.step is None or e.step <= k, f"{view}: element from after step k"
    if view in ("T0", "T"):
        assert not kinds & AGENT_TEXT, f"{view}: contains reasoning or visible text"
    if view in ("T0", "R_only"):
        assert not kinds & ENV_HISTORY, f"{view}: contains actions, outputs or harness messages"
    if view == "T_plus_R":
        keys = [e.key for e in chosen]
        assert len(keys) == len(set(keys)), "T+R has a duplicated element"
        assert set(keys) == t_keys | agent_keys_upto_k, "T+R is not exactly T plus agent text of steps 1..k"
        assert not t_keys & agent_keys_upto_k


def load_raw(config, episode_ids):
    files = sorted(glob.glob(os.path.join(DATA_DIR, "data", config, "*.parquet")))
    t = pq.read_table(files, columns=["episode_id", "messages_json", "main_task_description"],
                      filters=[("arm", "=", "honest"), ("episode_id", "in", list(episode_ids))])
    return {r["episode_id"]: r for r in t.to_pylist()}


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def norm_ws(text):
    return " ".join(text.split())


def choose_examples(records):
    """6 examples: 2 per config, one early-half and one late-half, with feature preferences."""
    prefs = {
        "basharena-kimi-k3": [("early", lambda r: r["multi_call"] and r["visible"]), ("late", lambda r: r["harness_in_t"] > 0)],
        "basharena-qwen3.5-397b": [("early", lambda r: r["visible"]), ("late", lambda r: r["multi_call"])],
        "basharena-nemotron-3-ultra": [("early", lambda r: True), ("late", lambda r: True)],
    }
    half = {"early": ("early", "early_mid"), "late": ("late_mid", "late")}
    chosen = []
    for config, slots in prefs.items():
        pool = [r for r in records if r["config"] == config]
        for side, pred in slots:
            cands = [r for r in pool if r["quarter"] in half[side]]
            pick = next((r for r in cands if pred(r)), cands[0])
            chosen.append(pick)
    assert any(r["multi_call"] for r in chosen) and any(r["visible"] for r in chosen)
    if any(r["harness_in_t"] for r in records):
        assert any(r["harness_in_t"] for r in chosen), "no example with a harness message in history"
    return chosen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["pilot", "main"], default="pilot")
    args = parser.parse_args()
    split = args.split
    if not os.path.isdir(os.path.join(DATA_DIR, "data")):
        sys.exit(f"run from the repo root; expected the dataset under ./{DATA_DIR}/data")

    suffix = "" if split == "pilot" else f"_{split}"
    manifest_path = os.path.join("sample", f"views_manifest{suffix}.csv")
    summary_path = os.path.join("sample", f"views_summary{suffix}.json")
    out_root = os.path.join(VIEWS_ROOT, split)

    with open(STEPS_CSV, newline="") as f:
        picks = [r for r in csv.DictReader(f) if r["split"] == split]
    with open(VIEW_LENGTHS_CSV, newline="") as f:
        proxy = {(r["config"], r["task_id"], int(r["step_index"])): r for r in csv.DictReader(f)}

    shutil.rmtree(out_root, ignore_errors=True)
    manifest, records = [], []
    harness_total = Counter()

    for config in sorted({p["config"] for p in picks}):
        cp = [p for p in picks if p["config"] == config]
        ep_ids = sorted({p["episode_id"] for p in cp})
        episodes = load_episodes(DATA_DIR, config, ep_ids)
        raw = load_raw(config, ep_ids)
        elements_by_ep = {}
        for eid in ep_ids:
            messages = json.loads(raw[eid]["messages_json"])
            task_text = raw[eid]["main_task_description"]
            assert messages[1]["role"] == "user" and messages[1]["content"] == task_text, f"{eid}: task text mismatch"
            elements_by_ep[eid] = (episode_elements(episodes[eid], messages, task_text), messages[0]["content"])
            harness_total[config] += sum(e.kind == HARNESS for e in elements_by_ep[eid][0])

        for p in cp:
            k = int(p["step_index"])
            ep = episodes[p["episode_id"]]
            step_k = ep.steps[k - 1]
            assert step_k.step_index == k and step_k.message_index == int(p["message_index"])
            elements, system_prompt = elements_by_ep[p["episode_id"]]

            chosen = {v: select(elements, v, k, step_k.message_index) for v in VIEWS}
            t_keys = {e.key for e in chosen["T"]}
            agent_keys = {e.key for e in elements if e.kind in AGENT_TEXT and e.step <= k}
            assert {e.key for e in chosen["R_only"]} - {elements[0].key} == agent_keys, "R-only is not all agent text of steps 1..k"
            texts = {}
            for v in VIEWS:
                check_view(v, chosen[v], k, step_k.message_index, t_keys, agent_keys)
                texts[v] = render(chosen[v], k)
                assert system_prompt not in texts[v], f"{v}: system prompt present"
            finals = {t.rstrip("\n").rsplit("\n", 1)[-1] for t in texts.values()}
            assert finals == {FINAL_LINE.format(k=k)}, "final line differs across views"

            step_dir = os.path.join(out_root, config, p["task_id"], f"step_{k}")
            for v in VIEWS:
                write(os.path.join(step_dir, VIEW_FILES[v]), texts[v])
            hidden = {
                "config": config, "task_id": p["task_id"], "episode_id": p["episode_id"], "split": split,
                "quarter": p["quarter"], "step_index": k, "message_index": step_k.message_index,
                "target": {"tool": step_k.target.name, "arguments": step_k.target.arguments, "id": step_k.target.id},
                "all_calls": [{"tool": c.name, "arguments": c.arguments, "id": c.id} for c in step_k.tool_calls],
            }
            write(os.path.join(step_dir, "hidden_action.json"), json.dumps(hidden, indent=2, sort_keys=True, ensure_ascii=False) + "\n")

            for v in VIEWS:
                c = chosen[v]
                manifest.append((
                    config, p["task_id"], split, p["quarter"], k, v, n_tokens(texts[v]),
                    len({e.step for e in c if e.step is not None and e.kind != HARNESS}),
                    sum(e.truncated for e in c if e.kind == OUTPUT),
                    sum(e.kind == HARNESS for e in c),
                    hashlib.sha256(texts[v].encode("utf-8")).hexdigest(),
                ))

            prior_actions = {norm_ws(e.text) for e in chosen["T"] if e.kind == ACTION}
            target_rendered = norm_ws(render_call(step_k.target))
            pr = proxy[(config, p["task_id"], k)]
            records.append({
                "config": config, "task_id": p["task_id"], "quarter": p["quarter"], "step_index": k,
                "dir": step_dir, "multi_call": step_k.n_tool_calls > 1,
                "visible": bool(step_k.visible_text.strip()),
                "harness_in_t": sum(e.kind == HARNESS for e in chosen["T"]),
                "repeated": target_rendered in prior_actions,
                "tok_T": manifest[-2][6], "tok_TR": manifest[-1][6],
                "proxy_T": int(pr["t_tokens"]), "proxy_TR": int(pr["tr_tokens"]),
            })

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["config", "task_id", "split", "quarter", "step_index", "view", "n_tokens", "n_steps_rendered",
                "n_outputs_truncated", "n_harness_messages_included", "sha256"])
    w.writerows(manifest)
    write(manifest_path, buf.getvalue())

    rep = defaultdict(lambda: [0, 0])
    for r in records:
        for key in (f"{r['config']}|{r['quarter']}", f"{r['config']}|all", f"all|{r['quarter']}", "all|all"):
            rep[key][0] += r["repeated"]
            rep[key][1] += 1

    def ratios(a, b):
        vals = sorted(r[a] / r[b] for r in records)
        return {"min": round(vals[0], 4), "median": round(vals[len(vals) // 2], 4), "max": round(vals[-1], 4)}

    summary = {
        "split": split,
        "tokenizer": f"tiktoken {TOKENIZER}",
        "output_cap_tokens": OUTPUT_CAP_TOKENS,
        "n_test_steps": len(records),
        "n_views": len(manifest),
        "n_outputs_truncated_by_view": {v: sum(m[8] for m in manifest if m[5] == v) for v in VIEWS},
        "n_harness_messages_in_episodes_by_config": dict(sorted(harness_total.items())),
        "n_views_with_harness_message_by_view": {v: sum(m[9] > 0 for m in manifest if m[5] == v) for v in VIEWS},
        "repeated_target_action": {k: {"repeated": v[0], "n": v[1]} for k, v in sorted(rep.items())},
        "token_ratio_view_over_proxy": {"T": ratios("tok_T", "proxy_T"), "T_plus_R": ratios("tok_TR", "proxy_TR")},
    }
    write(summary_path, json.dumps(summary, indent=2, sort_keys=True) + "\n")

    if split == "pilot":
        shutil.rmtree(EXAMPLES_DIR, ignore_errors=True)
        for r in choose_examples(records):
            dst = os.path.join(EXAMPLES_DIR, f"{r['config']}__{r['task_id']}__step_{r['step_index']}")
            shutil.copytree(r["dir"], dst)
            print(f"example: {dst} (quarter={r['quarter']}, multi_call={r['multi_call']}, "
                  f"visible_text={r['visible']}, harness_in_history={r['harness_in_t']})")

    print(f"wrote {len(manifest)} views for {len(records)} {split} test steps under {out_root}/")
    print(f"wrote {manifest_path}\nwrote {summary_path}")


if __name__ == "__main__":
    main()
