"""Glue between the data pipeline's committed CSVs (sample/) and src/scoring.py.

Contains no transcript text: only IDs, numbers, booleans and labels.

    steps = load_step_table("sample")                # one row per test step, with covariates
    df = to_scoring_frame(steps, predictions)        # one row per (step, condition)

`predictions` columns (written by the reader+judge run, one row per step and condition):
    step_id, condition (T0|T|R_only|T_R), type_match (bool), target_match (bool), parse_fail (bool, optional)
"""
from __future__ import annotations

import os

import pandas as pd

QUARTER_TO_POSITION = {"early": 1, "early_mid": 2, "late_mid": 3, "late": 4}
CONDITION_ALIASES = {"T+R": "T_R", "R-only": "R_only", "T_R": "T_R", "R_only": "R_only", "T0": "T0", "T": "T"}


def _read(path: str) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"task_id": str})


def load_step_table(sample_dir: str = "sample", split: str | None = None) -> pd.DataFrame:
    """One row per test step. Needs sample/steps.csv and sample/episodes.csv; uses sample/leak_flags.csv if present
    (pilot only for now: main-split leak flags are generated only after the pre-registration freeze)."""
    steps = _read(os.path.join(sample_dir, "steps.csv"))
    eps = _read(os.path.join(sample_dir, "episodes.csv"))
    keep = ["config", "episode_id", "main_task_score", "main_task_success", "attempt"]
    t = steps.merge(eps[keep], on=["config", "episode_id"], how="left", validate="many_to_one")
    if t.main_task_score.isna().any():
        raise ValueError("some test steps have no matching episode in episodes.csv")

    t["model"] = t["config"]
    t["episode_id"] = t["config"] + "|" + t["episode_id"]  # episode_id is only unique within a config
    t["step_id"] = t["config"] + "|" + t["task_id"] + "|" + t["step_index"].astype(str)
    t["position"] = t["quarter"].map(QUARTER_TO_POSITION)
    t["multi_call"] = t["n_tool_calls"] > 1
    t["attempt_gt1"] = t["attempt"] > 1

    lf_path = os.path.join(sample_dir, "leak_flags.csv")
    if os.path.exists(lf_path):
        lf = _read(lf_path)
        cols = ["config", "task_id", "step_index", "extraction_status", "command_leak", "target_leak", "basename_only_mention",
                "target_in_earlier_reasoning", "leak_category"]
        t = t.merge(lf[cols], on=["config", "task_id", "step_index"], how="left", validate="one_to_one")
        has = t["leak_category"].notna()
        t["has_leak_flags"] = has
        t["verbatim_leak"] = t["command_leak"].fillna(False).astype(bool)  # command leak = reasoning contains the command
        t["leak_target"] = t["target_leak"].fillna(False).astype(bool)
        t["ambiguous_extraction"] = (t["extraction_status"] == "ambiguous")
        t["basename_only_mention"] = t["basename_only_mention"].fillna(False).astype(bool)
        t["target_in_earlier_reasoning"] = t["target_in_earlier_reasoning"].fillna(False).astype(bool)
    if split is not None:
        t = t[t["split"] == split]
    if t.step_id.duplicated().any():
        raise ValueError("duplicate step ids")
    return t.reset_index(drop=True)


def to_scoring_frame(step_table: pd.DataFrame, predictions: pd.DataFrame) -> pd.DataFrame:
    """Join predictions to step covariates. A parse failure is WRONG (both type and target)."""
    p = predictions.copy()
    p["condition"] = p["condition"].map(CONDITION_ALIASES)
    if p["condition"].isna().any():
        raise ValueError("unknown condition names in predictions")
    pf = p["parse_fail"].fillna(False).astype(bool) if "parse_fail" in p else pd.Series(False, index=p.index)
    p["type_correct"] = p["type_match"].astype(bool) & ~pf
    p["correct"] = p["type_correct"] & p["target_match"].astype(bool)
    missing = set(p.step_id) - set(step_table.step_id)
    if missing:
        raise ValueError(f"{len(missing)} predictions refer to steps that are not in the step table")
    cov = [c for c in step_table.columns if c not in ("config",)]
    out = p[["step_id", "condition", "correct", "type_correct"] + (["parse_fail"] if "parse_fail" in p else [])].merge(
        step_table[cov], on="step_id", how="left", validate="many_to_one")
    return out
