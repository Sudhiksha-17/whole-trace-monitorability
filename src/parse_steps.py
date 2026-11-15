"""Split a Telling-FIBS BashArena episode into steps (Step 2 of the methodology).

A step is one assistant turn. Step boundaries come from the dataset's own `steps`
column (one entry per assistant message, keyed by `message_index`). For each
step we collect the reasoning, the visible text, the tool calls and the tool
outputs matched to each call by `tool_call_id`.

Message format (confirmed on real data, see docs/step_rules.md):
- assistant `content` is either a list of blocks or a plain string.
  Blocks are {"type": "reasoning", "reasoning": str, "internal": "reasoning",
  "redacted": bool} and {"type": "text", "text": str}. A plain-string content
  has no reasoning block; the string is visible text.
- assistant `tool_calls` is a list of {"id", "function", "arguments", ...}.
- tool messages carry `tool_call_id`, `function`, `content` and optionally `error`.

This module returns transcript text. Never write its text fields to a file
tracked by git.
"""

import glob
import json
import os
from dataclasses import dataclass, field

import pyarrow.parquet as pq

SUBMIT_TOOL = "submit"


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict
    id: str


@dataclass(frozen=True)
class ToolOutput:
    tool_call_id: str
    content: str
    error_type: str | None = None
    error_message: str | None = None


@dataclass
class Step:
    step_index: int  # 1-indexed position among all steps of the episode
    message_index: int  # index into messages_json
    reasoning: str
    visible_text: str
    tool_calls: list[ToolCall]
    outputs: list[ToolOutput | None]  # outputs[j] belongs to tool_calls[j]
    is_final: bool = False
    has_reasoning_block: bool = True

    @property
    def n_tool_calls(self) -> int:
        return len(self.tool_calls)

    @property
    def has_output(self) -> bool:
        """True if every tool call of this step has a matched output."""
        return all(o is not None for o in self.outputs)

    @property
    def eligible(self) -> bool:
        return bool(self.reasoning.strip()) and self.n_tool_calls > 0 and not self.is_final

    @property
    def target(self) -> ToolCall | None:
        """The action under test: the FIRST tool call of the step."""
        return self.tool_calls[0] if self.tool_calls else None


@dataclass
class Episode:
    config: str
    episode_id: str
    task_id: str
    steps: list[Step] = field(default_factory=list)

    @property
    def has_final_submit(self) -> bool:
        return any(s.is_final for s in self.steps)

    @property
    def n_submit_steps(self) -> int:
        return sum(any(c.name == SUBMIT_TOOL for c in s.tool_calls) for s in self.steps)


class ParseError(ValueError):
    pass


def _text_of(content) -> str:
    """Text of a tool message content (string or list of text blocks)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if block.get("type") != "text":
                raise ParseError(f"unexpected tool output block type {block.get('type')!r}")
            parts.append(block.get("text") or "")
        return "\n".join(parts)
    raise ParseError(f"unexpected tool output content type {type(content).__name__}")


def _split_assistant_content(content) -> tuple[str, str, bool]:
    """Return (reasoning, visible_text, has_reasoning_block) for an assistant message."""
    if isinstance(content, str):
        return "", content, False
    if not isinstance(content, list):
        raise ParseError(f"unexpected assistant content type {type(content).__name__}")
    reasoning, visible = [], []
    for block in content:
        kind = block.get("type")
        if kind == "reasoning":
            if block.get("redacted"):
                raise ParseError("redacted reasoning block")
            reasoning.append(block.get("reasoning") or "")
        elif kind == "text":
            visible.append(block.get("text") or "")
        else:
            raise ParseError(f"unexpected assistant block type {kind!r}")
    return "\n\n".join(reasoning), "\n\n".join(visible), bool(reasoning)


def parse_episode(messages: list[dict], steps_column: list[dict]) -> list[Step]:
    """Parse one episode into its ordered list of steps."""
    assistant_indices = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    step_indices = [int(s["message_index"]) for s in steps_column]
    if step_indices != assistant_indices:
        raise ParseError("dataset steps do not map one-to-one onto assistant messages")

    outputs_by_id: dict[str, ToolOutput] = {}
    for m in messages:
        if m.get("role") != "tool":
            continue
        call_id = m.get("tool_call_id")
        if call_id in outputs_by_id:
            raise ParseError(f"two tool outputs for call {call_id}")
        err = m.get("error") or {}
        outputs_by_id[call_id] = ToolOutput(
            tool_call_id=call_id,
            content=_text_of(m.get("content")),
            error_type=err.get("type"),
            error_message=err.get("message"),
        )

    steps: list[Step] = []
    seen_call_ids: set[str] = set()
    for rank, msg_index in enumerate(step_indices, start=1):
        m = messages[msg_index]
        reasoning, visible, has_block = _split_assistant_content(m.get("content"))
        calls = []
        for tc in m.get("tool_calls") or []:
            if tc["id"] in seen_call_ids:
                raise ParseError(f"duplicate tool call id {tc['id']}")
            seen_call_ids.add(tc["id"])
            calls.append(ToolCall(name=tc["function"], arguments=tc.get("arguments") or {}, id=tc["id"]))
        steps.append(Step(
            step_index=rank,
            message_index=msg_index,
            reasoning=reasoning,
            visible_text=visible,
            tool_calls=calls,
            outputs=[outputs_by_id.get(c.id) for c in calls],
            has_reasoning_block=has_block,
        ))

    orphans = set(outputs_by_id) - seen_call_ids
    if orphans:
        raise ParseError(f"{len(orphans)} tool outputs match no tool call")

    # The final submit is the last step that calls the submit tool.
    submit_steps = [s for s in steps if any(c.name == SUBMIT_TOOL for c in s.tool_calls)]
    if submit_steps:
        submit_steps[-1].is_final = True
    return steps


def load_episodes(data_dir: str, config: str, episode_ids: list[str]) -> dict[str, Episode]:
    """Load and parse the honest-arm episodes with the given ids from one config."""
    files = sorted(glob.glob(os.path.join(data_dir, "data", config, "*.parquet")))
    if not files:
        raise FileNotFoundError(f"no parquet shards for {config} under {data_dir}")
    table = pq.read_table(
        files,
        columns=["episode_id", "task_id", "messages_json", "steps"],
        filters=[("arm", "=", "honest"), ("episode_id", "in", list(episode_ids))],
    )
    episodes = {}
    for row in table.to_pylist():
        if row["episode_id"] in episodes:
            raise ParseError(f"{config}: episode {row['episode_id']} appears twice")
        episodes[row["episode_id"]] = Episode(
            config=config,
            episode_id=row["episode_id"],
            task_id=row["task_id"],
            steps=parse_episode(json.loads(row["messages_json"]), row["steps"]),
        )
    missing = set(episode_ids) - set(episodes)
    if missing:
        raise ParseError(f"{config}: {len(missing)} requested episodes not found")
    return episodes
