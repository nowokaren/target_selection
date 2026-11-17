#!/usr/bin/env python3
"""Generate the priority flowchart with Graphviz (dot)."""

from pathlib import Path
import subprocess

OUT = Path(__file__).with_name("decision_flow.png")

DOT = r'''digraph priority {
  graph [rankdir=TB, bgcolor="white", pad="0.25", nodesep="0.42", ranksep="0.55",
         splines=polyline, fontname="DejaVu Sans", fontsize=18, labelloc=t,
         label="Flujo de prioridad y modo de observación\n\nPrimero se decide el valor científico; después, si cabe una visita útil"];
  node [shape=box, style="rounded,filled", color="#52677A", penwidth=1.4,
        fontname="DejaVu Sans", fontsize=10, fontcolor="#18324B", margin="0.16,0.10"];
  edge [color="#52677A", penwidth=1.2, arrowsize=0.75, fontname="DejaVu Sans", fontsize=9, fontcolor="#40566B"];

  start [label="Target candidato", fillcolor="#F1F3F5", fontstyle=bold];
  event_state [shape=diamond, style=filled, label="¿El evento terminó?\nVer: estado reportado", fillcolor="#EAF3FA"];
  plausible [shape=diamond, style=filled, label="¿Microlensing plausible?\nVer: forma, significancia de la subida, otros surveys", fillcolor="#EAF3FA"];
  has_casleo [shape=diamond, style=filled, label="¿CASLEO tiene cobertura útil?\nVer: épocas/filtros y fases cubiertas", fillcolor="#EAF3FA"];

  rise [shape=diamond, style=filled, label="¿Subida clara y significativa?\nMedir pendiente/significancia y consistencia", fillcolor="#EAF3FA"];
  stale [shape=diamond, style=filled, label="¿Señal previa plausible\ny faltan datos recientes?\nMedir brecha temporal vs. cadencia", fillcolor="#EAF3FA"];
  stage [shape=diamond, style=filled, label="¿En qué etapa está?\nTendencia actual y tiempo al máximo", fillcolor="#EAF3FA"];
  active [shape=diamond, style=filled, label="¿Sigue activo y agrega fase?\nComparar Δm previsto con error fotométrico", fillcolor="#EAF3FA"];
  question [shape=diamond, style=filled, label="¿Hay una pregunta puntual?\nFase sin cubrir o baseline requerida", fillcolor="#EAF3FA"];

  discard [label="DESCARTAR", fillcolor="#FCE8E6", style="rounded,filled,bold"];
  diagnostic [label="DIAGNÓSTICO", fillcolor="#FFF4D8", style="rounded,filled,bold"];
  review [label="REVISAR", fillcolor="#FFF4D8", style="rounded,filled,bold"];
  newrise [label="NUEVO-RISE", fillcolor="#E6F4F1", style="rounded,filled,bold"];
  newpeak [label="NUEVO-PEAK", fillcolor="#E6F4F1", style="rounded,filled,bold"];
  continuity [label="CONTINUIDAD", fillcolor="#EAF3FA", style="rounded,filled,bold"];
  opportunistic [label="OPORTUNISTA", fillcolor="#F1F3F5", style="rounded,filled,bold"];

  mode [shape=note, label="MODO DE OBSERVACIÓN\n(aplicar después de elegir categoría)\n\n≥40 min sobre altura límite: V + I\n(20 min por filtro)\n\nMenos tiempo: un filtro si S/N alcanza\n\nVer: altura/airmass vs. tiempo,\nmagnitud prevista y S/N por filtro\nSin medición útil: no programar", fillcolor="#E6F4F1", color="#52677A", fontsize=9];

  start -> event_state;
  event_state -> discard [label="Sí", color="#B44D48", fontcolor="#B44D48"];
  event_state -> plausible [label="No"];
  plausible -> review [label="No: evidencia insuficiente", color="#B17B15", fontcolor="#8B620F"];
  plausible -> has_casleo [label="Sí"];
  has_casleo -> rise [label="No: evento nuevo"];
  has_casleo -> active [label="Sí: seguimiento CASLEO"];
  rise -> stage [label="Sí"];
  rise -> stale [label="No"];
  stale -> diagnostic [label="Sí: visita de estado", color="#B17B15", fontcolor="#8B620F"];
  stale -> review [label="No: subida no significativa", color="#B17B15", fontcolor="#8B620F"];
  stage -> newrise [label="Subida temprana", color="#25806D", fontcolor="#25806D"];
  stage -> newpeak [label="Cerca del máximo", color="#25806D", fontcolor="#25806D"];
  stage -> opportunistic [label="Descenso"];
  stage -> review [label="Baseline / fase incierta", color="#B17B15", fontcolor="#8B620F"];
  active -> continuity [label="Sí", color="#3673A5", fontcolor="#3673A5"];
  active -> question [label="No"];
  question -> opportunistic [label="Sí: baseline/pregunta puntual"];
  question -> discard [label="No", color="#B44D48", fontcolor="#B44D48"];

  {rank=same; mode; event_state}
  {rank=sink; discard; diagnostic; newrise; newpeak; continuity; opportunistic; review}
  discard -> diagnostic -> newrise -> newpeak -> continuity -> opportunistic -> review [style=invis, weight=20];
}
'''


def draw() -> Path:
    subprocess.run(["dot", "-Tpng", "-o", str(OUT)], input=DOT, text=True, check=True)
    return OUT


if __name__ == "__main__":
    print(draw())
