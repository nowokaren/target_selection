#!/usr/bin/env python
"""Build the standard seasonal observing products from the terminal.

Example:
    python scripts/seasonal_observing_plan.py --start-date 2026-10-07 \
        --end-date 2026-10-15 --providers mop,opm --magnitude-max 17
"""

from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd

from target_selection.planning import (
    add_latest_magnitude,
    add_ogle_event_parameters,
    apply_magnitude_limit,
    collect_photometry,
    load_candidates,
    save_planning_pdf,
    save_priority_products,
    save_target_sky_map,
    save_visibility_products,
)
from target_selection.prioritization import SCHEDULABLE_CATEGORIES, evaluate_priority


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a seasonal target-selection report.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--providers", default=None, help="Comma-separated TOM names; default: all registered TOMs.")
    parser.add_argument("--observatory", default="El Leoncito")
    parser.add_argument("--output-dir", default="outputs/seasonal_plan")
    parser.add_argument("--magnitude-max", type=float, default=None)
    parser.add_argument("--minimum-altitude-deg", type=float, default=40.0)
    parser.add_argument("--minimum-visible-minutes", type=float, default=90.0)
    parser.add_argument("--with-photometry", action="store_true")
    parser.add_argument("--with-lightcurves", action="store_true")
    parser.add_argument("--with-lsst-cutouts", action="store_true")
    args = parser.parse_args(argv)

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    providers = None if args.providers is None else tuple(item.strip().lower() for item in args.providers.split(",") if item.strip())

    targets = load_candidates(
        providers,
        start_date=args.start_date,
        end_date=args.end_date,
        include_photometry=args.with_photometry,
        cache_dir=output / "cache",
    )
    if targets.empty:
        print("No targets were loaded.")
        return 1
    targets = add_ogle_event_parameters(targets)
    targets.to_csv(output / "targets_loaded.csv", index=False)

    if args.with_photometry:
        observations = collect_photometry(
            targets,
            providers=providers,
            cache_dir=output / "cache" / "photometry",
        )
        observations.to_csv(output / "observations.csv", index=False)
    else:
        observations = pd.DataFrame()

    if args.with_photometry:
        targets = add_latest_magnitude(targets, observations)
        targets.to_csv(output / "targets_loaded.csv", index=False)

    candidates = apply_magnitude_limit(targets, args.magnitude_max)
    candidates.to_csv(output / "targets_after_magnitude.csv", index=False)
    selection, visibility_summary, visibility_plots, visibility_png = save_visibility_products(
        targets,
        output / "visibility",
        start_date=args.start_date,
        end_date=args.end_date,
        observatory=args.observatory,
        minimum_altitude_deg=args.minimum_altitude_deg,
        minimum_observable_minutes=args.minimum_visible_minutes,
    )
    priority = evaluate_priority(
        targets, observations, visibility_summary, as_of=args.start_date,
        output_dir=output / "tables",
    )
    _, priority_png = save_priority_products(
        priority, output / "tables", name="priority_assessment",
        title="Evidence-based priority assessment",
        subtitle="Scientific categories come from the shared evaluator; visibility and magnitude are feasibility evidence.",
        write_csv=False,
    )
    feasible = candidates.merge(
        priority[["target_id", "priority_category", "n_visible_nights", "priority_rank"]],
        on="target_id", how="left",
    )
    selected = feasible.loc[
        feasible["priority_category"].isin(SCHEDULABLE_CATEGORIES)
        & pd.to_numeric(feasible["n_visible_nights"], errors="coerce").fillna(0).gt(0)
    ]
    selected = selected.sort_values("priority_rank").drop(columns=["priority_rank", "n_visible_nights"])
    selected.to_csv(output / "selected_targets.csv", index=False)
    sky_map = save_target_sky_map(selected, output / "sky_map.png", title="Selected targets")
    cutout_images = []
    if args.with_lsst_cutouts and not selected.empty:
        from target_selection import data_source
        lsst_coverage = data_source.lsst.coverage(selected, image_kinds=("individual", "coadd"), properties=("maglim", "psf_size", "exposure_time"))
        lsst_coverage.to_csv(output / "lsst_coverage.csv", index=False)
        data_source.lsst.get_image(lsst_coverage, kind="coadd", output_dir=output / "cutouts" / "png", fits_dir=output / "cutouts" / "fits", bands=tuple("ugrizy"), add_rgb=False)
        cutout_images = sorted((output / "cutouts" / "png").glob("*.png"))
    lightcurve_pngs = []
    if args.with_lightcurves and not selected.empty:
        from target_selection import report
        lightcurve_pngs = report.create_lightcurve_plots(
            selected,
            output_dir=output / "lightcurves",
            observatory_photometry=observations,
            layers=("mop_photometry", "opm_photometry", "ogle_photometry"),
            observation_date=args.start_date,
            overwrite=True,
        )
    save_planning_pdf(
        output / "seasonal_observing_plan.pdf",
        sky_map=sky_map,
        table_images=(priority_png, visibility_png),
        visibility_images=visibility_plots,
        lightcurve_images=lightcurve_pngs,
        cutout_images=cutout_images,
    )
    print(f"Seasonal plan saved to {output}")
    print(f"Targets loaded: {len(targets):,}; selected: {len(selected):,}")
    print(f"PDF: {output / 'seasonal_observing_plan.pdf'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
