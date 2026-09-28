"""Interactive terminal workflow for manually selected visibility plots."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from typing import Sequence

import pandas as pd

from target_selection.tasks.visibility import evaluate_visibility
from visibility_plotter import compile_visibility_plot_pages, plot_selected_visibility, summarize_visibility_selection

ALIASES = {
    "Target": ("Target", "target", "target_id", "object", "name"),
    "RA_deg": ("RA_deg", "ra_deg", "RA", "ra"),
    "Dec_deg": ("Dec_deg", "dec_deg", "Dec", "dec"),
}

def load_targets_csv(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Target CSV not found: {path.resolve()}\n"
            f"Current directory: {Path.cwd()}\n"
            "Use a path that exists, for example "
            "data/hsh_data/hsh_objects.csv from the target_selection repository."
        )
    frame = pd.read_csv(path)
    rename = {}
    for canonical, aliases in ALIASES.items():
        found = next((col for col in aliases if col in frame), None)
        if found is None:
            raise ValueError(f"{path} debe tener {canonical}; aliases aceptados: {aliases}")
        rename[found] = canonical
    frame = frame.rename(columns=rename).copy()
    frame["Target"] = frame["Target"].astype(str).str.strip()
    frame["RA_deg"] = pd.to_numeric(frame["RA_deg"], errors="coerce")
    frame["Dec_deg"] = pd.to_numeric(frame["Dec_deg"], errors="coerce")
    frame = frame.loc[frame["Target"].ne("")].drop_duplicates("Target").reset_index(drop=True)
    if frame.empty:
        raise ValueError(f"No se encontraron targets en {path}.")
    return frame

def _label(row: pd.Series) -> str:
    mag = pd.to_numeric(row.get("mag_now"), errors="coerce")
    return f"{row['Target']}  |  mag={mag:.2f}" if pd.notna(mag) and mag > 0 else str(row["Target"])

def interactive_target_selection(targets: pd.DataFrame, *, initial_match: str = "") -> pd.DataFrame:
    try:
        from prompt_toolkit.shortcuts import checkboxlist_dialog, prompt
    except ImportError as exc:
        raise RuntimeError("Instalá el selector interactivo con: python -m pip install -e '.[interactive]'") from exc
    query = initial_match or prompt("Filtrar por nombre (Enter = todos): ", default="").strip()
    candidates = targets if not query else targets.loc[targets["Target"].str.contains(query, case=False, na=False, regex=False)]
    if candidates.empty:
        raise ValueError(f"No hay coincidencias para {query!r}.")
    values = [(row["Target"], _label(row)) for _, row in candidates.iterrows()]
    selected = checkboxlist_dialog(
        title="Seleccionar targets para visibilidad",
        text=f"{len(candidates)} coincidencias. Flechas, Espacio, Enter o clic del mouse.",
        ok_text="Generar PDF", cancel_text="Cancelar", values=values,
    ).run()
    if not selected:
        raise ValueError("No se seleccionó ningún target.")
    return targets.loc[targets["Target"].isin(selected)].copy()

def select_targets(targets: pd.DataFrame, *, names: Sequence[str] | None = None,
                   selection_file: str | Path | None = None, match: str = "",
                   interactive: bool = True) -> pd.DataFrame:
    if selection_file:
        selected_file = pd.read_csv(selection_file)
        column = next((c for c in ("Target", "target", "target_id", "name") if c in selected_file), None)
        if column is None:
            raise ValueError(f"{selection_file} debe tener una columna Target.")
        names = selected_file[column].astype(str).str.strip().tolist()
    if names:
        wanted = {str(name).strip() for name in names if str(name).strip()}
        selected = targets.loc[targets["Target"].isin(wanted)].copy()
        missing = sorted(wanted - set(selected["Target"]))
        if missing:
            raise ValueError(f"Targets no encontrados: {', '.join(missing)}")
        return selected
    if not interactive:
        raise ValueError("Usá --names, --selection-file o quitá --non-interactive.")
    return interactive_target_selection(targets, initial_match=match)

def _night_rows(selection: pd.DataFrame, start_date: str, end_date: str, only_visible: bool):
    for night in pd.date_range(start_date, end_date, freq="D"):
        text = night.date().isoformat()
        nightly = selection.loc[selection["observation_date"].astype(str).eq(text)].copy()
        if only_visible:
            nightly = nightly.loc[nightly["selected_for_visibility"].astype(bool)]
        if not nightly.empty:
            yield text, nightly

def run_visibility_report(targets: pd.DataFrame, *, start_date: str, end_date: str | None = None,
                          observatory: str = "El Leoncito", minimum_altitude: float = 40.0,
                          minimum_minutes: float = 90.0, time_step_minutes: int = 1,
                          observing_windows: tuple[str, str] | None = None,
                          output_dir: str | Path = "outputs/visibility_interactive",
                          only_visible: bool = False) -> dict[str, object]:
    end_date = end_date or start_date
    output_dir = Path(output_dir)
    plot_dir = output_dir / "nightly_plots"
    plot_dir.mkdir(parents=True, exist_ok=True)
    for stale in plot_dir.glob("*_selected_visibility.png"):
        stale.unlink()
    selection = evaluate_visibility(
        targets, start_date=start_date, end_date=end_date, observatory=observatory,
        minimum_altitude_deg=minimum_altitude, minimum_observable_minutes=minimum_minutes,
        time_step_minutes=time_step_minutes, observing_windows=observing_windows, verbose=True,
    )
    selection_path = output_dir / "visibility_selection.csv"
    summary_path = output_dir / "visibility_summary.csv"
    targets_path = output_dir / "selected_targets.csv"
    selection.to_csv(selection_path, index=False)
    summarize_visibility_selection(selection).to_csv(summary_path, index=False)
    targets.to_csv(targets_path, index=False)
    plot_paths = []
    for night, nightly in _night_rows(selection, start_date, end_date, only_visible):
        path = plot_dir / f"{night}_selected_visibility.png"
        plot_selected_visibility(nightly, night, path, observatory=observatory,
                                 minimum_altitude=minimum_altitude, time_step_minutes=time_step_minutes,
                                 observing_windows=observing_windows)
        plot_paths.append(path)
    if not plot_paths:
        raise ValueError("No hay noches con targets para graficar.")
    pdf_path = output_dir / "visibility_report.pdf"
    compile_visibility_plot_pages(plot_dir, pdf_path)
    config = dict(start_date=start_date, end_date=end_date, observatory=observatory,
                  minimum_altitude=minimum_altitude, minimum_minutes=minimum_minutes,
                  time_step_minutes=time_step_minutes, observing_windows=observing_windows,
                  only_visible=only_visible, n_targets=len(targets),
                  n_nights_with_plots=len(plot_paths))
    (output_dir / "visibility_config.json").write_text(json.dumps(config, indent=2, default=str), encoding="utf-8")
    return {"pdf": pdf_path, "summary": summary_path, "selection": selection_path, "plots": plot_paths}

def build_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--targets", required=True, help="CSV con Target, RA_deg y Dec_deg.")
    parser.add_argument("--start-date", required=True, help="Primera noche, YYYY-MM-DD.")
    parser.add_argument("--end-date", help="Última noche inclusive.")
    parser.add_argument("--observatory", default="El Leoncito")
    parser.add_argument("--minimum-altitude", type=float, default=40.0)
    parser.add_argument("--minimum-minutes", type=float, default=90.0)
    parser.add_argument("--time-step", type=int, default=1)
    parser.add_argument("--observing-window", nargs=2, metavar=("START", "END"))
    parser.add_argument("--output", default="outputs/visibility_interactive")
    parser.add_argument("--match", default="", help="Filtro parcial inicial.")
    parser.add_argument("--names", help="Nombres separados por comas; modo reproducible.")
    parser.add_argument("--selection-file", help="CSV con una columna Target.")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--only-visible", action="store_true", help="Excluir targets/noches que no pasan el corte.")

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="target-selection visibility",
        description="Seleccionar targets y generar curvas de visibilidad en PDF.")
    build_parser(parser)
    args = parser.parse_args(argv)
    targets = load_targets_csv(args.targets)
    names = [x.strip() for x in args.names.split(",")] if args.names else None
    selected = select_targets(targets, names=names, selection_file=args.selection_file, match=args.match,
                              interactive=not args.non_interactive and names is None and args.selection_file is None)
    print(f"[visibility] Targets seleccionados: {len(selected)}", flush=True)
    result = run_visibility_report(
        selected, start_date=args.start_date, end_date=args.end_date,
        observatory=args.observatory, minimum_altitude=args.minimum_altitude,
        minimum_minutes=args.minimum_minutes, time_step_minutes=args.time_step,
        observing_windows=tuple(args.observing_window) if args.observing_window else None,
        output_dir=args.output, only_visible=args.only_visible)
    print(f"[visibility] PDF: {result['pdf']}", flush=True)
    print(f"[visibility] Resumen: {result['summary']}", flush=True)
    return 0
