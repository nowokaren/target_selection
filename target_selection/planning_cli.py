"""Installed command-line entry point for seasonal observing plans."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import pandas as pd

from .planning import (
    add_latest_magnitude, add_ogle_event_parameters, apply_magnitude_limit, collect_photometry, load_candidates,
    save_planning_pdf, save_priority_products, save_target_sky_map,
    save_visibility_products, summarize_visibility,
)
from .prioritization import evaluate_priority, select_schedulable_targets
from .cache_paths import CACHE_ROOT, OUTPUT_ROOT, resolve_project_path
from .config import load_config
from .tasks.visibility import evaluate_visibility as evaluate_nightly_visibility


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a seasonal target-selection report.")
    parser.add_argument("--config", help="TOML/JSON run and source settings.")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--providers", default=None, help="Comma-separated TOM names; default: all registered TOMs.")
    parser.add_argument("--observatory")
    parser.add_argument("--output-dir")
    parser.add_argument("--magnitude-max", type=float)
    parser.add_argument("--minimum-altitude-deg", type=float)
    parser.add_argument("--minimum-visible-minutes", type=float)
    parser.add_argument("--with-photometry", action="store_true")
    parser.add_argument("--photometry-scope", choices=("all", "visible"), default="all", help="Download photometry for all candidates or only those visible on at least one requested night.")
    parser.add_argument("--with-lightcurves", action="store_true")
    parser.add_argument("--with-lsst-cutouts", action="store_true")
    args = parser.parse_args(argv)
    config = load_config(args.config) if args.config else None
    args.start_date = args.start_date or (config.start_date if config else None)
    args.end_date = args.end_date or (config.resolved_end_date if config else None)
    if not args.start_date or not args.end_date:
        parser.error("--start-date and --end-date are required unless supplied by --config.")
    args.observatory = args.observatory or (config.observatory if config else "El Leoncito")
    args.output_dir = args.output_dir or (config.output_dir if config else str(OUTPUT_ROOT / "seasonal_plan"))
    args.magnitude_max = args.magnitude_max if args.magnitude_max is not None else (config.selection.maximum_current_magnitude if config else None)
    args.minimum_altitude_deg = args.minimum_altitude_deg if args.minimum_altitude_deg is not None else (config.selection.minimum_altitude_deg if config else 40.0)
    args.minimum_visible_minutes = args.minimum_visible_minutes if args.minimum_visible_minutes is not None else (config.selection.minimum_observable_minutes if config else 90.0)
    output = resolve_project_path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    providers = None if args.providers is None else tuple(item.strip().lower() for item in args.providers.split(",") if item.strip())
    if providers is None and config is not None:
        providers = tuple(config.target_providers)
    if providers is None:
        from .data_source import data_source
        effective_providers = list(data_source.list(kind="tom"))
    else:
        effective_providers = list(providers)
    provider_settings = {
        name: dict(config.provider_specs[name].options)
        for name in effective_providers
        if config is not None and name in config.provider_specs
    }
    run_config = {**vars(args), "output_dir": str(output), "effective_providers": effective_providers, "provider_settings": provider_settings}
    (output / "run_config.json").write_text(json.dumps(run_config, indent=2) + "\n")
    targets = load_candidates(providers, start_date=args.start_date, end_date=args.end_date, observatory=args.observatory, minimum_altitude_deg=args.minimum_altitude_deg, minimum_observable_minutes=args.minimum_visible_minutes, observing_windows=config.selection.observing_windows if config else None, include_photometry=args.with_photometry, provider_settings=provider_settings, cache_dir=CACHE_ROOT)
    if targets.empty:
        print("No targets were loaded.")
        return 1
    targets = add_ogle_event_parameters(targets)
    targets.to_csv(output / "targets_loaded.csv", index=False)
    photometry_targets = targets
    if args.with_photometry and args.photometry_scope == "visible":
        nightly = evaluate_nightly_visibility(
            targets, start_date=args.start_date, end_date=args.end_date,
            observatory=args.observatory, minimum_altitude_deg=args.minimum_altitude_deg,
            minimum_observable_minutes=args.minimum_visible_minutes,
        )
        preliminary_summary = summarize_visibility(nightly)
        visible_ids = set(preliminary_summary.loc[preliminary_summary["n_visible_nights"].gt(0), "target_id"].astype(str))
        photometry_targets = targets.loc[targets["target_id"].astype(str).isin(visible_ids)].copy()
        print(f"Photometry scope: {len(photometry_targets):,}/{len(targets):,} candidates visible on at least one night.", flush=True)
    observations = collect_photometry(photometry_targets, providers=providers, cache_dir=CACHE_ROOT) if args.with_photometry else pd.DataFrame()
    survey_epochs, survey_photometry = _load_local_surveys(config, targets, verbose=True)
    if not survey_photometry.empty:
        observations = pd.concat([observations, survey_photometry], ignore_index=True, sort=False)
    if args.with_photometry:
        observations.to_csv(output / "observations.csv", index=False)
        targets["photometry_requested"] = targets["target_id"].astype(str).isin(set(photometry_targets["target_id"].astype(str)))
        targets = add_latest_magnitude(targets, observations)
        targets.to_csv(output / "targets_loaded.csv", index=False)
    candidates = apply_magnitude_limit(targets, args.magnitude_max)
    candidates.to_csv(output / "targets_after_magnitude.csv", index=False)
    _, visibility_summary, visibility_plots, visibility_png = save_visibility_products(targets, output / "visibility", start_date=args.start_date, end_date=args.end_date, observatory=args.observatory, minimum_altitude_deg=args.minimum_altitude_deg, minimum_observable_minutes=args.minimum_visible_minutes)
    priority = evaluate_priority(targets, observations, visibility_summary, as_of=args.start_date, output_dir=output / "tables")
    _, priority_png = save_priority_products(priority, output / "tables", name="priority_assessment", title="Evidence-based priority assessment", subtitle="Scientific categories come from the shared evaluator; visibility and magnitude are feasibility evidence.", write_csv=False)
    selected = select_schedulable_targets(candidates, priority)
    selected.to_csv(output / "selected_targets.csv", index=False)
    sky_map = save_target_sky_map(selected, output / "sky_map.png", title="Selected targets")
    cutout_images = []
    if args.with_lsst_cutouts and not selected.empty:
        from .data_source import data_source
        lsst_coverage = data_source.lsst.coverage(selected, image_kinds=("individual", "coadd"), properties=("maglim", "psf_size", "exposure_time"))
        lsst_coverage.to_csv(output / "lsst_coverage.csv", index=False)
        data_source.lsst.get_image(lsst_coverage, kind="coadd", output_dir=output / "cutouts" / "png", fits_dir=output / "cutouts" / "fits", bands=tuple("ugrizy"), add_rgb=False)
        cutout_images = sorted((output / "cutouts" / "png").glob("*.png"))
    lightcurve_pngs = []
    if args.with_lightcurves and not selected.empty:
        from . import report
        layers = _plot_layers(effective_providers, config)
        lightcurve_pngs = report.create_lightcurve_plots(selected, output_dir=output / "lightcurves", observatory_epochs=survey_epochs, observatory_photometry=observations, layers=layers, observation_date=args.start_date, overwrite=True)
    save_planning_pdf(output / "seasonal_observing_plan.pdf", sky_map=sky_map, table_images=(priority_png, visibility_png), visibility_images=visibility_plots, lightcurve_images=lightcurve_pngs, cutout_images=cutout_images)
    print(f"Seasonal plan saved to {output}")
    print(f"Targets loaded: {len(targets):,}; selected: {len(selected):,}")
    print(f"PDF: {output / 'seasonal_observing_plan.pdf'}")
    return 0


def _load_local_surveys(config, targets: pd.DataFrame, *, verbose: bool):
    """Import configured local survey files and return plot-ready rows."""
    empty_epochs = pd.DataFrame(columns=["Target", "provider", "mjd", "band", "exptime_s", "usable"])
    empty_photometry = pd.DataFrame()
    if config is None or not config.followup_surveys:
        return empty_epochs, empty_photometry

    from .sources import default_adapter_registry
    from .sources.base import SourceContext
    from .target_registry import TargetRegistry

    database_path = Path(config.cache.database_path).expanduser()
    if not database_path.is_absolute():
        database_path = Path(config.output_dir) / database_path
    database = TargetRegistry(database_path)
    context = SourceContext(config, database, CACHE_ROOT)
    registry = default_adapter_registry()
    providers = []
    for name in config.followup_surveys:
        spec = config.followup_specs[name]
        adapter = registry.create("followup_survey", spec)
        if verbose:
            print(f"[surveys] Importing configured local source: {name}", flush=True)
        adapter.import_observations(context)
        providers.append(str(spec.options.get("provider_name", name)).upper())

    requested = targets.rename(columns={"target_id": "Target"})
    epochs = database.observation_epochs(requested, providers=providers)
    photometry = database.photometry(requested, sources=providers)
    if not photometry.empty:
        photometry["target_id"] = photometry["Target"]
        photometry["magnitude_err"] = photometry["magnitude_error"]
        photometry["survey"] = photometry["provider"]
        photometry["provider"] = photometry["provider"].astype(str).str.lower()
    if verbose:
        print(f"[surveys] Local survey epochs: {len(epochs):,}; photometry points: {len(photometry):,}", flush=True)
    return epochs, photometry


def _plot_layers(providers, config):
    """Select plot layers from the active TOMs and local surveys."""
    provider_layers = {
        "mop": "mop_photometry", "opm": "opm_photometry", "bhtom": "bhtom_photometry",
    }
    survey_layers = {
        name: "hsh" if config.followup_specs[name].options.get("provider_name", name).upper() == "HSH" else "js"
        for name in config.followup_surveys
    } if config else {}
    if not config:
        return tuple(dict.fromkeys([
            *(provider_layers[name] for name in providers if name in provider_layers),
            "ogle_photometry",
        ]))

    layers = []
    for name in config.products.monitoring_layers:
        if name == "target_provider_photometry":
            layers.extend(provider_layers[item] for item in providers if item in provider_layers)
        elif name == "followup_surveys":
            layers.extend(survey_layers.values())
        elif name == "reference_epochs":
            layers.append("release_epochs")
        else:
            layers.append(name)
    return tuple(dict.fromkeys(layers))


if __name__ == "__main__":
    raise SystemExit(main())
