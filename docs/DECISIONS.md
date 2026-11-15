# Decisions and caveats

Running record of every decision and caveat for the "Does the reasoning match the work?"
study, Steps 1 to 2 implemented, Step 3 onward decided but not built. Last updated
2026-11-15. All numbers are from the current files in `sample/`.

This file contains IDs, counts, tool names and rule descriptions only. No transcript text
(reasoning, visible text, commands, tool outputs, task descriptions) belongs in git.

Detailed step rules: [`docs/step_rules.md`](step_rules.md).

## 1. Dataset

- `AlignmentResearch/telling-fibs-eval-transcripts` (Hugging Face), revision
  `97d20337785f68062befa9324d97d7c81063b0c2`, downloaded to `./data` (gitignored, never modified).
- Configs used: `basharena-qwen3.5-397b`, `basharena-kimi-k3`, `basharena-nemotron-3-ultra`.
  Honest arm only (`arm == "honest"`). The attack arms, `shade_public-*` and
  `fibs_validation` are not used in Steps 1 to 2.
- Honest episodes per config: 626 (Qwen), 626 (Kimi), 627 (Nemotron). `task_id` is unique
  within each config's honest arm. 623 task_ids are shared by all three.
- The methodology doc cites 637 BashBench2 tasks, so 14 are missing from the shared set.
  Partly explained by the dataset card:
  - 9 tasks were discarded for every model (5 because the test suite hangs, 4 because the
    environment cannot be set up), leaving 628 per model.
  - `provenance/SOURCES.json` records 1 Kimi honest episode dropped by the paper's cohort rule.
  - The remaining gaps (2 Qwen, 1 Kimi, 1 Nemotron honest episodes) have no recorded reason.
- `episode_id` (e.g. `5691193__honest`) is unique only within a config. `(config, episode_id)`
  identifies a row; `attempt_id` is unique across the whole dataset.

## 2. Task sample (Step 1)

- Shared task_ids sorted as strings, shuffled with `random.Random(SEED)`, `SEED = 20261113`.
  First 50 = main, next 20 = pilot. Disjoint by construction and by assertion.
- Every selected task has exactly one honest episode per config: 70 tasks x 3 = 210 episodes
  (150 main, 60 pilot).
- Python is pinned to 3.13.5 through `.python-version` (used by `uv run`); `pyarrow==26.0.1`
  is pinned in the script header. Runs are byte-identical.
- Outputs: `sample/tasks.csv` (task_id, split), `sample/episodes.csv` (config, task_id, split,
  episode_id, main_task_score, main_task_success, attempt, run), `sample/selection_summary.json`.
- `main_task_success` is `C` exactly when `main_task_score == 1.0`, else `I`.

## 3. Steps and test-step selection (Step 2)

- A step is one assistant turn. Boundaries come from the dataset's `steps` list, which has
  exactly one entry per assistant message in all 210 episodes (asserted).
- `step_index` is 1-indexed over all steps of the episode. `relative_position = step_index / n_total`.
- Eligible step: non-empty reasoning (after stripping whitespace), at least one tool call,
  target call is not `submit`, and not the final step (`is_final`, the last step calling `submit`).
- Kimi turns stored as plain strings with no reasoning block are ineligible: 322 of Kimi's
  3,629 steps in the sample (0 for Qwen and Nemotron).
- Target = the first tool call of the step.
- Quarters: eligible rank `i` (0-indexed) goes to quarter `floor(4 * i / n_eligible)`
  (`early`, `early_mid`, `late_mid`, `late`). One uniform pick per quarter with
  `random.Random(seed_int).choice(...)`, where
  `seed_int = int(sha256(f"{GLOBAL_SEED}|{config}|{task_id}|{q}").hexdigest(), 16)`,
  `GLOBAL_SEED = 20261113`, `q` in 0..3. Python's built-in `hash()` is not used because it is
  salted per process.
- n_eligible per episode (min / median / max): Qwen 23 / 65.5 / 224, Kimi 20 / 42.5 / 106,
  Nemotron 41 / 93 / 195. No episode has fewer than 4 eligible steps, so no replacement rule
  was needed (the rule remains undecided).
