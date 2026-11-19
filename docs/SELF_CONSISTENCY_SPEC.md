# SHADE self-consistency specification

This specification defines a leave-one-run-out baseline using the [SHADE tool mapping](SHADE_TOOL_MAPPING.md).

## What this measures

For repeated honest runs of the same model and task, predict a held-out run's next action from other runs with the same earlier action sequence.

This is an **action-prefix self-consistency baseline**, rather than the ceiling described in the assignment. A finite-sample majority vote estimates one predictor, not the best possible predictor. It also omits prior tool outputs and harness text that T can see. Equal action prefixes can therefore leave T with different information. Coarse labels and unstable target IDs introduce further differences between this score and intrinsic predictability.

A ceiling for T would have to concern the best prediction conditional on all of T's information, for the same labels and population. The proposed estimator does not establish that quantity, and a SHADE score cannot cap BashArena performance.

The reasoning comparison is paired **T+R versus T on the same SHADE steps**, with the reader and scoring rules fixed. T+R beating this baseline alone is insufficient evidence of reasoning-specific information, and a predictive gain over T would not prove that the reasoning caused the action. Report the baseline as a comparison, not a denominator for "percentage of the ceiling".

## Episodes and missing records

Use the honest arm of `shade_public-qwen3.5-397b`, `shade_public-kimi-k3` and `shade_public-nemotron-3-ultra` at revision `97d20337785f68062befa9324d97d7c81063b0c2`. The inventory found 100 episodes per model, comprising 25 runs of each of four scenarios. Stored initial task content matched within each model/scenario. Peers must come from the same model, scenario and task.

The holdout and resampling unit is an episode, keyed by `(config, episode_id)`. Check generating seeds and environment resets before treating replicates as independent. The four scenarios are fixed tasks; they do not support a general population claim about agent tasks.

The proposed primary analysis keeps the parser's strict rule: exclude episodes whose `steps` records do not map one-to-one onto assistant messages. This removes four Kimi episodes with five missing records, leaving 296 episodes. Kimi then has 23 runs in each of `api_key_calendar_agendas_2` and `bank_transfer_2`, and 25 in each of the other two scenarios.

A separate sensitivity analysis could reconstruct the five boundaries from assistant-message order and retain all 300 episodes, after manual inspection. That would restore boundaries, not missing original sabotage labels. Agree the primary and sensitivity policies before inspecting estimator results.

[DECISIONS.md](DECISIONS.md) records monitoring and secondary-task wording in the honest SHADE system prompt. Include the caveat when comparing with BashArena's different prompt.

## Positions and eligibility

Use recorded assistant-step positions **k = 1 through 5**, not the first five eligible actions. Excluding a helper or an ineligible target does not renumber positions or trigger a replacement.

A target needs non-empty reasoning and at least one tool call, with the first call neither `submit` nor `end_task`. Predict the **first call only**. Earlier history still includes turns without reasoning and every call in multi-call turns.

The proposed policy excludes `read_tool_output` and `grep_tool_output` as prediction targets while keeping them in history. If an alternative includes helpers, report them separately as harness reads. Under strict episode exclusion, the inventory gives:

| Model | Retained episodes | First-five slots | Helper first-call targets | Candidate non-helper targets |
|---|---:|---:|---:|---:|
| Qwen | 100 | 500 | 105 | 395 |
| Kimi | 96 | 480 | 106 | 374 |
| Nemotron | 100 | 500 | 41 | 459 |
| Total | 296 | 1,480 | 252 | 1,228 |

All 1,480 slots passed the reasoning and non-terminal-call checks, and 660 contain multiple calls. The candidate counts are before matching peer histories.

An unresolved mapping or invalid target-call schema requires adjudication before scoring. Retain the original call in histories, and report exclusions by model, scenario and position. The two observed name/key anomalies were outside the first five non-terminal tool-bearing first-call targets, but remain in corpus accounting.

## Labels and history keys

Use the SHADE types and target selectors in `SHADE_TOOL_MAPPING.md`, once agreed. They are separate from the Bash command classifier.

A native-call key consists of the function name and **all original arguments**, serialised with sorted object keys. Preserve types, list order, strings, omitted fields and explicit null. Ignore storage-only message and tool-call IDs, but keep references that occur in arguments. Do not add defaults, drop bodies/descriptions or merge IDs in the primary key.

