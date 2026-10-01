import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from src.core import cost, orchestrator, planner
from src.core.formatting import format_plan
from src.core.orchestrator import StateIntegrityError
from src.core.planner import PlanInvalidError, PlanLockedError
from src.demo import DEFAULT_SCRIPT, DemoStopped, run_demo
from src.export import export_run
from src.gates import AutoGate, ConsoleGate, ask, edit_text, wrap
from src.llm.base import LLMError
from src.llm.factory import ClientSet, build_clients
from src.llm.structured import StructuredOutputError
from src.logging_config import configure_logging
from src.models import DirectiveScope
from src.storage import db, repo

RUN_ID_HELP = "run id (see the `list` command)"
HANDLED_ERRORS = (DemoStopped, PlanInvalidError, PlanLockedError, StateIntegrityError, StructuredOutputError, LLMError)


def _require_run(run_id: str) -> None:
    if not db.db_path(run_id).exists():
        raise SystemExit(f"No such run: {run_id} (see `list`)")
    configure_logging(run_id)


def _clients(args: argparse.Namespace) -> ClientSet:
    try:
        return build_clients(args.provider)
    except KeyError as exc:
        raise SystemExit(f"Missing environment variable {exc}. Copy .env.example to .env and fill it in.")
    except ValueError as exc:
        raise SystemExit(str(exc))


def _gate(args: argparse.Namespace) -> AutoGate | ConsoleGate:
    return AutoGate() if args.auto_approve else ConsoleGate()


def _require_approved_plan(run_id: str) -> None:
    plan = repo.get_arc_plan(run_id)
    if plan is None or plan.status != "approved":
        raise SystemExit(f"Plan for {run_id} is not approved yet. Run: review-plan {run_id}")


def cmd_new(args: argparse.Namespace) -> None:
    clients = _clients(args)
    run = repo.create_run(args.premise)
    configure_logging(run.run_id)
    print(f"Run {run.run_id} created. Planning {args.episodes} episodes (this is one large call)...")
    planner.generate_arc_plan(run.run_id, clients.planner, total_episodes=args.episodes)
    if orchestrator.review_plan(run.run_id, clients, _gate(args)):
        print(f"\nPlan approved. Next: python main.py run {run.run_id}")
    else:
        print(f"\nPlan saved as a draft. Continue with: python main.py review-plan {run.run_id}")


def cmd_review_plan(args: argparse.Namespace) -> None:
    _require_run(args.run_id)
    if orchestrator.review_plan(args.run_id, _clients(args), _gate(args)):
        print(f"\nPlan approved. Next: python main.py run {args.run_id}")


def cmd_run(args: argparse.Namespace) -> None:
    _require_run(args.run_id)
    _require_approved_plan(args.run_id)
    written, outcome = orchestrator.run_episodes(args.run_id, _clients(args), _gate(args), count=args.count)
    run = repo.require_run(args.run_id)
    messages = {
        "complete": "All episodes in the plan are written.",
        "quit": f"Stopped. Resume any time with: python main.py resume {args.run_id}",
        "budget": "Stopped: run budget reached (MAX_RUN_COST_USD).",
        "written": f"Wrote {len(written)} episode(s).",
    }
    print(f"\n{messages[outcome]} Next episode: {run.current_episode}.")


