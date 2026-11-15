# /// script
# requires-python = ">=3.11"
# dependencies = ["pyarrow==26.0.1"]
# ///
"""Step 1 of the methodology: pick the data.

Draws a deterministic sample of 50 main and 20 pilot BashArena task IDs that
appear in the honest arm of all three model configs.

Only IDs and numbers are written to sample/. No transcript text is ever read
into the outputs: the script loads only the columns listed in COLUMNS.

Run from the repo root:
    uv run scripts/select_tasks.py
"""

import csv
import glob
import io
import json
import os
import platform
import random
import re
import sys

import pyarrow
import pyarrow.compute as pc
import pyarrow.parquet as pq

SEED = 20261113
N_MAIN = 50
N_PILOT = 20

DATASET_REPO = "AlignmentResearch/telling-fibs-eval-transcripts"
DATA_DIR = "data"
CONFIGS = [
    "basharena-qwen3.5-397b",
    "basharena-kimi-k3",
    "basharena-nemotron-3-ultra",
]
HONEST_ARM = "honest"
# Only metadata columns. Never add text columns (messages_json, descriptions, ...).
COLUMNS = ["episode_id", "arm", "task_id", "main_task_score", "main_task_success", "attempt", "run"]

OUT_DIR = "sample"
TASKS_CSV = os.path.join(OUT_DIR, "tasks.csv")
EPISODES_CSV = os.path.join(OUT_DIR, "episodes.csv")
SUMMARY_JSON = os.path.join(OUT_DIR, "selection_summary.json")

# Guards that the outputs hold identifiers and numbers only.
TASK_ID_RE = re.compile(r"^[0-9]+$")
EPISODE_ID_RE = re.compile(r"^[0-9]+__[a-z0-9_]+$")
RUN_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*$")


def load_honest(config):
    files = sorted(glob.glob(os.path.join(DATA_DIR, "data", config, "*.parquet")))
    assert files, f"no parquet shards found for {config}"
    table = pq.ParquetDataset(files).read(columns=COLUMNS)
    table = table.filter(pc.equal(table["arm"], HONEST_ARM))
    return table.to_pylist()


def dataset_revision():
    """Hub commit of the local download, read from huggingface_hub's cache metadata."""
    meta = os.path.join(DATA_DIR, ".cache", "huggingface", "download", "README.md.metadata")
    try:
        with open(meta) as f:
            return f.readline().strip() or None
    except OSError:
        return None


def draw(shared_task_ids):
    ids = sorted(shared_task_ids)  # string sort; task_id is a string column
    random.Random(SEED).shuffle(ids)
    return ids[:N_MAIN], ids[N_MAIN:N_MAIN + N_PILOT]


def to_csv(header, rows):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue()


def build_outputs():
    honest = {cfg: load_honest(cfg) for cfg in CONFIGS}

    by_task = {}
    for cfg, rows in honest.items():
        index = {}
        for row in rows:
            index.setdefault(row["task_id"], []).append(row)
        by_task[cfg] = index

    shared = set.intersection(*(set(index) for index in by_task.values()))
    main, pilot = draw(shared)

    # Checks: sizes, disjointness, and exactly one honest episode per config.
    assert len(main) == N_MAIN, f"main has {len(main)} tasks"
    assert len(pilot) == N_PILOT, f"pilot has {len(pilot)} tasks"
    assert not set(main) & set(pilot), "main and pilot overlap"
    split_of = {**{t: "main" for t in main}, **{t: "pilot" for t in pilot}}
    for cfg in CONFIGS:
        for task_id in split_of:
            n = len(by_task[cfg].get(task_id, []))
            assert n == 1, f"{cfg} has {n} honest episodes for task {task_id}"

    task_rows = [(t, split_of[t]) for t in main + pilot]

    episode_rows = []
    for cfg in CONFIGS:
        for task_id, split in task_rows:
            row = by_task[cfg][task_id][0]
            episode_rows.append((
                cfg,
                task_id,
                split,
                row["episode_id"],
                repr(float(row["main_task_score"])),
                row["main_task_success"],
                int(row["attempt"]),
                row["run"],
            ))

    assert len(episode_rows) == len(CONFIGS) * (N_MAIN + N_PILOT)
    for cfg, task_id, _, episode_id, _, success, attempt, run in episode_rows:
        assert TASK_ID_RE.match(task_id), f"unexpected task_id format: {task_id!r}"
        assert EPISODE_ID_RE.match(episode_id), f"unexpected episode_id format: {episode_id!r}"
        assert success in ("C", "I"), f"unexpected main_task_success: {success!r}"
        assert attempt >= 1, f"unexpected attempt: {attempt!r}"
        assert RUN_RE.match(run), f"unexpected run format: {run!r}"
    assert len({(r[0], r[3]) for r in episode_rows}) == len(episode_rows), "episode ids repeat"

    summary = {
        "seed": SEED,
        "n_main": N_MAIN,
        "n_pilot": N_PILOT,
        "dataset": DATASET_REPO,
        "dataset_revision": dataset_revision(),
        "configs": CONFIGS,
        "arm": HONEST_ARM,
        "honest_episodes_per_config": {cfg: len(honest[cfg]) for cfg in CONFIGS},
        "shared_task_id_count": len(shared),
        "draw_procedure": "sorted(shared task_ids as strings), random.Random(seed).shuffle, first n_main = main, next n_pilot = pilot",
        "main_task_success_encoding": "C = correct (main_task_score == 1.0), I = incorrect",
        "python_version": platform.python_version(),
        "library_versions": {"pyarrow": pyarrow.__version__},
    }

    return {
        TASKS_CSV: to_csv(["task_id", "split"], task_rows),
        EPISODES_CSV: to_csv(
            ["config", "task_id", "split", "episode_id", "main_task_score", "main_task_success", "attempt", "run"],
            episode_rows,
        ),
        SUMMARY_JSON: json.dumps(summary, indent=2, sort_keys=True) + "\n",
    }


def main():
    if not os.path.isdir(os.path.join(DATA_DIR, "data")):
        sys.exit(f"run from the repo root; expected the dataset under ./{DATA_DIR}/data")

    outputs = build_outputs()
    assert outputs == build_outputs(), "selection is not deterministic within a run"

    os.makedirs(OUT_DIR, exist_ok=True)
    for path, content in outputs.items():
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