At k, the history key is the ordered tuple of complete call lists from assistant steps 1 through k−1. Preserve turn boundaries, empty lists, call order, helpers and control calls. Step k contributes only the held-out answer, never information to the prefix. Prior outputs and harness text are omitted from this predictor.

Group by `(config, scenario, initial-task identity, k, canonical prior-call history)`. At k = 1 the prefix is empty, giving a task-only repeated-run baseline. Keep task text and argument values out of committed outputs.

The answer is the joint `(type, structured target)` label for the first call. Its target includes the native tool name, so distinct no-argument tools remain distinguishable. Exact matching still does not grade omitted arguments such as amounts or message bodies, or establish that an ID denotes the same object across runs.

Strict prefixes may yield poor coverage. A pre-agreed sensitivity could instead use type/target labels for every earlier call, preserving turn boundaries. Report it as a reduced-information analysis, not a replacement selected after seeing accuracy. Any cross-run ID/alias alignment must use information available before k rather than future outputs or the hidden answer.

## Leave-one-run-out prediction

For each eligible target `(r, k)`:

1. Find eligible peers at k from the same model/scenario/task with an identical earlier-call history.
2. Exclude every record from run r from the donor set.
3. With no donors, abstain. Do not shorten the prefix, borrow from another group or use the run's own answer.
4. Predict the most frequent **joint** `(type, target)` label among donors. Separate type and target votes could produce a pair no donor took.
5. Resolve ties by lexical order of the canonical serialised joint labels, fixed before evaluation.
6. Record joint correctness, the corresponding type-only correctness, donor count and whether a tie occurred.

One donor is sufficient for the proposed baseline; report donor-count strata and tie rates. A larger minimum can be a pre-agreed sensitivity. Use actual call labels, not the dataset's sabotage labels, and keep type-only accuracy secondary to the joint score.

## Scores and uncertainty

For each model, scenario and k, report:

- N: eligible targets with resolved mappings.
- C: targets with at least one qualifying donor.
- M: correct joint predictions among covered targets.
- Coverage C/N, conditional accuracy M/C and correct-and-covered rate M/N.
- Type-only accuracy on the same covered targets, exclusions, tie counts and donor-size distribution.

M/N counts abstentions against coverage; it is not a ceiling. If N is zero, the corresponding rate is unavailable. If C is zero, conditional accuracy is unavailable. Reporting only M/C would hide singleton groups and other uncovered targets.

Show every scenario and position, per-model pooled counts, and an equal-scenario macro-average with the number of eligible/covered scenarios. Treat positions and peer comparisons within a run as dependent.

For uncertainty, the proposal is to resample whole episodes within model×scenario strata and recompute votes and coverage. Carry repeated selections as weights, excluding **all copies of the held-out original run** from its donor set. Keep its early positions together. Report 95% intervals and how often a ratio is undefined across resamples. Test duplicate self-exclusion and changing coverage explicitly; small groups and four scenarios still limit inference.

For a reader comparison, carry all four condition outcomes together through resampling. Select covered steps independently of reader success, and compare T+R with T on identical targets. If bootstrap coverage changes, apply the same resampled set to both conditions. Matching replicate numbers across different models is not a reason to pair their runs.

## Reader comparison and review decisions

A SHADE reader subset needs the same episode exclusions, step positions, first-call target, helper policy and mapping. Specify the sampling rule before inspecting reader/judge outcomes rather than relying on an approximate target of 100 early steps. Show baseline, T and T+R scores together with coverage. A low baseline may reflect omitted outputs, strict keys, finite samples or unstable IDs rather than randomness alone; a high shallow-prefix score may reflect stereotyped tool choice.

Before running the estimator:

- Agree the `write` class or `write_or_send` merge, target namespaces, optional-field handling and alias policy.
- Settle missing-record and helper policies, retaining original step positions and all prior calls.
- Agree history keys, joint voting, ties, abstention accounting and uncertainty. Test empty prefixes, singletons, multi-call histories, optional/null values, ID/reference mismatches, missing steps and bootstrap self-exclusion on synthetic cases, then hand-check real histories without executing their tools.
- Inspect a count/coverage report before interpreting accuracy. If a justified rule revision is needed, record it as exploratory rather than changing rules solely to improve coverage.

Record the reader subset, settings and resource limits with this specification, the tool mapping, severity rubric and labelling guide in the pre-registration.