def cmd_edit_episode(args: argparse.Namespace) -> None:
    _require_run(args.run_id)
    clients = _clients(args)
    episode = repo.get_episode(args.run_id, args.episode)
    if episode is None:
        raise SystemExit(f"Episode {args.episode} does not exist")
    new_text = Path(args.file).read_text() if args.file else edit_text(episode.text)
    if new_text.strip() == episode.text.strip():
        print("No changes.")
        return

    preview = orchestrator.preview_edit(args.run_id, clients, args.episode, new_text)
    print(f"\nEpisodes after {args.episode} that built on the old version: {preview.later_episodes or 'none'}")
    if preview.hard_differences:
        print("Plot-level differences from the original:")
        for diff in preview.hard_differences:
            print(f"  - {diff}")
    else:
        print("No plot-level differences detected (characters' status and thread outcomes unchanged).")
    print("Note: edits to facts, names or details are not detected automatically; invalidate if they matter.")

    if args.invalidate or args.keep:
        invalidate = args.invalidate
    elif preview.later_episodes:
        default = bool(preview.hard_differences)
        answer = ask(f"Mark episodes {preview.later_episodes[0]}-{preview.later_episodes[-1]} stale and regenerate them? [{'Y/n' if default else 'y/N'}] ").lower()
        invalidate = default if not answer else answer.startswith("y")
    else:
        invalidate = False

    impact = orchestrator.commit_edit(args.run_id, clients, preview, new_text, invalidate_later=invalidate)
    if impact.stale_episodes:
        print(f"\nEpisode {args.episode} updated. Stale: {impact.stale_episodes}. Regenerate in order with: python main.py run {args.run_id}")
    elif impact.accepted_inconsistency:
        print(f"\nEpisode {args.episode} updated. Later episodes kept; world state still reflects the old reading (logged as accepted).")
    else:
        print(f"\nEpisode {args.episode} updated; nothing downstream affected.")


def cmd_rebuild_state(args: argparse.Namespace) -> None:
    _require_run(args.run_id)
    rebuilt = orchestrator.rebuild_state(args.run_id, _clients(args), from_episode=args.from_episode)
    print(f"Re-derived world state from {len(rebuilt)} episode(s): {rebuilt or 'none'}")


def cmd_demo(args: argparse.Namespace) -> None:
    script = DEFAULT_SCRIPT.model_copy(update={"premise": args.premise, "plan_length": args.plan_length, "total_episodes": args.episodes})
    if args.resume:
        _require_run(args.resume)
    result = run_demo(script, Path(args.out), make_clients=lambda: _clients(args), resume_run_id=args.resume)
    print(f"\nDone: {result.episodes_written} episodes in run {result.run_id}; deliverables in {args.out}/")


def cmd_cost_report(args: argparse.Namespace) -> None:
    _require_run(args.run_id)
    print(cost.format_report(args.run_id))


def cmd_export(args: argparse.Namespace) -> None:
    _require_run(args.run_id)
    for path in export_run(args.run_id, Path(args.out)):
        print(path)


def cmd_show(args: argparse.Namespace) -> None:
    _require_run(args.run_id)
    if args.episode:
        episode = repo.get_episode(args.run_id, args.episode)
        if episode is None:
            raise SystemExit(f"Episode {args.episode} does not exist")
        print(f"EPISODE {episode.episode_no} [{episode.status}, {episode.source}, {episode.word_count} words]\nsummary: {episode.summary}\n\n{wrap(episode.text)}")
        return
    plan = repo.get_arc_plan(args.run_id)
    if plan:
        print(format_plan(plan, repo.get_characters(args.run_id), repo.get_threads(args.run_id), repo.get_facts(args.run_id)))
    print("\nEPISODES")
    for e in repo.get_episodes_range(args.run_id, 1, finalized_only=False):
        print(f"{e.episode_no:>3} [{e.status:<9}] {e.word_count:>3}w  {e.summary}")


def cmd_directives(args: argparse.Namespace) -> None:
    _require_run(args.run_id)
    run = repo.require_run(args.run_id)
    if args.add:
        scope: DirectiveScope = "next_n_episodes" if args.next else "permanent"
        orchestrator.add_directive(args.run_id, run.current_episode - 1, args.add, scope, args.next)
    if args.retire:
        repo.retire_directive(args.run_id, args.retire)
    for d in repo.get_active_directives(args.run_id, run.current_episode):
        window = f"through ep {d.expires_after_episode}" if d.expires_after_episode else "permanent"
        print(f"{d.directive_id}  ({window}, set at ep {d.created_at_episode})  {d.text}")


def cmd_list(args: argparse.Namespace) -> None:
    for run_id in db.list_run_ids():
        run = repo.require_run(run_id)
        plan = repo.get_arc_plan(run_id)
        status = plan.status if plan else "no plan"
        print(f"{run_id}  next ep {run.current_episode:<4} plan {status:<8} {run.premise[:70]}")


