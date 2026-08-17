"""Generate the architecture proposal PDF without external PDF dependencies."""

from __future__ import annotations

import textwrap
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUTPUT = Path("docs/target_selection_architecture_proposal.pdf")

BG = "#fbfcfe"
BLUE = "#d9ebff"
GREEN = "#dff4e6"
GRAY = "#edf1f5"
YELLOW = "#fff2c6"
RED = "#ffe1df"
INK = "#1d2733"
BORDER = "#5c6b7a"


def page(title: str):
    fig, ax = plt.subplots(figsize=(11, 8.5))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.text(0.055, 0.94, title, fontsize=20, weight="bold", color=INK, va="top")
    return fig, ax


def wrapped(ax, text: str, x: float, y: float, width: int = 95, size: int = 10, lineheight: float = 0.033, **kwargs):
    lines = []
    for para in text.split("\n"):
        if not para.strip():
            lines.append("")
            continue
        lines.extend(textwrap.wrap(para, width=width))
    for idx, line in enumerate(lines):
        ax.text(x, y - idx * lineheight, line, fontsize=size, color=INK, va="top", **kwargs)
    return y - max(len(lines), 1) * lineheight


def box(ax, xy, wh, text, color=GRAY, size=9.5):
    x, y = xy
    w, h = wh
    patch = FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.012,rounding_size=0.018",
        linewidth=1.1,
        edgecolor=BORDER,
        facecolor=color,
    )
    ax.add_patch(patch)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=size, color=INK, wrap=True)
    return patch


def arrow(ax, start, end):
    ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=13, linewidth=1.2, color=BORDER))


def text_page(pdf, title, body):
    fig, ax = page(title)
    wrapped(ax, body, 0.06, 0.86, width=105, size=10.5, lineheight=0.036)
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def current_flow(pdf):
    fig, ax = page("Current Workflow")
    nodes = {
        "Config": (0.08, 0.78, BLUE),
        "AnalysisConfig\nvalidation": (0.30, 0.78, BLUE),
        "AnalysisWorkflow": (0.52, 0.78, BLUE),
        "Source\nadapters": (0.74, 0.78, GREEN),
        "Target union\n+ provenance": (0.18, 0.57, GREEN),
        "MOP enrichment\n+ photometry": (0.42, 0.57, GREEN),
        "Local visibility": (0.66, 0.57, YELLOW),
        "TAP coverage": (0.14, 0.35, YELLOW),
        "Butler coadds": (0.36, 0.35, YELLOW),
        "Reference\nphotometry": (0.58, 0.35, YELLOW),
        "Products": (0.80, 0.35, RED),
        "Registry +\nrun outputs": (0.48, 0.14, GRAY),
    }
    centers = {}
    for label, (x, y, color) in nodes.items():
        box(ax, (x, y), (0.15, 0.09), label, color=color)
        centers[label] = (x + 0.075, y + 0.045)
    sequence = [
        "Config", "AnalysisConfig\nvalidation", "AnalysisWorkflow", "Source\nadapters",
        "Target union\n+ provenance", "MOP enrichment\n+ photometry", "Local visibility",
        "TAP coverage", "Butler coadds", "Reference\nphotometry", "Products", "Registry +\nrun outputs",
    ]
    for a, b in zip(sequence, sequence[1:]):
        arrow(ax, centers[a], centers[b])
    wrapped(ax, "Main current issue: the high-level source/adapters model is good, but target_selection_pipeline.py still owns too many unrelated tasks: visibility, Rubin coverage, coadds, photometry, summaries, sky plots, and dashboards.", 0.07, 0.06, width=110, size=10)
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def source_model(pdf):
    fig, ax = page("Proposed Source Model")
    box(ax, (0.38, 0.80), (0.24, 0.085), "DataSourceDefinition", BLUE, 11)

    columns = [
        ("Source kind", ["Survey / observing program\nHSH, JS, Rubin, ZTF, Gaia", "Event aggregator / broker\nMOP, OMP", "User target list / catalog\nCSV, LaStBeRu"], 0.06, GREEN),
        ("Access mode", ["python_api / http_api", "tap_butler", "local_csv / local_files", "database"], 0.31, GRAY),
        ("Capabilities", ["targets + parameters", "coverage + epochs", "photometry + objects", "images + coadds + cutouts"], 0.56, YELLOW),
        ("Run usage", ["candidate input", "planning telescope", "context / comparison", "light-curve or dashboard source"], 0.79, RED),
    ]

    for title, items, x, color in columns:
        box(ax, (x, 0.64), (0.16, 0.07), title, color, 9.8)
        arrow(ax, (0.50, 0.80), (x + 0.08, 0.71))
        y = 0.50
        for item in items:
            box(ax, (x, y), (0.16, 0.075), item, color, 7.8)
            y -= 0.105

    wrapped(
        ax,
        "Core change: reference/follow-up are not source classes. They describe how a survey is used in one run. The stable source definition is kind + access mode + capabilities; run usage is selected separately.",
        0.07, 0.08, width=110, size=10,
    )
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def task_model(pdf):
    fig, ax = page("Proposed Task Model")
    positions = {
        "targets": (0.08, 0.76, "Target\nselection", BLUE),
        "registry": (0.34, 0.76, "Registry\nupdate", GRAY),
        "match": (0.60, 0.76, "Match", GREEN),
        "visibility": (0.08, 0.53, "Visibility", YELLOW),
        "coverage": (0.34, 0.53, "Coverage", YELLOW),
        "photometry": (0.60, 0.53, "Photometry", GREEN),
        "images": (0.80, 0.53, "Images +\ncutouts", GREEN),
        "planning": (0.08, 0.30, "Planning\nsummary", RED),
        "lightcurves": (0.34, 0.30, "Light-curve\nPDF", RED),
        "dashboard": (0.60, 0.30, "Target\ndashboard", RED),
        "index": (0.80, 0.30, "Product\nindex", GRAY),
    }
    centers = {}
    for key, (x, y, label, color) in positions.items():
        box(ax, (x, y), (0.14, 0.09), label, color, 9.3)
        centers[key] = (x + 0.07, y + 0.045)
    for a, b in [
        ("targets", "registry"), ("registry", "match"), ("registry", "visibility"),
        ("match", "coverage"), ("match", "photometry"), ("coverage", "images"),
        ("photometry", "lightcurves"), ("images", "dashboard"), ("photometry", "dashboard"),
        ("visibility", "planning"), ("coverage", "planning"), ("planning", "index"),
        ("lightcurves", "index"), ("dashboard", "index"),
    ]:
        arrow(ax, centers[a], centers[b])
    wrapped(ax, "Each task should declare required inputs, optional inputs, outputs, and compatible source capabilities. This keeps dashboard-only, visibility-only, and photometry-only runs independent.", 0.07, 0.13, width=110, size=10)
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def data_ownership(pdf):
    fig, ax = page("Data Ownership")
    box(ax, (0.07, 0.70), (0.18, 0.10), "Provider-native\ncaches", BLUE)
    box(ax, (0.07, 0.53), (0.18, 0.10), "User CSV\ninventories", BLUE)
    box(ax, (0.07, 0.36), (0.18, 0.10), "Remote\nservices", BLUE)
    box(ax, (0.36, 0.53), (0.20, 0.12), "Adapters normalize\nsource data", GREEN, 10)
    box(ax, (0.67, 0.53), (0.20, 0.12), "TargetRegistry\nSQLite", YELLOW, 10)
    box(ax, (0.35, 0.25), (0.20, 0.10), "Run snapshots", GRAY)
    box(ax, (0.66, 0.25), (0.22, 0.10), "Products +\nshared reports", RED)
    for start in [(0.25, 0.75), (0.25, 0.58), (0.25, 0.41)]:
        arrow(ax, start, (0.36, 0.59))
    arrow(ax, (0.56, 0.59), (0.67, 0.59))
    arrow(ax, (0.77, 0.53), (0.45, 0.35))
    arrow(ax, (0.77, 0.53), (0.77, 0.35))
    wrapped(ax, "Normalized persistent facts belong in the registry: identities, authoritative coordinates, source records, epochs, photometry, matches, coverage summaries, and run metadata. Large provider-native files and images can remain as files with registry metadata.", 0.07, 0.12, width=110, size=10)
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def config_page(pdf):
    body = """
Recommended config direction:

1. Define each data source once with kind, access mode, and capabilities.
2. Select run usage separately: candidate_inputs, planning_surveys, context_surveys, photometry_sources, and image_sources.
3. Keep selection settings independent: target_names, bands, magnitude cuts, visibility dates/windows, and data-availability requirements.
4. Make tasks explicit: match, coverage, photometry, images, visibility, planning_summary, lightcurves_report, target_dashboard.
5. Keep product switches as the user-facing output layer, but allow tags such as product:target-report or photometry:dia to map to task groups.

Useful tags:

source:survey, source:aggregator, source:local-csv, targets:mop-visible, targets:observed, targets:user-list, targets:subset, match:catalog, visibility:local, coverage:survey, photometry:mop, photometry:survey, photometry:dia, photometry:coadd-forced, images:coadd, images:cutout, product:dashboard, product:lightcurves, product:planning-table, product:visibility-plots.
"""
    text_page(pdf, "Configuration and Functional Tags", body)


