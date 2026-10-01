# 06 — Writer & Critic (`src/core/writer.py`, `src/core/critic.py`)

## `writer.draft_episode(context: EpisodeContext, critic_notes: list[str] | None = None) -> str`
One LLM call (mid-tier model). Prompt enforces: 400-700 words, ends on a hook, respects active
directives, must advance or touch at least one open thread when the current beat calls for it.
If `critic_notes` passed (revision pass), they're included as explicit fix-this instructions.

## `critic.check(draft: str, context: EpisodeContext) -> CriticResult`
Two layers, cheap-first:

1. **Deterministic/cheap checks (no LLM call)**
   - Word count in range.
   - Named entities in the draft (simple string match) against `Character` names with
     `status="dead"` — catches the "dead character shows up" class of bug for free.
   - Embedding/cosine-similarity of this draft's summary against recent episode summaries —
     flags likely repeated beats before spending a model call on it.

2. **LLM-as-judge pass (cheap model, structured output)**
   - Given the draft + context facts/threads/directives, returns a checklist verdict:
     contradicts a known fact? (y/n + which), ignores an active directive? (y/n), thread that
     should have progressed didn't? (y/n), ends on a genuine hook? (y/n).

`CriticResult = { passed: bool, notes: list[str] }`. `passed=False` triggers a revision: back to
`writer.draft_episode` with `critic_notes`, capped at **2 revision attempts**. After 2 failed
attempts, stop looping — surface the draft plus critic notes to the human directly rather than
silently looping or silently shipping (bounded, per requirements).

## Implementation notes
- Critic pass/fail is computed from issue severities (blocker/minor) plus deterministic blockers, never from a model-reported flag.
- Dead characters named in a draft are a *hint* to the judge, not a failure: the premise itself features dead characters.
- Repetition: 5-gram overlap vs the last 3 episodes (hard above 10%) plus the judge reading summaries and recent endings.
  Deviates from the spec's cosine similarity because there is no embedding infrastructure.
- The critic re-retrieves facts by entities named in the draft so it can catch contradictions the writer was never shown.
- The revision loop lives in `orchestrator._produce_draft` (it owns bounds); `writer.draft_episode` takes a `Revision`.

## Status: [x] implemented (`src/core/writer.py`, `src/core/critic.py`, prompts), `tests/test_writer.py`, `tests/test_critic.py`
