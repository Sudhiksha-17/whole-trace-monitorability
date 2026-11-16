# Data format note

Structure and field names of `AlignmentResearch/telling-fibs-eval-transcripts`
(revision `97d20337785f68062befa9324d97d7c81063b0c2`) as used by this project, confirmed on
the data. No transcript text here. Decisions and caveats: [`DECISIONS.md`](DECISIONS.md).

The dataset is gated and evaluation-only. Keep transcript text out of git and share it only
with people who have accepted the terms.

## Layout

Downloaded to `./data` (gitignored). Each config is a folder of Parquet shards, one row per
episode. Load all shards of a folder together (`pyarrow.parquet.read_table(list_of_files)`).

| Config | Shards | Rows | Honest | Attack |
|---|---|---|---|---|
| `data/data/basharena-qwen3.5-397b/` | 8 | 2,882 | 626 | 2,256 |
| `data/data/basharena-kimi-k3/` | 8 | 2,911 | 626 | 2,285 |
| `data/data/basharena-nemotron-3-ultra/` | 7 | 2,560 | 627 | 1,933 |
| `data/data/shade_public-qwen3.5-397b/` | 1 | 200 | 100 | 100 |
| `data/data/shade_public-kimi-k3/` | 1 | 193 | 100 | 93 |
| `data/data/shade_public-nemotron-3-ultra/` | 1 | 196 | 100 | 96 |
| `data/data/fibs_validation/` | 1 | | | (probe data, not used) |

Also present: `data/README.md` (the dataset card) and `data/provenance/` (build scripts,
`SOURCES.json`, `SHA256SUMS`; the checksums are of the authors' source files, not of the
Hub files).

## Key columns

| Column | Type | Notes |
|---|---|---|
| `arm` | string | `honest` or `attack`. We use `honest`. |
| `episode_id` | string | BashArena: `<task_id>__<arm or side task>`, e.g. `<task_id>__honest`; unique only within a config, so key rows by `(config, episode_id)`. SHADE: 24-character hex. |
| `attempt_id` | string | BashArena: `<run>:<episode_id>:<attempt>`; unique across the dataset. |
| `task_id` | string | BashArena only. Numeric-looking but stored as a string; sort as strings. |
| `scenario`, `replicate` | string, int32 | SHADE only. 4 scenarios x 25 replicates (0 to 24) per model, honest arm. |
| `run`, `attempt` | string, int32 | BashArena only. `attempt > 1` means the episode was re-run after a failed attempt; only the finished attempt is in the data. |
| `main_task_description` | string | The task text. Identical to the first user message in every honest episode checked. |
| `main_task_score` | double | 0 to 1 (BashArena: fraction of tests passed). |
| `main_task_success` | string / bool | BashArena: `C` (score == 1.0) or `I`. SHADE: boolean. |
| `request_system_message` | string | System prompt the model received. Honest arm: equal to `messages_json[0]`. |
| `messages_json` | string (JSON) | Full transcript, verbatim. Parse with `json.loads`. |
| `model_view_messages_json` | string (JSON) | History as of the last model call, with long tool outputs elided. Not used. |
| `steps` | list of struct | The dataset's step list (see below). |
| `n_messages`, `n_assistant_messages`, `n_model_calls`, `n_steps` | int32 | Sizes. `n_model_calls` can differ from `n_assistant_messages`. |
| `sample_metadata_json`, `generate_config_json`, `tools_json`, `per_call_generation_json` | string (JSON) | Run metadata. `sample_metadata_json.ba_episode` (BashArena) holds run/attempt bookkeeping, no retry reason. |

SHADE-only extra columns: `main_task_status`, `main_task_headline_eligible`,
`main_task_headline_success`, `main_task_headline_reason`, `main_task_threshold`,
`side_task_status`, `side_task_headline_*`, `outcome_scorer_caveat`, `outcome_fill_judge`.
BashArena-only: `run`, `attempt`, `task_id`, `max_model_len`, `episode_wall_clock_s`.

## `messages_json`