def _add_common(parser: argparse.ArgumentParser, auto: bool = True) -> None:
    parser.add_argument("--provider", choices=["anthropic", "openai", "bedrock"], help="override LLM_PROVIDER")
    if auto:
        parser.add_argument("--auto-approve", action="store_true", help="approve everything without prompting (critic revisions still apply)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="main.py", description="Agentic serial story writer with human-in-the-loop control")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("new", help="create a run, generate the arc plan, review it")
    p.add_argument("premise")
    p.add_argument("--episodes", type=int, default=200)
    _add_common(p)
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("review-plan", help="review or revise the arc plan before writing")
    p.add_argument("run_id", help=RUN_ID_HELP)
    _add_common(p)
    p.set_defaults(func=cmd_review_plan)

    for name, help_text in (("run", "write episodes from the current position"), ("resume", "same as run; continue a stopped run")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("run_id", help=RUN_ID_HELP)
        p.add_argument("--count", type=int, help="stop after this many episodes")
        _add_common(p)
        p.set_defaults(func=cmd_run)

    p = sub.add_parser("edit-episode", help="retroactively edit an approved episode")
    p.add_argument("run_id", help=RUN_ID_HELP)
    p.add_argument("episode", type=int)
    p.add_argument("--file", help="read the new text from a file instead of opening $EDITOR")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--invalidate", action="store_true", help="mark later episodes stale without asking")
    group.add_argument("--keep", action="store_true", help="keep later episodes without asking")
    _add_common(p, auto=False)
    p.set_defaults(func=cmd_edit_episode)

    p = sub.add_parser("rebuild-state", help="re-derive characters/threads/facts from the stored episode texts")
    p.add_argument("run_id", help=RUN_ID_HELP)
    p.add_argument("--from", dest="from_episode", type=int, default=1, help="first episode to re-extract (default 1)")
    _add_common(p, auto=False)
    p.set_defaults(func=cmd_rebuild_state)

    p = sub.add_parser("cost-report", help="spend so far and projection to the full run")
    p.add_argument("run_id", help=RUN_ID_HELP)
    p.set_defaults(func=cmd_cost_report)

    p = sub.add_parser("demo", help="run the whole reproducible demo (plan, HITL interventions, resume) and export the deliverables")
    p.add_argument("--premise", default=DEFAULT_SCRIPT.premise)
    p.add_argument("--plan-length", type=int, default=DEFAULT_SCRIPT.plan_length, help="episodes in the arc plan (default 200)")
    p.add_argument("--episodes", type=int, default=DEFAULT_SCRIPT.total_episodes, help="episodes to write (default 18)")
    p.add_argument("--out", default="demo", help="output folder (default: demo)")
    p.add_argument("--resume", metavar="RUN_ID", help="continue a demo that stopped early instead of starting a new run")
    _add_common(p, auto=False)
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("export", help="write the plan, episodes, human decisions, cost report and trace summary to a folder")
    p.add_argument("run_id", help=RUN_ID_HELP)
    p.add_argument("--out", default="demo", help="output folder (default: demo)")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("show", help="print the plan and episode list, or one episode")
    p.add_argument("run_id", help=RUN_ID_HELP)
    p.add_argument("--episode", type=int)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("directives", help="list, add or retire standing steering directives")
    p.add_argument("run_id", help=RUN_ID_HELP)
    p.add_argument("--add", help="new directive text")
    p.add_argument("--next", type=int, help="make --add apply to the next N episodes only")
    p.add_argument("--retire", help="directive id to retire")
    p.set_defaults(func=cmd_directives)

    p = sub.add_parser("list", help="list runs")
    p.set_defaults(func=cmd_list)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    handler: Callable[[argparse.Namespace], None] = args.func
    try:
        handler(args)
    except HANDLED_ERRORS as exc:
        hint = "" if isinstance(exc, DemoStopped) else "\nProgress so far is saved; re-run the same command to continue."
        print(f"\nError: {exc}{hint}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\nInterrupted. Progress is saved.", file=sys.stderr)
        raise SystemExit(130)
