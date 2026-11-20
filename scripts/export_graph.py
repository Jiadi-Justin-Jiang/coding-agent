"""Export the compiled LangGraph as Mermaid + a clustered Graphviz diagram.

The diagram is generated from the *real* compiled graph, so it can't drift
from the code. Error edges (every node → recover) are omitted for legibility.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coding_agent.agent import CodingAgent  # noqa: E402
from coding_agent.config import load_config  # noqa: E402
from coding_agent.llm.providers import ReplayLLM  # noqa: E402
from coding_agent.observability.tracer import Tracer  # noqa: E402

COLORS = {"explore": "#dbeafe", "edit": "#dcfce7", "run": "#fef3c7", "correct": "#fee2e2",
          "remember": "#ede9fe", "chat": "#f3f4f6"}
LABELS = {"explore": "Read / Explore", "edit": "Edit (verify + self-correct)", "run": "Run & React",
          "correct": "Correction", "remember": "Persistent memory", "chat": "Fallback"}


def main() -> None:
    agent = CodingAgent(load_config(None, workspace=str(ROOT / "target_project")),
                        llm=ReplayLLM(Tracer("x")), trace=False)
    g = agent.graph.get_graph()
    agent.close()
    out = ROOT / "docs"
    out.mkdir(exist_ok=True)
    (out / "state_graph.mmd").write_text(g.draw_mermaid())

    lines = ["digraph G {", 'rankdir=TB; nodesep=0.35; ranksep=0.45; fontname="Helvetica";',
             'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11, '
             'fillcolor="#ffffff"];', 'edge [fontname="Helvetica", fontsize=9, color="#555555"];',
             '__start__ [label="user request", shape=oval, fillcolor="#111827", fontcolor=white];',
             '__end__ [label="response", shape=oval, fillcolor="#111827", fontcolor=white];',
             'route [label="route\\n(rules → LLM fallback)", fillcolor="#fde68a"];',
             'load_context [label="load_context\\n(retrieve memory)", fillcolor="#bbf7d0"];',
             'recover [label="recover\\n(rollback open txn)", fillcolor="#fecaca"];',
             'finalize [label="finalize\\n(promote to session memory)", fillcolor="#e5e7eb"];']
    groups: dict[str, list[str]] = {}
    for n in g.nodes:
        if "_" in n and not n.startswith("__") and n.split("_")[0] in COLORS:
            groups.setdefault(n.split("_")[0], []).append(n)
    for grp, nodes in groups.items():
        lines.append(f'subgraph cluster_{grp} {{ label="{LABELS[grp]}"; style="rounded,filled"; '
                     f'fillcolor="{COLORS[grp]}"; color="#9ca3af"; fontsize=12;')
        for n in nodes:
            lines.append(f'  {n} [label="{n.split("_", 1)[1]}"];')
        lines.append("}")
    for e in g.edges:
        if e.target == "recover":
            continue
        style = ' [style=dashed]' if e.conditional else ""
        if e.source == "edit_verify" and e.target == "edit_fix":
            style = ' [style=dashed, color="#dc2626", label=" fail &\\n budget ok"]'
        if e.source == "edit_fix" and e.target == "edit_apply":
            style = ' [style=dashed, color="#dc2626"]'
        lines.append(f"{e.source} -> {e.target}{style};")
    lines.append('note [shape=note, fillcolor="#fff7ed", label="Every node also has a guarded '
                 'edge → recover\\n(exceptions, step budget). Dashed = conditional."];')
    lines.append("}")
    dot = out / "state_graph.dot"
    dot.write_text("\n".join(lines))
    for fmt in ("svg", "png"):
        subprocess.run(["dot", f"-T{fmt}", "-Gdpi=130", str(dot), "-o", str(out / f"state_graph.{fmt}")],
                       check=True)
    print(f"wrote {out}/state_graph.{{mmd,dot,svg,png}}")


if __name__ == "__main__":
    main()
