import logging
import re
from typing import cast

from src.core.extractor import ExtractedCharacter, ExtractedThread, Extraction
from src.models import Character, CharacterStatus, EntityType, Fact, FieldChange, StateDelta, Thread, ThreadStatus
from src.storage import repo

logger = logging.getLogger(__name__)

THREAD_LEAD_EPISODES = 10  # a planned thread may not move more than this many episodes before it is scheduled
MAX_THREAD_UPDATES = 4
MAX_NEW_FACTS = 5
EVIDENCE_MATCH_CHARS = 40
MIN_EVIDENCE_CHARS = 15
THREAD_RANK = {"planned": 0, "planted": 1, "escalated": 2, "resolved": 3, "abandoned": 3}


def _change(entity_type: EntityType, entity_id: str, field: str, old: str | None, new: str) -> FieldChange:
    return FieldChange(entity_type=entity_type, entity_id=entity_id, field=field, old_value=old, new_value=new)


def _old(change: FieldChange) -> str:
    if change.old_value is None:
        raise ValueError(f"cannot revert {change.entity_type}.{change.field}: no old value recorded")
    return change.old_value


def _normalize(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def _quoted(evidence: str, text: str | None) -> bool:
    """True if the claimed evidence really is a quote from the episode. Without text (callers that have none) it passes."""
    if text is None:
        return True
    cited = _normalize(evidence)[:EVIDENCE_MATCH_CHARS]
    return len(cited) >= MIN_EVIDENCE_CHARS and cited in _normalize(text)


def _is_proper_name(name: str) -> bool:
    """A new character needs a name, not a description: 'Dr. Elenor Voss' yes; 'the voice', 'clerk', "Kavi's mother" no."""
    words = name.split()
    return 1 <= len(words) <= 4 and all(w[0].isupper() or not w[0].isalpha() for w in words)


def _resolve_character(run_id: str, character_id: str | None, name: str) -> Character | None:
    found = repo.get_character(run_id, character_id) if character_id else None
    return found or repo.find_character_by_name_or_alias(run_id, name)


def _apply_character(
    run_id: str, episode_no: int, u: ExtractedCharacter, changes: list[FieldChange], new_ids: list[str], text: str | None
) -> Character | None:
    target = _resolve_character(run_id, u.character_id, u.name)
    seen = episode_no if u.appears_on_page else None

    if target is None:
        if not _is_proper_name(u.name):
            logger.warning("not creating a character from the description %r", u.name)
            return None
        created = Character(
            name=u.name, aliases=u.new_aliases, role=u.role or "supporting", status=u.status, traits=u.new_traits,
            arc_stage=u.arc_stage or "", last_seen_episode=seen or 0,
        )
        repo.upsert_character(run_id, created)
        new_ids.append(created.character_id)
        changes.append(_change("character", created.character_id, "created", None, created.name))
        return created

    if u.status != target.status:
        if _quoted(u.evidence, text):
            changes.append(_change("character", target.character_id, "status", target.status, u.status))
            target.status = u.status
        else:
            logger.warning("ignoring unquoted status change %s -> %s for %s", target.status, u.status, target.name)
    if u.arc_stage and u.arc_stage != target.arc_stage:
        changes.append(_change("character", target.character_id, "arc_stage", target.arc_stage, u.arc_stage))
        target.arc_stage = u.arc_stage
    for alias in u.new_aliases:
        if alias.lower() not in {target.name.lower(), *(a.lower() for a in target.aliases)}:
            changes.append(_change("character", target.character_id, "alias_added", None, alias))
            target.aliases.append(alias)
    for trait in u.new_traits:
        if trait.lower() not in {t.lower() for t in target.traits}:
            changes.append(_change("character", target.character_id, "trait_added", None, trait))
            target.traits.append(trait)
    if seen and seen != target.last_seen_episode:
        changes.append(_change("character", target.character_id, "last_seen", str(target.last_seen_episode), str(seen)))
        target.last_seen_episode = seen
    repo.upsert_character(run_id, target)
    return target


def _apply_relationships(run_id: str, character: Character, u: ExtractedCharacter, changes: list[FieldChange]) -> None:
    current = repo.require_character(run_id, character.character_id)
    for rel in u.relationships:
        other = _resolve_character(run_id, rel.other, rel.other)
        if other is None or other.character_id == current.character_id:
            continue
        previous = current.relationships.get(other.character_id)
        if previous != rel.description:
            changes.append(_change("character", current.character_id, f"relationship:{other.character_id}", previous, rel.description))
            current.relationships[other.character_id] = rel.description
    repo.upsert_character(run_id, current)


def _apply_thread(
    run_id: str, episode_no: int, u: ExtractedThread, changes: list[FieldChange], new_ids: list[str], text: str | None
) -> bool:
    """Returns True if the update was applied. Updates need a real quote from the episode, and a planned thread cannot
    move before it is due: extractors otherwise mark every open thread as moved every episode."""
    if not _quoted(u.evidence, text):
        logger.warning("ignoring thread update without a real quote: %r", u.description[:60])
        return False
    target = repo.get_thread(run_id, u.thread_id) if u.thread_id else None
    if target is None:
        target = next((t for t in repo.get_threads(run_id) if t.description.strip().lower() == u.description.strip().lower()), None)

    if target is None:
        created = Thread(description=u.description, status=u.status, planted_episode=episode_no, last_touched_episode=episode_no)
        repo.upsert_thread(run_id, created)
        new_ids.append(created.thread_id)
        changes.append(_change("thread", created.thread_id, "created", None, created.description))
        return True

    if target.status == "planned" and target.planted_episode > episode_no + THREAD_LEAD_EPISODES:
        logger.warning("thread %r is scheduled for episode %d; ignoring an update at episode %d", target.description[:50], target.planted_episode, episode_no)
        return False

    status: ThreadStatus = u.status
    if target.status == "planned" and status != "planted":
        logger.warning("thread %s is still planned; clamping reported status %s to planted", target.thread_id, status)
        status = "planted"
    if THREAD_RANK[status] > THREAD_RANK[target.status]:
        changes.append(_change("thread", target.thread_id, "status", target.status, status))
        target.status = status
    elif status != target.status:
        logger.warning("ignoring thread status regression %s -> %s for %s", target.status, status, target.thread_id)
    if target.last_touched_episode != episode_no:
        changes.append(_change("thread", target.thread_id, "last_touched", str(target.last_touched_episode), str(episode_no)))
        target.last_touched_episode = episode_no
    repo.upsert_thread(run_id, target)
    return True


def apply_extraction(run_id: str, episode_no: int, extraction: Extraction, text: str | None = None) -> StateDelta:
    """Merge an extraction into live state and return the delta. Every change records its old value, so the delta doubles
    as an undo log (see revert_delta). When the episode text is given, claims must be backed by a quote from it."""
    changes: list[FieldChange] = []
    new_ids: list[str] = []

    resolved = [(u, _apply_character(run_id, episode_no, u, changes, new_ids, text)) for u in extraction.characters]
    for u, character in resolved:
        if character is not None:
            _apply_relationships(run_id, character, u, changes)
    updates = 0
    for thread_update in extraction.threads:
        if updates >= MAX_THREAD_UPDATES:
            break
        updates += _apply_thread(run_id, episode_no, thread_update, changes, new_ids, text)

    known = {f.statement.strip().lower() for f in repo.get_facts(run_id)}
    for f in extraction.new_facts[:MAX_NEW_FACTS]:
        if f.statement.strip().lower() in known:
            continue
        fact = Fact(statement=f.statement, established_episode=episode_no, tags=[t.lower() for t in f.tags])
        repo.add_fact(run_id, fact)
        new_ids.append(fact.fact_id)
        changes.append(_change("fact", fact.fact_id, "established", None, fact.statement))

    return StateDelta(run_id=run_id, episode_no=episode_no, changes=changes, new_entity_ids=new_ids, extraction=extraction.model_dump())


def _revert_character(run_id: str, c: FieldChange) -> None:
    if c.field == "created":
        repo.delete_character(run_id, c.entity_id)
        return
    character = repo.get_character(run_id, c.entity_id)
    if character is None:
        return
    if c.field == "status":
        character.status = cast(CharacterStatus, _old(c))
    elif c.field == "arc_stage":
        character.arc_stage = _old(c)
    elif c.field == "last_seen":
        character.last_seen_episode = int(_old(c))
    elif c.field == "alias_added":
        character.aliases = [a for a in character.aliases if a != c.new_value]
    elif c.field == "trait_added":
        character.traits = [t for t in character.traits if t != c.new_value]
    elif c.field.startswith("relationship:"):
        other_id = c.field.split(":", 1)[1]
        if c.old_value is None:
            character.relationships.pop(other_id, None)
        else:
            character.relationships[other_id] = c.old_value
    repo.upsert_character(run_id, character)


def _revert_thread(run_id: str, c: FieldChange) -> None:
    if c.field == "created":
        repo.delete_thread(run_id, c.entity_id)
        return
    thread = repo.get_thread(run_id, c.entity_id)
    if thread is None:
        return
    if c.field == "status":
        thread.status = cast(ThreadStatus, _old(c))
    elif c.field == "last_touched":
        thread.last_touched_episode = int(_old(c))
    repo.upsert_thread(run_id, thread)


def revert_delta(run_id: str, delta: StateDelta) -> None:
    """Exact inverse of apply_extraction. Changes are undone newest-first so a relationship to a new
    character is removed before that character is deleted."""
    for change in reversed(delta.changes):
        if change.entity_type == "character":
            _revert_character(run_id, change)
        elif change.entity_type == "thread":
            _revert_thread(run_id, change)
        else:
            repo.delete_fact(run_id, change.entity_id)
