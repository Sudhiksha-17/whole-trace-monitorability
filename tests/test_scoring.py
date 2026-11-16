"""Tests on FAKE data with known truth. No real transcripts involved."""
import numpy as np
import pandas as pd
import pytest

from src import scoring as sc

TRUE_P = {"T0": 0.15, "T": 0.45, "R_only": 0.35, "T_R": 0.65}


def fake(n_ep=150, steps=4, p=TRUE_P, seed=0, ep_sd=0.0, sev_effect=False, models=("A", "B", "C")):
    rng = np.random.default_rng(seed)
    rows = []
    for e in range(n_ep):
        m = models[e % len(models)]
        shift = rng.normal(0, ep_sd)  # episode-level clustering
        for s in range(steps):
            sev = int(rng.choice([1, 2, 3], p=[0.6, 0.3, 0.1]))
            u = rng.random()  # shared latent difficulty: conditions are positively correlated within a step
            for c, pc in p.items():
                pc2 = np.clip(pc + shift, 0.01, 0.99)
                if sev_effect:
                    pc2 = np.clip(pc2 - 0.12 * (sev - 1), 0.01, 0.99)
                ok = u < pc2
                rows.append(dict(episode_id=f"{m}-{e}", model=m, step_id=f"{e}-{s}", condition=c, correct=ok,
                                 type_correct=ok or rng.random() < 0.3, severity=sev, position=s + 1,
                                 gold_type=rng.choice(["read_inspect", "local_write", "execute_install"], p=[.6, .25, .15]),
                                 verbatim_leak=False, chained=False))
    return pd.DataFrame(rows)


def test_recovers_known_accuracies_and_gains():
    df = fake(n_ep=3000, seed=1)
    r = sc.summarize(df, n_boot=300)
    for _, row in r["acc"].iterrows():
        assert abs(row.accuracy - TRUE_P[row.condition]) < 0.02
    g = r["gains"].set_index("metric").estimate
    assert abs(g["cot_gain"] - (0.65 - 0.45)) < 0.02
    assert abs(g["recon"] - (0.35 - 0.15)) < 0.02


def test_ci_coverage_of_true_gain():
    """~95% of simulated studies of the real size (150 episodes) should have CI covering the true gain."""
    hit_cot = hit_rec = 0
    N = 200
    for s in range(N):
        df = fake(n_ep=150, seed=100 + s, ep_sd=0.08)
        g = sc.summarize(df, n_boot=400, seed=s)["gains"].set_index("metric")
        hit_cot += g.loc["cot_gain", "ci_lo"] <= 0.20 <= g.loc["cot_gain", "ci_hi"]
        hit_rec += g.loc["recon", "ci_lo"] <= 0.20 <= g.loc["recon", "ci_hi"]
    assert 0.88 <= hit_cot / N <= 0.995, hit_cot / N
    assert 0.88 <= hit_rec / N <= 0.995, hit_rec / N


def test_paired_ci_narrower_than_unpaired():
    """Shared per-step difficulty => the paired CI for a gain must be narrower than treating the two arms as independent."""
    df = fake(n_ep=150, seed=3, ep_sd=0.05)
    r = sc.summarize(df, n_boot=2000)
    g = r["gains"].set_index("metric").loc["cot_gain"]
    a = r["acc"].set_index("condition")
    unpaired = np.sqrt((a.loc["T_R", "ci_hi"] - a.loc["T_R", "ci_lo"]) ** 2 + (a.loc["T", "ci_hi"] - a.loc["T", "ci_lo"]) ** 2)
    assert (g.ci_hi - g.ci_lo) < unpaired


def test_zero_gain_ci_contains_zero():
    p = {"T0": .2, "T": .5, "R_only": .3, "T_R": .5}
    r = sc.summarize(fake(n_ep=150, p=p, seed=7), n_boot=1000)["gains"].set_index("metric").loc["cot_gain"]
    assert r.ci_lo <= 0 <= r.ci_hi


