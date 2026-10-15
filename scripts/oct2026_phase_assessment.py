#!/usr/bin/env python
"""Summarize event phases and acquisition coverage for the October 2026 plan.

OGLE model parameters were read from the public EWS event pages on 2026-10-07.
They are preliminary fits; dates of HSH/JS acquisition are not reduced photometry.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from scripts.oct2026_followup import HSH_DATES, JS_EXTRA_DATES, TARGETS
from target_selection.cache_paths import OUTPUT_ROOT


ROOT = OUTPUT_ROOT / "oct2026_followup"
AS_OF = pd.Timestamp("2026-10-07")
# target: (OGLE EWS t0 HJD, tE days, u_min, baseline I)
MODEL = {
    "OGLE-2026-BLG-0888": (2461313.352, 49.041, 0.000, 16.248),
    "OGLE-2026-BLG-0363": (2461216.914, 98.944, 1.076, 12.065),
    "OGLE-2026-BLG-0128": (2461233.583, 199.695, 0.252, 17.543),
    "OGLE-2026-BLG-0419": (2461303.438, 77.300, 0.000, 13.960),
    "OGLE-2025-BLG-1480": (2460952.576, 42.520, None, 14.420),
    "OGLE-2024-BLG-0674": (2460486.900, 24.665, None, 14.843),
    "OGLE-2024-BLG-0605": (2460539.625, 48.811, None, 15.940),
    "OGLE-2024-DG-0016": (2460610.742, 133.577, None, 13.172),
    "OGLE-2025-BLG-0451": (2460837.033, 38.546, None, 14.046),
    "OGLE-2024-BLG-0669": (2460523.075, 74.681, None, 15.810),
    "OGLE-2024-BLG-0592": (2460509.716, 50.526, None, 14.635),
    "OGLE-2025-BLG-0397": (2460906.756, 149.538, None, 13.577),
    "OGLE-2025-BLG-0675": (2460836.115, 43.507, None, 15.071),
    "OGLE-2025-BLG-0715": (2461098.399, 161.656, 0.840, 13.791),
    "OGLE-2025-BLG-0674": (2460869.548, 78.970, None, 19.779),
    "OGLE-2024-BLG-0675": (2460430.049, 30.219, None, 18.589),
    "OGLE-2025-BLG-0901": (2460897.770, 74.432, None, 18.209),
    "OGLE-2025-BLG-1457": (2460943.378, 21.509, None, 16.244),
    "OGLE-2025-DG-0010": (2460853.345, 96.516, None, 18.732),
    "OGLE-2025-BLG-0303": (2460822.926, 85.911, None, 17.352),
    "OGLE-2024-GD-0006": (2460519.979, 114.285, None, 16.326),
}


def phase(date: pd.Timestamp, t0: float, te: float) -> float:
    return (date.to_julian_date() - t0) / te


def lens_magnitude(phase_te: float, umin: float | None, baseline: float) -> float:
    if umin is None or umin <= 0:
        return np.nan
    u = np.hypot(phase_te, umin)
    amplification = (u*u + 2) / (u * np.sqrt(u*u + 4))
    return baseline - 2.5*np.log10(amplification)


def main() -> None:
    phot = pd.read_csv(ROOT / "all_photometry.csv")
    archival = pd.read_csv(
        "data/hsh_data/image_collection_astro.csv",
        usecols=["objname", "night", "imagetyp", "astromet", "obj_in"],
        low_memory=False,
    )
    archival = archival.loc[
        archival["imagetyp"].eq("object")
        & archival["astromet"].eq("yes")
        & archival["obj_in"].eq(True)
    ].copy()
    archival["date"] = pd.to_datetime(archival["night"].astype(str), format="%Y%m%d", errors="coerce")
    js = pd.read_csv("data/js_data/js_inventory.csv")
    rows = []
    for name in TARGETS:
        if name not in MODEL:
            continue
        t0, te, umin, baseline = MODEL[name]
        h_archive = sorted(set(archival.loc[archival.objname.eq(name), "date"].dropna()))
        h_log = list(map(pd.Timestamp, HSH_DATES.get(name, [])))
        j_log = sorted(set(map(pd.Timestamp,
            js.loc[js.target.eq(name) & js.product_status.eq("observed"), "date"].dropna().astype(str).tolist()
            + JS_EXTRA_DATES.get(name, []))))
        g = phot.loc[phot.target_id.eq(name) & phot.source.eq("ogle") & phot.band.eq("I")].sort_values("mjd")
        latest_ogle = g.iloc[-1] if not g.empty else None
        mop = phot.loc[phot.target_id.eq(name) & phot.source.eq("mop") & phot.dataset.eq("ATLAS")].sort_values("mjd")
        last_atlas = mop.iloc[-1] if not mop.empty else None
        p_now = phase(AS_OF, t0, te)
        rows.append({
            "target_id": name,
            "t0_ogle_utc": pd.to_datetime(t0-2440587.5, unit="D", origin="unix").date().isoformat(),
            "te_days": te,
            "phase_oct07_te": round(p_now, 2),
            "baseline_I_model": baseline,
            "predicted_I_oct07_model_only": round(lens_magnitude(p_now, umin, baseline), 3),
            "latest_OGLE_I_date": pd.to_datetime(latest_ogle.mjd, unit="D", origin="1858-11-17").date().isoformat() if latest_ogle is not None else "",
            "latest_OGLE_I": round(latest_ogle.magnitude, 3) if latest_ogle is not None else np.nan,
            "latest_ATLAS_date": pd.to_datetime(last_atlas.mjd, unit="D", origin="1858-11-17").date().isoformat() if last_atlas is not None else "",
            "latest_ATLAS_band": last_atlas.band if last_atlas is not None else "",
            "latest_ATLAS_mag": round(last_atlas.magnitude, 3) if last_atlas is not None else np.nan,
            "HSH_archival_in_frame_nights": len(h_archive),
            "HSH_archival_peak_nights_abs_phase_lt1": sum(abs(phase(d, t0, te)) < 1 for d in h_archive),
            "HSH_logged_2026_dates": ";".join(d.date().isoformat() for d in h_log),
            "HSH_logged_2026_phase_te": ";".join(f"{phase(d,t0,te):+.2f}" for d in h_log),
            "JS_logged_dates": ";".join(d.date().isoformat() for d in j_log),
            "JS_logged_phase_te": ";".join(f"{phase(d,t0,te):+.2f}" for d in j_log),
        })
    pd.DataFrame(rows).to_csv(ROOT / "phase_assessment.csv", index=False)


if __name__ == "__main__":
    main()
