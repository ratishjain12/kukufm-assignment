from src.core import critic
from src.core.critic import CriticVerdict, Issue
from src.models import Character, Episode, Fact
from tests.factories import make_context, words
from tests.fakes import ScriptedClient


def _episode(no: int, text: str) -> Episode:
    return Episode(episode_no=no, run_id="r", beat_id="b", text=text, status="approved")


def _verdict(issues=None, hook_strong=True) -> CriticVerdict:
    return CriticVerdict(issues=issues or [], hook_strong=hook_strong, hook_note="flat")


def test_clean_draft_passes(run_id):
    result = critic.check(ScriptedClient([_verdict()]), make_context(run_id), words(500))
    assert result.passed and result.notes == []


def test_word_count_is_a_blocker_without_judge_help(run_id):
    result = critic.check(ScriptedClient([_verdict()]), make_context(run_id), words(300))
    assert not result.passed
    assert "300 words" in result.blockers[0]


def test_recycled_phrasing_blocks_and_names_source_episode(run_id):
    old = words(500, "old")
    ctx = make_context(run_id, recent=[_episode(4, old)])
    result = critic.check(ScriptedClient([_verdict()]), ctx, old)
    assert any("recycled from episode 4" in b for b in result.blockers)


def test_fresh_text_is_not_flagged_as_recycled(run_id):
    ctx = make_context(run_id, recent=[_episode(4, words(500, "old"))])
    assert critic.recycled_phrasing(words(500, "new"), ctx)[0] == 0.0




def test_minor_issues_alone_do_not_fail(run_id):
    verdict = _verdict(issues=[Issue(category="pacing", severity="minor", description="meh")])
    assert critic.check(ScriptedClient([verdict]), make_context(run_id), words(500)).passed


def test_dead_character_mention_is_a_hint_to_the_judge_not_a_failure(run_id):
    roster = [Character(name="Mr. Verma", aliases=["the old man"], status="dead")]
    client = ScriptedClient([_verdict()])
    result = critic.check(client, make_context(run_id, roster=roster), words(500) + " the old man smiled")
    assert result.passed
    assert "Dead characters named in the draft: Mr. Verma" in client.calls[0][1]


def test_judge_sees_recent_endings(run_id):
    ctx = make_context(run_id, recent=[_episode(4, words(100) + " THE-LAST-LINE")])
    client = ScriptedClient([_verdict()])
    critic.check(client, ctx, words(500))
    assert "THE-LAST-LINE" in client.calls[0][1]


def test_critic_retrieves_facts_by_entities_in_the_draft_not_just_the_writers_context(run_id):
    from src.models import Fact
    from src.storage import repo

    repo.add_fact(run_id, Fact(statement="The lift has been broken since 2019", established_episode=3, tags=["lift"]))
    repo.add_fact(run_id, Fact(statement="Unrelated fact about boats", established_episode=3, tags=["boat"]))
    client = ScriptedClient([_verdict()])
    critic.check(client, make_context(run_id), words(500) + " she pressed the lift button")
    prompt = client.calls[0][1]
    assert "broken since 2019" in prompt and "boats" not in prompt


def test_tag_matching_uses_word_boundaries(run_id):
    from src.models import Fact
    from src.storage import repo

    repo.add_fact(run_id, Fact(statement="Rider fact", established_episode=1, tags=["rider"]))
    client = ScriptedClient([_verdict()])
    critic.check(client, make_context(run_id), words(500) + " the provider called")
    assert "Rider fact" not in client.calls[0][1]


def test_word_count_notes_say_exactly_how_much_to_cut_or_add(run_id):
    long = critic.check(ScriptedClient([_verdict()]), make_context(run_id), words(760))
    assert f"cut roughly {760 - critic.TARGET_WORDS} words" in long.blockers[0]
    short = critic.check(ScriptedClient([_verdict()]), make_context(run_id), words(300))
    assert f"add roughly {critic.TARGET_WORDS - 300} words" in short.blockers[0]



SEVEN_FLOORS = Fact(statement="The building has seven floors", established_episode=0, tags=["core"])










def test_stray_cjk_characters_are_a_hard_blocker(run_id):
    result = critic.check(ScriptedClient([_verdict()]), make_context(run_id), words(500) + " an old woman 検 smiled")
    assert not result.passed and "Stray CJK" in result.blockers[0]


def test_judge_findings_never_block_a_draft_even_when_cited_and_rated_blocker(run_id):
    findings = [
        Issue(category="contradiction", severity="blocker", description="names a new recipient", conflicts_with="The building has seven floors"),
        Issue(category="directive", severity="blocker", description="ignores feedback", conflicts_with="End on dialogue"),
    ]
    result = critic.check(ScriptedClient([_verdict(issues=findings, hook_strong=False)]), make_context(run_id, facts=[SEVEN_FLOORS]), words(500))
    assert result.passed and result.blockers == []
    assert result.minor_notes == [
        "[contradiction] names a new recipient (cites: The building has seven floors) (advice)",
        "[directive] ignores feedback (cites: End on dialogue) (advice)",
        "[hook] Weak or repeated ending: flat (advice)",
    ]


def test_objective_rules_still_block_while_the_judge_only_advises(run_id):
    advice = Issue(category="pacing", severity="minor", description="slow middle")
    result = critic.check(ScriptedClient([_verdict(issues=[advice])]), make_context(run_id), words(300))
    assert not result.passed and "300 words" in result.blockers[0]
    assert result.minor_notes == ["[pacing] slow middle (advice)"]


def test_an_overused_content_word_is_a_hard_blocker_but_character_names_are_exempt(run_id):
    tic = " ".join(["seventeen"] * 14) + " " + words(500)
    result = critic.check(ScriptedClient([_verdict()]), make_context(run_id), tic)
    assert not result.passed and "'seventeen' appears 14 times" in result.blockers[0]

    roster = [Character(name="Kavi Nair", role="protagonist")]
    names = " ".join(["Kavi"] * 30) + " " + words(500)
    assert critic.check(ScriptedClient([_verdict()]), make_context(run_id, roster=roster), names).passed


def test_ordinary_repetition_is_not_flagged(run_id):
    ordinary = " ".join(["phone"] * 6 + ["light"] * 7) + " " + words(500)
    assert critic.check(ScriptedClient([_verdict()]), make_context(run_id), ordinary).passed


def test_function_words_are_not_a_tic(run_id):
    natural = " ".join(["their"] * 14 + ["there"] * 12) + " " + words(500)
    assert critic.check(ScriptedClient([_verdict()]), make_context(run_id), natural).passed
