"""Interactive CLI with live streaming of the agent's work.

    coding-agent                       # REPL against ./target_project
    coding-agent --dry-run             # plan & show diffs, never write
    coding-agent -c "run the tests"    # one-shot
    coding-agent --script demo.txt     # replay a scripted session (for recordings)

Meta commands: /help /tools /prefs /forget <id> /memory /trace [turn] /dry-run on|off /quit
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from coding_agent.agent import CodingAgent
from coding_agent.config import load_config
from coding_agent.observability.tracer import Span

console = Console(highlight=False)

ICONS = {"tool": "⚙", "llm": "✦", "decision": "↳", "status": "•", "memory": "◆", "feedback": "⟲"}
STYLES = {"tool": "cyan", "llm": "magenta", "decision": "yellow", "status": "bold blue",
          "memory": "green", "feedback": "red"}


class LiveRenderer:
    """Subscribes to tracer spans and prints a compact live log."""

    def __init__(self, verbose: bool = False) -> None:
        self.verbose = verbose

    def __call__(self, phase: str, span: Span) -> None:
        if span.kind == "node":
            if self.verbose and phase == "start":
                console.print(f"  [dim]▸ {span.name}[/dim]")
            return
        if span.kind in ("tool", "llm") and phase != "end":
            return
        icon, style = ICONS.get(span.kind, "·"), STYLES.get(span.kind, "white")
        if span.kind == "status":
            console.print(f"[{style}]{icon} {span.name}[/{style}]")
            return
        detail = span.attrs.get("summary") or span.attrs.get("reason") or ""
        if span.kind == "tool":
            ok = "[green]✓[/green]" if span.ok else "[red]✗[/red]"
            cached = " [dim](cached)[/dim]" if span.attrs.get("cached") else ""
            res = span.attrs.get("result") or span.attrs.get("error") or ""
            console.print(f"  [{style}]{icon} {span.name}[/{style}] [dim]{detail[:70]}[/dim] "
                          f"{ok} [dim]{res[:60]} {span.duration_ms}ms[/dim]{cached}")
        elif span.kind == "llm":
            console.print(f"  [{style}]{icon} llm:{span.name}[/{style}] "
                          f"[dim]{span.attrs.get('provider')} {span.duration_ms}ms[/dim]")
        else:
            console.print(f"  [{style}]{icon} {span.name}[/{style}] [dim]{str(detail)[:110]}[/dim]")


def show_help(agent: CodingAgent) -> None:
    from coding_agent.trajectories import REGISTRY
    t = Table(title="Trajectories", show_lines=False)
    t.add_column("name", style="bold")
    t.add_column("example")
    for tr in REGISTRY.values():
        t.add_row(tr.name, tr.examples[0])
    console.print(t)
    console.print("[dim]Meta: /tools /prefs /forget <id> /memory /trace [turn] "
                  "/dry-run on|off /quit · prefix a request with 'dry run:' to preview[/dim]")


def handle_meta(cmd: str, agent: CodingAgent) -> bool:
    rt = agent.rt
    parts = cmd.split()
    name = parts[0]
    if name in ("/quit", "/exit"):
        raise EOFError
    if name == "/help":
        show_help(agent)
    elif name == "/tools":
        t = Table(title="MCP tools")
        for col in ("tool", "read-only", "description"):
            t.add_column(col)
        for d in agent.tools.describe():
            t.add_row(d["name"], "yes" if d["read_only"] else "", d["description"][:70])
        console.print(t)
    elif name == "/prefs":
        prefs = rt.persistent.preferences
        if not prefs:
            console.print("[dim]No stored preferences.[/dim]")
        for p in prefs:
            chk = f" [green](enforced: {p['check']})[/green]" if p.get("check") else ""
            console.print(f"  [bold]{p['id']}[/bold] [{p['scope']}] {p['text']}{chk}")
        for lesson in rt.persistent.lessons:
            console.print(f"  [dim]lesson {lesson['id']} ×{lesson['count']}: {lesson['text'][:90]}"
                          "[/dim]")
    elif name == "/forget" and len(parts) > 1:
        removed = rt.persistent.remove_preference(parts[1])
        console.print(f"Removed {len(removed)} preference(s).")
    elif name == "/memory":
        console.print(rt.session.stats())
        ctx = rt.session.build_context(" ".join(parts[1:]) or "recent work", 800)
        console.print(Panel(ctx or "(empty)", title="session context (as retrieved)"))
    elif name == "/trace":
        turn = int(parts[1]) if len(parts) > 1 else None
        console.print(Panel(agent.tracer.render_tree(turn) or "(no spans)",
                            title=f"trace · turn {turn or agent.tracer.turn}"))
        if agent.tracer.path:
            try:
                shown = agent.tracer.path.relative_to(Path.cwd())
            except ValueError:
                shown = agent.tracer.path
            console.print(f"[dim]full JSONL: {shown}[/dim]")
    elif name == "/dry-run":
        on = len(parts) < 2 or parts[1] in ("on", "true", "1")
        rt.config.raw["dry_run"] = on
        console.print(f"dry-run {'ON — no files will be written' if on else 'OFF'}")
    else:
        console.print(f"[red]unknown command {name}[/red]")
    return True


def looks_like_shell_invocation(text: str) -> bool:
    """True for input like ``--dry-run`` or ``coding-agent -c ...`` typed at the REPL."""
    first = text.split(maxsplit=1)[0] if text.split() else ""
    return first in ("coding-agent", "coding_agent") or first.startswith("-")


def run_one(agent: CodingAgent, text: str) -> None:
    if text.startswith("/"):
        handle_meta(text, agent)
        return
    if looks_like_shell_invocation(text):
        console.print("[yellow]That looks like a command-line flag. Flags such as --dry-run, "
                      "--workspace, -v and -c go on the shell command line, e.g.\n"
                      "  coding-agent --dry-run\n  coding-agent -c \"run the tests\"\n"
                      "Quit with Ctrl-D first, or type a plain request here (/help for "
                      "commands).[/yellow]\n")
        return
    res = agent.run(text)
    console.print()
    console.print(Markdown(res["response"] or "(no response)"))
    route = res.get("route") or {}
    console.print(f"[dim]── {res['trajectory']} · {res['status']} · route: {route.get('source')} "
                  f"· {res['tool_calls']} tool calls (+{res['cached_calls']} cached) · "
                  f"{res['llm_calls']} llm calls · "
                  f"{res['elapsed_s']}s[/dim]\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="coding-agent")
    ap.add_argument("--config", default="agent.yaml" if Path("agent.yaml").exists() else None)
    ap.add_argument("--workspace")
    ap.add_argument("--llm", choices=["anthropic", "openai", "replay"])
    ap.add_argument("--replay-file")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", "-v", action="store_true", help="also print every graph node")
    ap.add_argument("-c", "--command", help="run a single request and exit")
    ap.add_argument("--script", help="file of requests to run in order (one per line)")
    ap.add_argument("--typing-delay", type=float, default=0.0)
    args = ap.parse_args(argv)

    overrides: dict = {"workspace": args.workspace}
    if args.dry_run:
        overrides["dry_run"] = True
    cfg = load_config(args.config, **overrides)
    if args.llm or args.replay_file:
        cfg.raw["llm"] = {**cfg.raw["llm"], **({"provider": args.llm} if args.llm else {}),
                          **({"replay_file": args.replay_file} if args.replay_file else {})}
        if args.replay_file and not args.llm:
            cfg.raw["llm"]["provider"] = "replay"

    try:
        agent = CodingAgent(cfg)
    except Exception as exc:
        console.print(f"[red]Failed to start agent: {exc}[/red]")
        return 1
    agent.tracer.listeners.append(LiveRenderer(args.verbose))
    try:
        shown_ws = cfg.workspace.relative_to(Path.cwd())
    except ValueError:
        shown_ws = cfg.workspace
    console.print(Panel.fit(
        f"[bold]coding-agent[/bold] · workspace [cyan]{shown_ws}[/cyan]\n"
        f"llm: {agent.rt.llm.name} · tools: {len(agent.tools.tools)} via "
        f"{len(cfg.server_configs())} MCP servers · prefs: {len(agent.rt.persistent.preferences)}"
        f"{' · [yellow]DRY RUN[/yellow]' if cfg.raw.get('dry_run') else ''}\n"
        "[dim]/help for commands · Ctrl-D to quit[/dim]"))
    try:
        if args.command:
            run_one(agent, args.command)
        elif args.script:
            for line in Path(args.script).read_text().splitlines():
                if not line.strip() or line.startswith("#"):
                    continue
                console.print("[bold green]>[/bold green] ", end="")
                for ch in line:
                    console.print(ch, end="")
                    if args.typing_delay:
                        time.sleep(args.typing_delay)
                console.print()
                run_one(agent, line.strip())
        else:
            while True:
                try:
                    text = console.input("[bold green]>[/bold green] ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if text:
                    try:
                        run_one(agent, text)
                    except EOFError:
                        break
    finally:
        agent.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
