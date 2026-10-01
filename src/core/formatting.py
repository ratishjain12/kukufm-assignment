from src.models import ArcPlan, Beat, Character, EpisodeContext, Fact, Thread

NONE = "(none)"
ENDING_CHARS = 350


def _beat_line(beat: Beat) -> str:
    marker = " [TURNING POINT]" if beat.turning_point else ""
    return f"Episodes {beat.episode_start}-{beat.episode_end}: {beat.title}{marker} — {beat.summary}"


def _beat_position(context: EpisodeContext) -> str:
    beat = context.beat
    total = beat.episode_end - beat.episode_start + 1
    index = context.episode_no - beat.episode_start + 1
    return (
        f"This is episode {index} of {total} in this beat: cover only this episode's share of it and leave the beat's "
        "later developments for later episodes."
    )


def format_characters(context: EpisodeContext) -> str:
    names = {c.character_id: c.name for c in context.roster}
    lines = []
    for c in context.relevant_characters:
        relationships = "; ".join(f"{names.get(other, other)}: {desc}" for other, desc in c.relationships.items())
        lines.append(
            f"- {c.name} [{c.role}, {c.status}] traits: {', '.join(c.traits) or '-'} | "
            f"planned arc: {c.planned_arc or '-'} | now: {c.arc_stage or '-'} | "
            f"relationships: {relationships or '-'} | last seen ep {c.last_seen_episode}"
        )
    return "\n".join(lines) or NONE


def format_roster(roster: list[Character]) -> str:
    lines = []
    for c in roster:
        aka = f" (aka {', '.join(c.aliases)})" if c.aliases else ""
        lines.append(f"- {c.name}{aka}: {c.status}")
    return "\n".join(lines) or NONE


def format_threads(context: EpisodeContext) -> str:
    neglected = {t.thread_id for t in context.neglected_threads}
    lines = []
    for t in context.open_threads:
        due = f", due ep {t.resolution_target_episode}" if t.resolution_target_episode else ""
        flag = " NEGLECTED — address soon" if t.thread_id in neglected else ""
        lines.append(
            f"- {t.description} [{t.status}; planted ep {t.planted_episode}, last touched ep {t.last_touched_episode}{due}]{flag}"
        )
    return "\n".join(lines) or NONE


def format_threads_to_introduce(context: EpisodeContext) -> str:
    return "\n".join(f"- {t.description} (scheduled to appear around ep {t.planted_episode})" for t in context.threads_to_introduce) or NONE


def format_history(context: EpisodeContext) -> str:
    lines = [f"Episodes {b.episode_start}-{b.episode_end}: {b.summary}" for b in context.block_summaries]
    lines += [f"Episode {s.episode_no}: {s.summary}" for s in context.episode_summaries]
    return "\n".join(lines) or "(nothing earlier than the episodes below)"


def format_recent_episodes(context: EpisodeContext) -> str:
    return "\n\n".join(f"--- Episode {e.episode_no} ---\n{e.text}" for e in context.recent_episodes) or NONE


def format_recent_endings(context: EpisodeContext) -> str:
    return "\n".join(f"Episode {e.episode_no}: ...{e.text[-ENDING_CHARS:]}" for e in context.recent_episodes) or NONE


def format_context(context: EpisodeContext) -> dict[str, str]:
    """Template variables shared by the writer, critic and extractor prompts."""
    return {
        "premise": context.premise,
        "overview": context.overview or NONE,
        "episode_no": str(context.episode_no),
        "beat": _beat_line(context.beat) + "\n" + _beat_position(context),
        "upcoming": "\n".join(_beat_line(b) for b in context.upcoming_beats) or NONE,
        "characters": format_characters(context),
        "roster": format_roster(context.roster),
        "threads": format_threads(context),
        "introduce": format_threads_to_introduce(context),
        "directives": "\n".join(f"- {d.text}" for d in context.directives) or NONE,
        "facts": "\n".join(f"- {f.statement}" for f in context.facts) or NONE,
        "author_notes": "\n".join(f"- {f.statement}" for f in context.author_notes) or NONE,
        "history": format_history(context),
        "recent": format_recent_episodes(context),
        "recent_endings": format_recent_endings(context),
    }


def format_plan(plan: ArcPlan, characters: list[Character], threads: list[Thread], facts: list[Fact]) -> str:
    """Human- and LLM-readable arc plan. Used for the plan review gate and for revise prompts."""
    names = {c.character_id: c.name for c in characters}
    thread_names = {t.thread_id: t.description for t in threads}
    out = [f"PREMISE: {plan.premise}", f"PLAN VERSION {plan.version} ({plan.status}), {plan.total_episodes} episodes", "", "OVERVIEW", plan.overview, "", "CHARACTERS"]
    for c in characters:
        rel = "; ".join(f"{names.get(other, other)}: {desc}" for other, desc in c.relationships.items())
        out.append(f"- {c.name} [{c.role}] traits: {', '.join(c.traits) or '-'}")
        out.append(f"    arc: {c.planned_arc or '-'}")
        if rel:
            out.append(f"    relationships: {rel}")
    out += ["", "THREADS"]
    out += [f"- {t.description} (introduce ~ep {t.planted_episode}, resolve ~ep {t.resolution_target_episode or '?'})" for t in threads]
    out += ["", "WORLD FACTS"] + [f"- {f.statement}" for f in facts]
    out += ["", "BEATS"]
    for b in plan.beats:
        marker = " [TURNING POINT]" if b.turning_point else ""
        who = ", ".join(names.get(i, i) for i in b.character_ids)
        what = ", ".join(thread_names.get(i, i) for i in b.thread_ids)
        out.append(f"{b.episode_start:>3}-{b.episode_end:<3} {b.title}{marker}")
        out.append(f"        {b.summary}")
        if who or what:
            out.append(f"        characters: {who or '-'} | threads: {what or '-'}")
    return "\n".join(out)


def format_roster_with_ids(roster: list[Character]) -> str:
    """Roster the extractor resolves mentions against; ids are what it must echo back."""
    lines = []
    for c in roster:
        aka = f" (aka {', '.join(c.aliases)})" if c.aliases else ""
        lines.append(f"- {c.character_id} | {c.name}{aka} | {c.status} | arc: {c.arc_stage or '-'}")
    return "\n".join(lines) or NONE


def format_threads_with_ids(threads: list[Thread]) -> str:
    lines = [f"- {t.thread_id} | {t.description} | {t.status}" for t in threads if t.status not in ("resolved", "abandoned")]
    return "\n".join(lines) or NONE
