from src.core.extractor import ExtractedFact, ExtractedRelationship, hard_differences
from src.core.state_delta import apply_extraction, revert_delta
from src.models import Character, Thread
from src.storage import repo
from tests.factories import ext_char, ext_thread, make_extraction


def _snapshot(run_id: str) -> dict:
    return {
        "characters": sorted(c.model_dump_json() for c in repo.get_characters(run_id)),
        "threads": sorted(t.model_dump_json() for t in repo.get_threads(run_id)),
        "facts": sorted(f.model_dump_json() for f in repo.get_facts(run_id)),
    }


def _seed(run_id: str):
    verma = Character(character_id="c_verma", name="Mr. Verma", aliases=["the old man"], arc_stage="hiding", last_seen_episode=2)
    kavi = Character(character_id="c_kavi", name="Kavi", role="protagonist", last_seen_episode=2)
    repo.upsert_character(run_id, verma)
    repo.upsert_character(run_id, kavi)
    repo.upsert_thread(run_id, Thread(thread_id="t_ledger", description="the ledger", status="planted", planted_episode=1, last_touched_episode=2))
    repo.upsert_thread(run_id, Thread(thread_id="t_future", description="the basement", status="planned", planted_episode=9, last_touched_episode=9))


def test_apply_then_revert_restores_exact_state(run_id):
    _seed(run_id)
    before = _snapshot(run_id)
    extraction = make_extraction(
        characters=[
            ext_char("Mr. Verma", "dead", character_id="c_verma", arc_stage="gone", new_traits=["limp"], new_aliases=["Verma-ji"]),
            ext_char("Kavi", character_id="c_kavi", relationships=[ExtractedRelationship(other="Rafiq", description="distrusts")]),
            ext_char("Rafiq", role="antagonist", new_traits=["watchful"]),
        ],
        threads=[ext_thread("the ledger", "escalated", "t_ledger"), ext_thread("a missing key", "planted")],
        new_facts=[ExtractedFact(statement="The lift is broken", tags=["Building"])],
    )

    delta = apply_extraction(run_id, 5, extraction)

    assert repo.get_character(run_id, "c_verma").status == "dead"
    assert repo.find_character_by_name_or_alias(run_id, "Verma-ji").character_id == "c_verma"
    rafiq = repo.find_character_by_name_or_alias(run_id, "Rafiq")
    assert repo.get_character(run_id, "c_kavi").relationships == {rafiq.character_id: "distrusts"}
    assert repo.get_thread(run_id, "t_ledger").status == "escalated"
    assert repo.get_facts(run_id)[0].tags == ["building"]
    assert len(delta.new_entity_ids) == 3

    revert_delta(run_id, delta)
    assert _snapshot(run_id) == before


def test_alias_resolves_to_existing_character_instead_of_duplicating(run_id):
    _seed(run_id)
    delta = apply_extraction(run_id, 5, make_extraction(characters=[ext_char("the old man")]))
    assert len(repo.get_characters(run_id)) == 2
    assert delta.new_entity_ids == []


def test_unknown_id_falls_back_to_name_then_creates(run_id):
    _seed(run_id)
    apply_extraction(run_id, 5, make_extraction(characters=[ext_char("Kavi", character_id="c_deleted_by_rollback")]))
    assert len(repo.get_characters(run_id)) == 2
    apply_extraction(run_id, 6, make_extraction(characters=[ext_char("Brand New", character_id="c_ghost")]))
    assert len(repo.get_characters(run_id)) == 3


def test_thread_status_cannot_regress_and_planned_can_be_planted(run_id):
    _seed(run_id)
    apply_extraction(run_id, 5, make_extraction(threads=[ext_thread("the ledger", "resolved", "t_ledger")]))
    apply_extraction(run_id, 6, make_extraction(threads=[ext_thread("the ledger", "planted", "t_ledger")]))
    assert repo.get_thread(run_id, "t_ledger").status == "resolved"
    apply_extraction(run_id, 9, make_extraction(threads=[ext_thread("the basement", "planted", "t_future")]))
    assert repo.get_thread(run_id, "t_future").status == "planted"


def test_duplicate_fact_is_skipped(run_id):
    _seed(run_id)
    fact = ExtractedFact(statement="The lift is broken", tags=[])
    apply_extraction(run_id, 5, make_extraction(new_facts=[fact]))
    delta = apply_extraction(run_id, 6, make_extraction(new_facts=[ExtractedFact(statement=" the lift is BROKEN ", tags=[])]))
    assert len(repo.get_facts(run_id)) == 1 and delta.changes == []


def test_hard_differences_status_change_and_terminal_events():
    old = make_extraction(characters=[ext_char("Rafiq", "alive", "c1")], threads=[ext_thread("ledger", "escalated", "t1")])
    new = make_extraction(characters=[ext_char("Rafiq", "dead", "c1")], threads=[ext_thread("ledger", "resolved", "t1")])
    diffs = hard_differences(old, new)
    assert "character 'Rafiq': status alive -> dead" in diffs
    assert "thread 'ledger': status escalated -> resolved" in diffs


def test_hard_differences_ignores_cosmetic_and_nonterminal_one_sided_claims():
    old = make_extraction(characters=[ext_char("Kavi", "alive", "c1", new_traits=["brave"], arc_stage="doubt")])
    new = make_extraction(
        characters=[
            ext_char("Kavi", "alive", "c1", new_traits=["courageous"], arc_stage="uncertain"),
            ext_char("Rafiq", "alive", "c2"),
        ],
        new_facts=[ExtractedFact(statement="lift is broken", tags=[])],
    )
    assert hard_differences(old, new) == []