- Logged per pick (`sample/steps.csv`): step_index, message_index, eligible_rank, n_total,
  n_eligible, relative_position, n_tool_calls, target_tool_name, has_visible_text.
- Totals: 600 main and 240 pilot test steps (200 main and 80 pilot per config).
  Target tools: `bash` 726, `text_editor` 110, `python` 3, `str_replace` 1.
- Change log: the submit-target rule was added on 2026-11-15. It made one step ineligible
  (Nemotron `3070723__honest` step 107), changing that episode's n_eligible from 107 to 106 and
  two of its picks (late_mid 69 -> 68, late 102 -> 101). No other episode or pick changed.

## 4. Decisions for reader views (Step 3, not yet built)

- Visible text is treated as part of the agent's stated reasoning. It appears in R-only and
  T+R, never in T0 or T. Reasoning and visible text are tagged separately within views, so
  results can be split by source.
- Every view includes the identical line "This is step k of the episode", where k is the
  full-episode `step_index`. The total step count is never shown.
- No system prompt in any view. Views are built from `messages_json`, not
  `model_view_messages_json`. Tool outputs are capped at 2,000 tokens each.
- When step k's action is hidden, ALL of step k's tool calls and outputs are hidden, not just
  the target.
- T+R includes the reasoning at step k.

## 5. Scoring decisions

- First-call-only: a prediction is scored against the first tool call of step k only.
- Multi-call steps are reported separately. Picked steps with more than one tool call:
  Kimi 95 of 280, Qwen 24 of 280, Nemotron 7 of 280 (126 total). Over all steps of the 210
  episodes: Kimi 888, Qwen 594, Nemotron 250.

## 6. Leakage flags (planned, before any reader runs)

- `command_leak` and `target_leak` are computed on step k's reasoning and on step k's visible
  text, separately for each source.
- `target_in_earlier_reasoning` is an extra flag.
- Results are split into no leak / target only / command. No steps are dropped.
- Matching rules (normalisation, full path vs basename, target extraction from `bash` and
  `text_editor` calls) will be documented separately.

## 7. Context length

- Assumption: context length is not a problem, and no history truncation is applied.
- Estimate (`scripts/view_lengths.py`): tiktoken `cl100k_base` v0.14.0 as a proxy tokenizer.
  T = task description + all tool calls and capped outputs of steps 1..k-1.
  T+R = T + reasoning of steps 1..k-1 + reasoning of step k. Visible text, formatting and
  the "step k" line are not counted (visible text would add 0.1 to 0.9%).

T view tokens (median / p90 / max):

| Config | early | early_mid | late_mid | late |
|---|---|---|---|---|
| Qwen | 5,800 / 10,754 / 25,750 | 13,958 / 29,658 / 35,570 | 19,566 / 37,910 / 55,792 | 25,409 / 50,427 / 78,674 |
| Kimi | 6,902 / 16,077 / 19,524 | 19,633 / 28,970 / 64,514 | 26,425 / 45,423 / 78,438 | 32,320 / 58,447 / 111,448 |
| Nemotron | 6,983 / 16,826 / 24,819 | 16,276 / 31,335 / 54,824 | 25,688 / 46,391 / 80,152 | 33,727 / 57,832 / 85,369 |

T+R view tokens (median / p90 / max):

| Config | early | early_mid | late_mid | late |
|---|---|---|---|---|
| Qwen | 6,470 / 11,603 / 26,879 | 14,974 / 31,090 / 36,887 | 22,577 / 42,449 / 61,459 | 28,587 / 55,279 / 86,103 |
| Kimi | 12,110 / 30,804 / 45,815 | 35,060 / 54,433 / 98,004 | 45,350 / 80,494 / 120,112 | 56,630 / 98,635 / 168,278 |
| Nemotron | 7,630 / 17,745 / 27,713 | 18,196 / 34,338 / 56,346 | 28,051 / 50,433 / 85,100 | 37,656 / 66,090 / 91,318 |

- Views over 100k: 1 T view and 9 T+R views, all Kimi (T: 1 late; T+R: 2 late_mid, 7 late).
  Views over 200k: none. Over 1M: none.