def refactor_page(pdf):
    body = """
Recommended refactor path:

1. Preserve run_analysis(config), the CLI, and the notebook workflow.
2. Define normalized source interfaces around kind, access mode, and capabilities. Do not make reference/follow-up separate source classes.
3. Define capability protocols: TargetProvider, EventDataProvider, CoverageProvider, EpochProvider, PhotometryProvider, ObjectCatalogProvider, ImageProvider, and CutoutProvider.
4. Split target_selection_pipeline.py into task modules for coverage, photometry, images, dashboards, summaries, and run assembly.
5. Promote MOP enrichment and photometry into provider capabilities, so MOP can be used for enrichment even when its daily visible target list is not selected.
6. Store match results and reference photometry in the registry with source, method, collection, counterpart ID, separation, status, and version.
7. Keep old product keywords as compatibility aliases while adding task/product groups.
8. Keep target reports in a shared target_reports/ folder and update reports by target name.

Do not rewrite everything at once. The current architecture is usable. The next changes should isolate the monolithic Rubin/product backend first, because that is where most complexity is concentrated.
"""
    text_page(pdf, "Refactor Recommendation", body)


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(OUTPUT) as pdf:
        text_page(
            pdf,
            "Target Selection Architecture Proposal",
            "This PDF describes the current organization of target_selection and proposes an incremental architecture that is easier to extend to MOP, OMP, HSH, JS, Rubin DP1/DP2, OGLE, Gaia, ZTF, local CSVs, and future sources. The central recommendation is to define sources by kind, access mode, and capabilities, then choose their run usage explicitly.",
        )
        current_flow(pdf)
        source_model(pdf)
        task_model(pdf)
        data_ownership(pdf)
        config_page(pdf)
        refactor_page(pdf)


if __name__ == "__main__":
    main()