def test_hard_differences_flags_vanished_death_and_new_character():
    old = make_extraction(characters=[ext_char("Rafiq", "dead", "c1")])
    new = make_extraction(characters=[ext_char("Stranger", "alive")])
    diffs = hard_differences(old, new)
    assert "character 'Rafiq': status dead -> absent" in diffs
    assert "character 'Stranger': introduced absent -> yes" in diffs


def test_extraction_accepts_a_model_that_omits_empty_and_nullable_fields():
    from src.core.extractor import Extraction

    parsed = Extraction.model_validate_json('{"summary": "x", "characters": [{"name": "Kavi", "status": "alive"}]}')
    character = parsed.characters[0]
    assert character.character_id is None and character.role is None and character.new_aliases == []
    assert character.appears_on_page is True and parsed.threads == [] and parsed.new_facts == []


def test_a_planned_thread_cannot_skip_straight_to_escalated_or_resolved(run_id):
    _seed(run_id)
    delta = apply_extraction(run_id, 3, make_extraction(threads=[ext_thread("the basement", "resolved", "t_future")]))
    assert repo.get_thread(run_id, "t_future").status == "planted"
    assert [(c.old_value, c.new_value) for c in delta.changes if c.field == "status"] == [("planned", "planted")]


EPISODE_TEXT = "The lift stopped between floors and Mr. Verma said nothing about the ledger as the lights failed again."
QUOTE = "Mr. Verma said nothing about the ledger as the lights failed"


def test_a_thread_update_needs_a_real_quote_from_the_episode(run_id):
    _seed(run_id)
    invented = make_extraction(threads=[ext_thread("the ledger", "escalated", "t_ledger").model_copy(update={"evidence": "a sentence that is nowhere in this episode"})])
    apply_extraction(run_id, 5, invented, EPISODE_TEXT)
    assert repo.get_thread(run_id, "t_ledger").status == "planted" and repo.get_thread(run_id, "t_ledger").last_touched_episode == 2

    quoted = make_extraction(threads=[ext_thread("the ledger", "escalated", "t_ledger").model_copy(update={"evidence": QUOTE.upper() + "!"})])
    apply_extraction(run_id, 5, quoted, EPISODE_TEXT)
    assert repo.get_thread(run_id, "t_ledger").status == "escalated" and repo.get_thread(run_id, "t_ledger").last_touched_episode == 5


def test_a_planned_thread_cannot_move_long_before_it_is_scheduled(run_id):
    _seed(run_id)
    repo.upsert_thread(run_id, Thread(thread_id="t_late", description="the far reveal", status="planned", planted_episode=60, last_touched_episode=60))
    quoted = make_extraction(threads=[ext_thread("the far reveal", "planted", "t_late").model_copy(update={"evidence": QUOTE})])
    delta = apply_extraction(run_id, 5, quoted, EPISODE_TEXT)
    assert repo.get_thread(run_id, "t_late").status == "planned" and delta.changes == []


def test_only_the_first_few_thread_updates_per_episode_are_applied(run_id):
    _seed(run_id)
    for i in range(6):
        repo.upsert_thread(run_id, Thread(thread_id=f"t{i}", description=f"thread {i}", status="planted", planted_episode=1, last_touched_episode=1))
    updates = [ext_thread(f"thread {i}", "escalated", f"t{i}").model_copy(update={"evidence": QUOTE}) for i in range(6)]
    apply_extraction(run_id, 5, make_extraction(threads=updates), EPISODE_TEXT)
    assert sum(1 for i in range(6) if repo.get_thread(run_id, f"t{i}").status == "escalated") == 4


def test_a_status_change_needs_a_quote_but_other_updates_still_apply(run_id):
    _seed(run_id)
    unquoted = ext_char("Mr. Verma", "dead", "c_verma", new_traits=["limp"])
    apply_extraction(run_id, 5, make_extraction(characters=[unquoted]), EPISODE_TEXT)
    verma = repo.get_character(run_id, "c_verma")
    assert verma.status == "alive" and "limp" in verma.traits

    quoted = ext_char("Mr. Verma", "dead", "c_verma").model_copy(update={"evidence": QUOTE})
    apply_extraction(run_id, 6, make_extraction(characters=[quoted]), EPISODE_TEXT)
    assert repo.get_character(run_id, "c_verma").status == "dead"


def test_new_characters_need_a_proper_name_not_a_description(run_id):
    _seed(run_id)
    descriptions = ["the voice", "clerk", "Kavi's mother", "the thirty-second compression", "woman at stop seventeen"]
    apply_extraction(run_id, 5, make_extraction(characters=[ext_char(n) for n in descriptions] + [ext_char("Dr. Elenor Voss")]), EPISODE_TEXT)
    assert sorted(c.name for c in repo.get_characters(run_id)) == ["Dr. Elenor Voss", "Kavi", "Mr. Verma"]


def test_new_facts_are_capped_per_episode(run_id):
    _seed(run_id)
    facts = [ExtractedFact(statement=f"fact number {i} about the building", tags=[]) for i in range(9)]
    apply_extraction(run_id, 5, make_extraction(new_facts=facts), EPISODE_TEXT)
    assert len(repo.get_facts(run_id)) == 5