- The longest views must be recounted with the reader's real tokenizer once the reader is chosen.
- Reader context must be at least 200k. A 128k reader would require truncating only Kimi's
  late views, which would bias comparisons.
- The 2,000-token output cap removes 23.7% (Kimi), 25.0% (Qwen) and 31.7% (Nemotron) of
  output tokens.

## 8. Caveats

- **Retries.** Honest episodes with `attempt > 1`: Qwen 79/626 (12.6%), Kimi 140/626 (22.4%),
  Nemotron 75/627 (12.0%). In the sample: Qwen 6/70, Kimi 15/70, Nemotron 6/70
  (main set: 5, 10, 5 of 50). The dataset card says failed episodes (wall clock, setup or
  transport errors) were re-run, with no per-episode reason; failed attempts are not in the
  dataset. Wall-clock failures may depend on behaviour, and Kimi is retried most, so kept
  episodes may be biased toward attempts that finished in time. Proposed sensitivity check:
  first-attempt-only episodes within each model, and, across models, the tasks that were
  first-attempt for all three models (382 of the 623 shared tasks; 33 of the 50 main tasks and
  13 of the 20 pilot tasks).
- **Binary success is very low.** Main set: Qwen 4/50 (8%), Kimi 7/50 (14%), Nemotron 2/50 (4%).
  Use `main_task_score` as a continuous covariate instead of a success/failure split
  (main-set mean score: Qwen 0.608, Kimi 0.784, Nemotron 0.608).
- **Visible text is very unevenly distributed.** Picked steps with visible text: Kimi 187 of 280,
  Qwen 52 of 280, Nemotron 9 of 280. Kimi's visible text often announces the next action, so
  reasoning-based results for Kimi may be driven by visible text. Report the split by source.
- **Multi-call steps are concentrated in Kimi** (95 picked, against 24 Qwen and 7 Nemotron).
  First-call-only scoring may understate Kimi.
- **View length.** Median view length rises 4.4 to 4.9x from early to late quarters. Kimi's
  median T+R is 1.69x its T, against 1.08x for Qwen and Nemotron. Reasoning is 43.3% of Kimi's
  T+R tokens, against 8.7% (Qwen) and 10.5% (Nemotron). View length should be a covariate.
- **Data quirks.** Nemotron `3070723__honest` has a submit that returned a tool error (step 107)
  followed by a second submit (step 108); step 107 is now ineligible under the submit-target
  rule. One Kimi tool output (`13343058__honest`, pilot, step 11) contains NUL bytes; it is real
  tool output, not a parser bug, and makes text tools treat the hand-check dump as binary.
- **Data terms.** The dataset is gated and evaluation-only: no training on it, no transcript
  text in git, and only people who have accepted the terms may see transcript text (including
  the `debug/handcheck/` dumps).

## 9. Open items

- Retry sensitivity plan (section 8): proposed, awaiting confirmation.
- Whether the reader runs on SHADE-Arena early steps for the self-consistency bound.
- Reader model choice, and recount of the longest views with its real tokenizer.
- Leak matching rules write-up (section 6).
- Replacement rule for episodes with fewer than 4 eligible steps (not needed for the current
  sample).
- Pre-registration freeze.

Nothing runs on the main set before the pilot and the pre-registration are done.

## 10. Reproduce

From the repo root, with the dataset downloaded and the terms accepted:

```sh
hf download AlignmentResearch/telling-fibs-eval-transcripts --repo-type dataset \
  --revision 97d20337785f68062befa9324d97d7c81063b0c2 --local-dir data
uv run scripts/select_tasks.py     # sample/tasks.csv, episodes.csv, selection_summary.json
uv run scripts/sample_steps.py     # sample/steps.csv, episode_steps.csv, steps_summary.json
uv run scripts/view_lengths.py     # sample/view_lengths.csv, view_lengths_summary.json
uv run scripts/dump_handcheck.py   # optional: debug/handcheck/ (gitignored, contains transcript text)
```

Each script is deterministic: rerunning produces byte-identical files.
