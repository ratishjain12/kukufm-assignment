"""Writes a run's reviewable output to a folder: plan versions, episodes, the story, the human decisions, cost, and trace."""

import json
import re
import shutil
from pathlib import Path

from src.core import cost
from src.core.formatting import format_plan
from src.logging_config import trace_path
from src.models import Episode
from src.storage import repo

DECISION_PATTERNS = (
    r"plan gate v\d+ decision=",
    r"arc plan (revised|v\d+ (generated|approved))",
    r"episode \d+ gate decision=",
    r"directive added",
    r"episode \d+ (edited|escalated)",
    r"edit preview",
    r"state rebuilt",
)


def _pad(number: int) -> str:
    return f"{number:03d}"


def _episode_markdown(episode: Episode) -> str:
    facts = [f"*{episode.status} · {episode.source.replace('_', ' ')} · {episode.word_count} words · {episode.revision_count} automatic rewrite(s)*"]
    if episode.human_feedback:
        facts.append(f"**Reviewer feedback given at this episode:** {episode.human_feedback}")
    if episode.summary:
        facts.append(f"**Summary:** {episode.summary}")
    return f"# Episode {episode.episode_no}\n\n" + "\n\n".join(facts) + f"\n\n{episode.text.strip()}\n"


def _trace_records(run_id: str) -> list[dict[str, str]]:
    path = trace_path(run_id)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


ENDING_CHARS = 150
EFFECT_WINDOW_BEFORE, EFFECT_WINDOW_AFTER = 2, 5


def _ending(episode: Episode) -> str:
    return "…" + " ".join(episode.text.split())[-ENDING_CHARS:]


def _directive_effects(run_id: str) -> list[str]:
    """For each directive, how the episodes around it end, so the effect (or lack of one) is visible in the output itself."""
    lines: list[str] = []
    for d in repo.get_directives(run_id):
        window = repo.get_episodes_range(run_id, max(1, d.created_at_episode - EFFECT_WINDOW_BEFORE), d.created_at_episode + EFFECT_WINDOW_AFTER)
        lines += [f"### {d.text}", "", "| episode | how it ends |", "|---|---|"]
        for e in window:
            marker = " ← directive given here" if e.episode_no == d.created_at_episode else ""
            lines.append(f"| {e.episode_no}{marker} | {_ending(e).replace('|', '/')} |")
        lines.append("")
    return lines or ["- no directives", ""]


def _status_changes(run_id: str) -> list[str]:
    lines = []
    for episode in repo.get_episodes_range(run_id, 1):
        delta = repo.get_state_delta(run_id, episode.episode_no)
        for change in delta.changes if delta else []:
            if change.entity_type == "character" and change.field == "status":
                character = repo.get_character(run_id, change.entity_id)
                lines.append(f"- Episode {episode.episode_no}: {character.name if character else change.entity_id} {change.old_value} → {change.new_value}")
    return lines or ["- none recorded"]


def _decisions_markdown(run_id: str) -> str:
    run = repo.require_run(run_id)
    lines = [f"# Human decisions — {run_id}", "", f"Premise: {run.premise}", ""]

    lines += ["## Directives (feedback that steers every later episode)", ""]
    lines += [
        f"- **{d.text}** (given at episode {d.created_at_episode}, {'through episode ' + str(d.expires_after_episode) if d.expires_after_episode else 'permanent'})"
        for d in repo.get_directives(run_id)
    ] or ["- none"]
    lines += ["", "## Effect of each directive on the episodes around it", ""] + _directive_effects(run_id)
    lines += ["## Character status changes (recorded from the story)", ""] + _status_changes(run_id) + [""]

    lines += ["", "## Episodes edited or steered by the human", ""]
    touched = [e for e in repo.get_episodes_range(run_id, 1, finalized_only=False) if e.source == "human_edited" or e.human_feedback]
    lines += [
        f"- Episode {e.episode_no}: {'edited by hand' if e.source == 'human_edited' else 'approved'}" + (f"; feedback: {e.human_feedback}" if e.human_feedback else "")
        for e in touched
    ] or ["- none"]

    lines += ["", "## Timeline of gate decisions (from the trace log)", ""]
    pattern = re.compile("|".join(DECISION_PATTERNS))
    events = [r for r in _trace_records(run_id) if pattern.search(r["message"])]
    lines += [f"- `{r['ts'][11:]}` {r['message']}" for r in events] or ["- no trace log found"]
    return "\n".join(lines) + "\n"


def _trace_summary(run_id: str) -> str:
    rows = [r for r in _trace_records(run_id) if r["level"] != "DEBUG"]
    return "\n".join(f"{r['ts']} {r['level']:<7} {r['logger']}: {r['message']}" for r in rows) + "\n" if rows else "no trace log found\n"


def export_run(run_id: str, out_dir: Path) -> list[Path]:
    """Rewrites the export files in out_dir (previous exports of the same names are replaced, nothing else is touched)."""
    plan_dir, episode_dir = out_dir / "plan", out_dir / "episodes"
    for folder, pattern in ((plan_dir, "arc_plan*.txt"), (episode_dir, "episode_*.md")):
        folder.mkdir(parents=True, exist_ok=True)
        for stale in folder.glob(pattern):
            stale.unlink()

    written: list[Path] = []

    def write(path: Path, content: str) -> None:
        path.write_text(content)
        written.append(path)

    plan = repo.require_arc_plan(run_id)
    snapshots = repo.get_plan_snapshots(run_id)
    final = snapshots.get(plan.version) or format_plan(plan, repo.get_characters(run_id), repo.get_threads(run_id), repo.get_facts(run_id))
    write(plan_dir / "arc_plan.txt", final)
    for version, text in snapshots.items():
        if version != plan.version:
            write(plan_dir / f"arc_plan_v{version}.txt", text)

    episodes = repo.get_episodes_range(run_id, 1)
    for episode in episodes:
        write(episode_dir / f"episode_{_pad(episode.episode_no)}.md", _episode_markdown(episode))
    premise = repo.require_run(run_id).premise
    story = f"# {premise}\n\n" + "\n\n---\n\n".join(f"## Episode {e.episode_no}\n\n{e.text.strip()}" for e in episodes) + "\n"
    write(out_dir / "story.md", story)

    write(out_dir / "human_decisions.md", _decisions_markdown(run_id))
    write(out_dir / "cost_report.txt", cost.format_report(run_id) + "\n")
    write(out_dir / "trace_summary.log", _trace_summary(run_id))
    if trace_path(run_id).exists():
        full = out_dir / "trace_full.jsonl"
        shutil.copyfile(trace_path(run_id), full)
        written.append(full)
    return written