def test_severity_weighting_detects_hidden_dangerous_actions():
    df = fake(n_ep=1500, seed=5, sev_effect=True)
    s = sc.summarize(df, n_boot=300)["severity"].set_index("condition")
    for c in sc.CONDS:
        assert s.loc[c, "weighted_miss"] > s.loc[c, "miss"]
        assert s.loc[c, "miss_sev3"] > s.loc[c, "miss_sev1"]


def test_no_severity_effect_gives_equal_rates():
    df = fake(n_ep=2000, seed=6, sev_effect=False)
    s = sc.summarize(df, n_boot=200)["severity"].set_index("condition")
    for c in sc.CONDS:
        assert abs(s.loc[c, "weighted_miss"] - s.loc[c, "miss"]) < 0.02


def test_parse_failure_counts_as_wrong_and_input_checks():
    df = fake(n_ep=20, seed=2)
    with pytest.raises(ValueError):
        sc.check_input(df[df.condition != "T"])  # missing a condition
    with pytest.raises(ValueError):
        sc.check_input(pd.concat([df, df.iloc[:1]]))  # duplicate row
    with pytest.raises(ValueError):
        sc.check_input(df.drop(columns=["type_correct"]))


def test_subset_drops_whole_steps_and_keeps_pairing():
    df = fake(n_ep=30, seed=4)
    leak_steps = df.step_id.unique()[:10]
    df.loc[df.step_id.isin(leak_steps), "verbatim_leak"] = True
    d = sc.subset(df, drop_verbatim=True)
    assert d.step_id.nunique() == df.step_id.nunique() - 10
    sc.check_input(d)


def test_trivial_baseline():
    rng = np.random.default_rng(0)

    def steps(n_ep, p_read, ids):
        rows = []
        for e in range(n_ep):
            for pos in (1, 2, 3, 4):
                rows.append(dict(step_id=f"{ids}{e}-{pos}", episode_id=f"{ids}{e}", position=pos,
                                 gold_type="read_inspect" if rng.random() < p_read else "execute_install"))
        return pd.DataFrame(rows)

    pilot, main = steps(20, 0.8, "p"), steps(150, 0.8, "m")
    b = sc.trivial_baseline(pilot, main)
    assert all(v == "read_inspect" for v in b["guess_by_position"].values())
    assert abs(b["type_accuracy"] - 0.8) < 0.06
    lo, hi = sc.trivial_baseline_ci(b, n_boot=500)
    assert lo < b["type_accuracy"] < hi


def test_per_model_and_report_run():
    df = fake(n_ep=60, seed=9)
    by = sc.summarize_by_model(df, n_boot=100)
    assert set(by) == {"A", "B", "C"}
    assert "CoT gain" in sc.report(df, n_boot=100)


def test_generic_drop_and_split_summary():
    df = fake(n_ep=60, seed=11)
    rng = np.random.default_rng(0)
    flag = {s: bool(rng.random() < 0.2) for s in df.step_id.unique()}
    df["leak_target"] = df.step_id.map(flag)
    d = sc.subset(df, drop=("leak_target",))
    assert not d.leak_target.any() and d.step_id.nunique() == sum(not v for v in flag.values())
    sc.check_input(d)
    with pytest.raises(KeyError):
        sc.subset(df, drop=("nope",))
    parts = sc.split_summary(df, "leak_target", n_boot=100)
    assert set(parts) == {False, True}


def test_context_and_cost_from_lengths():
    toks = {"T0": [500, 600], "T": [20_000, 40_000], "R_only": [10_000, 30_000], "T_R": [60_000, 168_000]}
    over = sc.fraction_over_context(toks, 128_000)
    assert over["T_R"] == 0.5 and over["T0"] == 0.0
    assert sc.fraction_over_context(toks, 200_000)["T_R"] == 0.0
    cost = sc.estimate_cost_by_condition(toks, 1.0, 5.0, mean_out_tokens=60, prompt_overhead=700)
    assert cost["total"] == pytest.approx(sum(v for k, v in cost.items() if k != "total"))
    assert cost["T_R"] > cost["T"] > cost["T0"]


def test_cost():
    assert sc.estimate_cost(1000, 50, 0.15, 0.6, 2400) == pytest.approx(2400 * (1000 * .15 + 50 * .6) / 1e6)
