# Does the reasoning match the work? Reasoning vs whole-trace monitorability

A Mercial Fellowship research project on whether an AI agent's reasoning predicts what it does next.

A reader model sees an agent's task and either its actions, its reasoning, or both. It then predicts the agent's next action. We compare how accurate it is under each view.

## Team

Four people work on this project: Gabriel, Supratik, Dan and Sudhiksha.

## What is in this branch

| Folder     | Contents                                                                       |
| ---------- | ------------------------------------------------------------------------------ |
| `docs/`    | The action taxonomy: the six action types and the rules for labelling a step   |
| `prompts/` | The reader prompt and the judge prompt                                         |
| `src/`     | The rule-based action classifier, the predictions adapter and the scoring code |
| `tests/`   | Tests, which run on synthetic data only                                        |
| `scripts/` | A check that the examples in the docs are not copied from real transcripts     |

## Confidentiality

The data used in this project is restricted. It is for evaluation only and must not be shared with anyone who has not accepted its terms.

- Do not commit transcripts, views, targets or any text taken from the data.
- Examples in the docs, prompts and tests are synthetic.
- Keep any file containing real data in a gitignored folder.

## Status

This is a work in progress. The prompts and classifier have not yet been fully validated on real data.
