# Step rules (Step 2: break each episode into steps, pick test steps)

These rules are implemented in `src/parse_steps.py` (parser) and
`scripts/sample_steps.py` (sampler). Reproduce with `uv run scripts/sample_steps.py`
from the repo root. Inputs: the 210 episodes in `sample/episodes.csv`
(70 tasks x 3 BashArena configs, honest arm).

## Data format (confirmed on the data)

- `messages_json` is a list of messages with roles `system`, `user`, `assistant`, `tool`.
- Assistant `content` is either:
  - a list of blocks: one `{"type": "reasoning", "reasoning": ..., "internal": "reasoning", "redacted": false}`
    followed by one `{"type": "text", "text": ...}`; or
  - a plain string, with no reasoning block (Kimi-K3 only: 322 of its 3,629 turns in the sample).
    The string is treated as visible text and the step's reasoning is empty.
  `internal` is always the constant `"reasoning"`, not a copy of the reasoning. No reasoning
  block is redacted or encrypted. Reasoning lives only in the `reasoning` field.
- Assistant `tool_calls` is a list of `{"id", "function", "arguments", "view", "type"}`.
  `function` is the tool name (`bash`, `text_editor`, `python`, `submit`, ...).
- Tool messages carry `tool_call_id`, `function`, `content` (string) and sometimes
  `error` (`{"type", "message"}`). Every tool call in the sample has exactly one tool
  message with its id, and there are no tool messages without a call.
- A user message can appear after the first one: a harness nudge after an assistant turn
  with no tool call (3 cases in the sample). It belongs to no step.

## Step boundaries

- A step is one assistant turn. Boundaries come from the dataset's `steps` column: one
  entry per assistant message, keyed by `message_index`. The parser asserts that the
  `message_index` values equal the indices of the assistant messages, in order (true for
  all 210 episodes).
- `step_index` is the 1-indexed position of the step among ALL steps of the episode,
  eligible or not. `message_index` is the index into `messages_json`.
- Each step holds:
  - `reasoning`: the `reasoning` text of the turn's reasoning blocks, joined with a blank
    line (in practice there is at most one block). Empty if the turn has no reasoning block.
  - `visible_text`: the turn's `text` blocks joined with a blank line, or the plain-string
    content. This is separate from reasoning.
  - `tool_calls`: ordered list of (name, arguments, id) as stored.
  - `outputs`: for each tool call, the tool message whose `tool_call_id` equals the call id
    (content plus error type and message, if any).
  - `is_final`: see below.

## Final step

- `is_final` is true for the LAST step that contains a call to the `submit` tool, and false
  for every other step.
- Every episode in the sample ends with a submit step. One episode
  (`basharena-nemotron-3-ultra`, `3070723__honest`) has two submit steps: the submit at
  step 107 returned a tool error and the agent submitted again at step 108. Only step 108 is
  `is_final`; step 107 is eligible under these rules (it was not picked).

## Eligibility

A step is eligible if all three hold:

1. `reasoning.strip()` is non-empty,
2. it has at least one tool call,
3. `is_final` is false.

## Target action

If a step has several tool calls, the target is the FIRST one (`tool_calls[0]`).
`n_tool_calls` is recorded per step so multi-call steps can be analysed separately.

## Quarters and picks

For each episode:

1. List the eligible steps in order. `n_eligible` is their count, `n_total` the count of
   all steps.
2. Give each eligible step its 0-indexed eligible rank `i`. Its quarter is
   `q = floor(4 * i / n_eligible)`, so `q` is in {0, 1, 2, 3}, labelled
   `early`, `early_mid`, `late_mid`, `late`. Every quarter is non-empty when `n_eligible >= 4`.
3. In each quarter, pick one eligible step uniformly at random:

   ```python
   GLOBAL_SEED = 20261113
   seed_str = f"{GLOBAL_SEED}|{config}|{task_id}|{q}"
   seed_int = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16)
   pick = random.Random(seed_int).choice(steps_in_quarter)
   ```

   SHA-256 is used instead of Python's `hash()`, which is salted per process.
   `config` is the full config name (e.g. `basharena-kimi-k3`) and `task_id` is the string id.
4. If `n_eligible < 4`, nothing is picked or substituted: the episode is left out of
   `sample/steps.csv` and listed in `sample/steps_summary.json`. The replacement rule is
   undecided. (No episode in the current sample is affected.)

## Derived fields

- `relative_position = step_index / n_total`, using the 1-indexed `step_index` over all steps.
- `eligible_rank` is the 0-indexed rank among eligible steps.

## Outputs

Committed (IDs, indices, counts, booleans, lengths and tool names only):

- `sample/steps.csv`: one row per picked test step.
- `sample/episode_steps.csv`: one row per step of all 210 episodes.
- `sample/steps_summary.json`: seeds, distributions and edge cases.

Not committed: `debug/handcheck/` (written by `scripts/dump_handcheck.py`), readable dumps
that contain transcript text. Share them only with people who have accepted the dataset terms.