A list of messages, each with `role`, `id` and `content`.

- `messages_json[0]`: `system` (the system prompt). `messages_json[1]`: `user` (the task,
  equal to `main_task_description`).
- `assistant` messages (keys `content`, `id`, `metadata`, `model`, `role`, `source`, `tool_calls`):
  - `content` is usually a list of two blocks: `{"type": "reasoning", "reasoning": str,
    "internal": "reasoning", "redacted": false}` then `{"type": "text", "text": str}`.
    `internal` is a constant label, not a second copy of the reasoning. No block is redacted.
    The text block is the visible (non-reasoning) text, often empty.
  - Kimi-K3 only: some turns have `content` as a plain string and no reasoning block
    (BashArena sample: 322 of 3,629 turns; SHADE honest: 49 turns). The string is visible text.
  - `tool_calls`: list of `{"id", "function", "arguments", ...}`. `function` is the tool name,
    `arguments` a JSON object. BashArena calls also carry `view` and `type`.
  - A turn can have zero, one or several tool calls.
- `tool` messages (keys `content`, `function`, `id`, `role`, `tool_call_id`, sometimes
  `error`): one per tool call, linked by `tool_call_id` == the call's `id`. `content` is a
  string, or an empty list for an empty output (SHADE). `error`, when present, is
  `{"type", "message"}`; the content may then be empty.
- Harness `user` messages: any `user` message after `messages_json[1]`. In all six honest
  configs every one has the same fixed text (a nudge to continue or to call `submit`), and
  each comes right after an assistant turn with no tool call. Counts, honest arm:
  BashArena Qwen 3, Kimi 13, Nemotron 1 (3 of them in our 210-episode sample);
  SHADE Qwen 75, Kimi 26, Nemotron 25 (at most one per episode).

### Tools

- BashArena: `bash` (`command`), `python` (`code`), `text_editor` (`command`, `path`, plus
  `file_text`, `old_str`/`new_str` or `view_range`), occasionally `str_replace`,
  `read_tool_output`, and `submit`.
- SHADE: 69 to 86 domain tools per model (email, calendar, contacts, files, banking, rules),
  with typed arguments (strings, ints, lists, floats, booleans), and `submit`.
- `submit` ends the episode. Every honest episode checked ends with a `submit` turn. One
  BashArena episode (Nemotron `3070723__honest`) has a failed submit (tool error) followed by
  a second one.

### Tool output content

- BashArena outputs sometimes contain raw binary bytes printed by the agent's commands: NUL
  bytes (18 outputs in 13 of the 210 sampled episodes), other control characters, and U+FFFD.
  ANSI escape sequences (terminal colours) appear in 111 outputs, mostly Nemotron.
  `scripts/build_views.py` strips ANSI sequences and replaces NUL with the literal text `\x00`.
- SHADE honest outputs contain no NUL bytes or ANSI sequences.

## `steps`

One entry per step, keyed by `message_index` (the index of the assistant message in
`messages_json`).

- BashArena fields: `message_index`, `message_id`, `step_rank`, `model_event_ordinal`,
  `label_class`, `paper_role`, `family_labels`.
- SHADE fields: `message_index`, `message_id`, `step_rank`, `model_event_ordinal`,
  `label_status`, `label_class`, `side_task_action`, `stage`, `disclosure`, `effect`,
  `judge_reason`, `unresolved_reason`, `label_source`, `paper_role`. `judge_reason` and
  `unresolved_reason` are free text written by the paper's judge.
- Mapping: in all 210 sampled BashArena episodes, `steps` maps one-to-one onto assistant
  messages, in order. In SHADE, 5 assistant turns in 4 Kimi-K3 honest episodes have no step
  record (documented in the dataset card); the turns stay in the transcript. Our parser
  (`src/parse_steps.py`) rejects such episodes and needs a rule before it is run on SHADE.
- The step labels concern sabotage (side-task actions). The dataset card reports 0 positive
  steps in every honest arm. We use `steps` only for step boundaries.
