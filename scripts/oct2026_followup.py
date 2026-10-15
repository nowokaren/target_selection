#!/usr/bin/env python
"""Rebuild the 7–15 October 2026 CASLEO light-curve evidence bundle.

Uses the project's MOP, OGLE, OPM and BHTOM adapters. Rubin DP2 is read from
the existing, matched per-target caches because this host has no RSP Butler.
Observation markers are acquisition dates from the CASLEO notes, not photometry.
"""

from __future__ import annotations

from pathlib import Path
import argparse
import json
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import numpy as np
import pandas as pd

from target_selection.sources.ogle import OGLEAdapter
from target_selection.sources.opm import OPMAdapter
from target_selection.sources.bhtom import BHTOMAdapter
from target_selection.sources.mop import create_client
from target_selection.cache_paths import OUTPUT_ROOT, RUBIN_PHOTOMETRY_CACHE


ROOT = OUTPUT_ROOT / "oct2026_followup"
TARGETS = [
    "OGLE-2026-BLG-0888", "OGLE-2026-BLG-0363",
    "OGLE-2026-BLG-0128", "OGLE-2026-BLG-0419",
    "OGLE-2025-BLG-1480", "OGLE-2024-BLG-0674",
    "OGLE-2024-BLG-0605", "OGLE-2024-DG-0016",
    "OGLE-2025-BLG-0451", "OGLE-2024-BLG-0669",
    "OGLE-2024-BLG-0592", "OGLE-2025-BLG-0397",
    "OGLE-2025-BLG-0675", "OGLE-2025-BLG-0715",
    "OGLE-2025-BLG-0674", "OGLE-2024-BLG-0675",
    "OGLE-2025-BLG-0901", "OGLE-2025-BLG-1457",
    "OGLE-2025-DG-0010", "OGLE-2025-BLG-0303",
    "OGLE-2024-GD-0006", "Gaia24bsj", "Gaia24bsi",
]
OPM_IDS = {"OGLE-2026-BLG-0888": 20454, "OGLE-2026-BLG-0363": 4128,
           "OGLE-2026-BLG-0419": 4253}
HSH_DATES = {
    "OGLE-2026-BLG-0888": ["2026-09-09", "2026-09-13", "2026-09-15", "2026-09-16"],
    "OGLE-2025-BLG-1480": ["2026-08-10", "2026-09-06", "2026-09-07", "2026-09-09"],
    "OGLE-2024-BLG-0674": ["2026-08-16", "2026-09-05", "2026-09-16"],
    "OGLE-2024-BLG-0605": ["2026-09-16"],
    "OGLE-2024-DG-0016": ["2026-08-10", "2026-09-06"],
    "OGLE-2025-BLG-0451": ["2026-08-16", "2026-08-18", "2026-09-07"],
    "OGLE-2024-BLG-0592": ["2026-08-10", "2026-08-12", "2026-08-16", "2026-09-08"],
    "OGLE-2025-BLG-0397": ["2026-08-10", "2026-09-06", "2026-09-07", "2026-09-13", "2026-09-15"],
    "OGLE-2025-BLG-0675": ["2026-09-09", "2026-09-16"],
}
JS_EXTRA_DATES = {
    "OGLE-2025-DG-0010": ["2026-09-20", "2026-09-22"],
    "OGLE-2025-BLG-0901": ["2026-09-22"],
    "OGLE-2025-BLG-1457": ["2026-09-22"],
    "Gaia24bsj": ["2026-09-22"],
    "Gaia24bsi": ["2026-09-20", "2026-09-22"],
}
MANUAL_COORDS = {
    "OGLE-2024-DG-0016": (280.666125, -6.1070556),
    "OGLE-2025-DG-0010": (283.6008333, -0.8884167),
    "Gaia24bsj": (80.06375, -70.0729722),
    "Gaia24bsi": (82.5967917, -70.0395611),
}


def safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def read_or_fetch(path: Path, fetch, *, refresh: bool = False):
    empty_marker = path.with_suffix(".empty")
    if refresh:
        path.unlink(missing_ok=True)
        empty_marker.unlink(missing_ok=True)
    if path.exists():
        return pd.read_csv(path)
    if empty_marker.exists():
        return pd.DataFrame()
    try:
        frame = fetch()
        if frame is not None and not frame.empty:
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(path, index=False)
        else:
            empty_marker.parent.mkdir(parents=True, exist_ok=True)
            empty_marker.touch()
        return frame
    except Exception as exc:
        print(f"{path.name}: {type(exc).__name__}: {str(exc)[:150]}", flush=True)
        empty_marker.parent.mkdir(parents=True, exist_ok=True)
        empty_marker.touch()
        return pd.DataFrame()


def load_lsst(name: str) -> pd.DataFrame:
    for base in [RUBIN_PHOTOMETRY_CACHE]:
        path = base / f"{name}.csv"
        if not path.exists():
            continue
        frame = pd.read_csv(path, dtype={"diaObjectId": "string"})
        if "measurement_status" in frame:
            frame = frame.loc[frame["measurement_status"].eq("measured")].copy()
        if "measurement_flag" in frame:
            flags = frame["measurement_flag"].map(lambda value: value is True or str(value).lower() == "true")
            frame = frame.loc[~flags].copy()
        frame = frame.loc[pd.to_numeric(frame.get("magnitude"), errors="coerce").notna()].copy()
        if frame.empty:
            continue
        sep = pd.to_numeric(frame.get("dia_match_sep_arcsec"), errors="coerce")
        if sep.notna().any() and sep.min() > 1.0:
            print(f"LSST {name}: rejected match at {sep.min():.2f} arcsec", flush=True)
            continue
        return frame
    return pd.DataFrame()


def normalized(name: str, source: str, frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    f = frame.copy()
    if source == "mop":
        mjd = pd.to_datetime(f["Timestamp"], utc=True, errors="coerce").map(lambda t: t.to_julian_date()-2400000.5 if pd.notna(t) else np.nan)
        band, mag, err = f["Filter"], f["Magnitude"], f["Error"]
        dataset = f["Telescope"].fillna(f.get("Source", ""))
    elif source == "ogle":
        mjd = pd.to_numeric(f["epoch_hjd"], errors="coerce") - 2400000.5
        band, mag, err = "I", f["magnitude"], f["magnitude_err"]
        dataset = "OGLE EWS"
    elif source == "opm":
        mjd = pd.to_numeric(f["epoch_hjd"], errors="coerce") - 2400000.5
        band, mag, err = f["band"], f["magnitude"], f["magnitude_err"]
        dataset = f["dataset_id"]
    elif source == "bhtom":
        mjd = pd.to_numeric(f["epoch_mjd"], errors="coerce")
        band, mag, err = f["band"], f["magnitude"], f["magnitude_err"]
        dataset = f["facility"]
    else:
        mjd = pd.to_numeric(f["expMidptMJD"], errors="coerce")
        band, mag, err = f["band"], f["magnitude"], f["magnitude_err"]
        dataset = f.get("dataset_type", "DP2")
    n = len(f)
    return pd.DataFrame({
        "target_id": [name]*n, "source": [source]*n,
        "mjd": mjd, "band": band if isinstance(band, pd.Series) else [band]*n,
        "magnitude": pd.to_numeric(mag, errors="coerce"),
        "magnitude_err": pd.to_numeric(err, errors="coerce"),
        "dataset": dataset if isinstance(dataset, pd.Series) else [dataset]*n,
    }).dropna(subset=["mjd", "magnitude"])


def main(*, refresh: bool = False) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    ogle = OGLEAdapter(timeout=25)
    mop = create_client()
    opm = OPMAdapter(timeout=25)
    bhtom = BHTOMAdapter(timeout=25)
    try:
        opm.login()
        opm_ready = True
    except Exception as exc:
        print(f"OPM login: {type(exc).__name__}: {str(exc)[:100]}")
        opm_ready = False
    try:
        bhtom.login()
        bhtom_ready = True
    except Exception as exc:
        print(f"BHTOM login: {type(exc).__name__}: {str(exc)[:100]}")
        bhtom_ready = False

    all_rows, inventory, positions, rubin_records = [], [], [], []
    for name in TARGETS:
        print("FETCH", name, flush=True)
        sources = {}
        sources["mop"] = read_or_fetch(ROOT / "raw" / "mop" / f"{name}.csv", lambda n=name: mop.photometry(n), refresh=refresh)
        if name.startswith("OGLE-"):
            one = pd.DataFrame({"target_id": [name]})
            sources["ogle"] = read_or_fetch(ROOT / "raw" / "ogle" / f"{name}.csv", lambda d=one: ogle.photometry(d, errors="raise"), refresh=refresh)
        if name in OPM_IDS and opm_ready:
            selected = pd.DataFrame({"target_id": [name], "opm_target_page_id": [OPM_IDS[name]]})
            sources["opm"] = read_or_fetch(ROOT / "raw" / "opm" / f"{name}.csv", lambda d=selected: opm.photometry(d, errors="raise", verbose=False), refresh=refresh)
        if bhtom_ready:
            found = read_or_fetch(ROOT / "raw" / "bhtom_targets" / f"{name}.csv", lambda n=name: bhtom.load_targets(query={"name": n}, classification=None, max_pages=1), refresh=refresh)
            if not found.empty and name in found["target_id"].astype(str).values:
                sources["bhtom"] = read_or_fetch(ROOT / "raw" / "bhtom" / f"{name}.csv", lambda n=name: bhtom.photometry(pd.DataFrame({"target_id": [n]}), errors="raise"), refresh=refresh)
        lsst = load_lsst(name)
        if not lsst.empty:
            for dataset_type, group in lsst.groupby("dataset_type", dropna=False):
                rubin_records.append({
                    "target_id": name, "dataset_type": dataset_type, "n_rows": len(group),
                    "diaObjectId": str(group["diaObjectId"].dropna().iloc[0]) if "diaObjectId" in group and group["diaObjectId"].notna().any() else "",
                    "match_sep_arcsec": pd.to_numeric(group.get("dia_match_sep_arcsec"), errors="coerce").min(),
                    "interpretation": "visit light curve" if dataset_type in {"ForcedSourceOnDiaObject", "DiaSource"} else "static coadd; excluded from light curve",
                })
        sources["lsst_dp2"] = lsst.loc[lsst["dataset_type"].isin(["ForcedSourceOnDiaObject", "DiaSource"])].copy() if not lsst.empty else lsst
        coords = None
        from target_selection.sources.ogle import _event_paths
        import requests
        from bs4 import BeautifulSoup
        try:
            if not name.startswith("OGLE-"):
                raise ValueError("No OGLE event page")
            txt = BeautifulSoup(requests.get(_event_paths(name)["event_url"], timeout=15).text, "html.parser").get_text(" ", strip=True)
            match = re.search(r"RA \(J2000\.0\)\s+(\d\d:\d\d:\d\d\.\d+)\s+Dec \(J2000\.0\)\s+([+-]\d\d:\d\d:\d\d\.\d+)", txt)
            if match:
                from astropy.coordinates import SkyCoord
                import astropy.units as u
                c = SkyCoord(match.group(1), match.group(2), unit=(u.hourangle,u.deg))
                coords = (c.ra.deg, c.dec.deg)
        except Exception:
            pass
        coords = coords or MANUAL_COORDS.get(name)
        if coords:
            positions.append({"target_id": name, "ra_deg": coords[0], "dec_deg": coords[1]})
        for source, frame in sources.items():
            n = normalized(name, source, frame)
            if n.empty:
                inventory.append({"target_id": name, "source": source, "n_points": 0, "last_utc": ""})
                continue
            all_rows.append(n)
            last = pd.to_datetime(n["mjd"].max(), unit="D", origin=pd.Timestamp("1858-11-17"), utc=True)
            inventory.append({"target_id": name, "source": source, "n_points": len(n), "last_utc": last.isoformat()})
    phot = pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame()
    phot.to_csv(ROOT / "all_photometry.csv", index=False)
    pd.DataFrame(inventory).to_csv(ROOT / "source_inventory.csv", index=False)
    pd.DataFrame(positions).to_csv(ROOT / "positions.csv", index=False)
    pd.DataFrame(rubin_records).to_csv(ROOT / "rubin_matches.csv", index=False)
    js = pd.read_csv("data/js_data/js_inventory.csv")
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
    archival["date"] = pd.to_datetime(
        archival["night"].astype(str), format="%Y%m%d", errors="coerce"
    )
    archival_dates = {
        name: sorted(set(group["date"].dropna()))
        for name, group in archival.groupby("objname")
    }
    with PdfPages(ROOT / "lightcurves.pdf") as pdf:
        for name in TARGETS:
            d = phot.loc[phot["target_id"].eq(name)].copy()
            # OGLE and OPM points already embedded in MOP are downloaded but
            # omitted from the plots to prevent the same exposure appearing twice.
            has_ogle = (d["source"] == "ogle").any()
            if has_ogle:
                d = d.loc[~((d["source"] == "mop") & d["dataset"].astype(str).str.contains("OGLE", case=False))]
                d = d.loc[~((d["source"] == "opm") & d["dataset"].astype(str).str.contains("OGLE", case=False))]
            if (d["source"] == "mop").any():
                d = d.loc[~((d["source"] == "opm") & d["dataset"].astype(str).str.contains("OGLE|ZTF", case=False))]
            fig, axes = plt.subplots(2, 1, figsize=(11.7, 8.3), constrained_layout=True)
            colors = plt.get_cmap("tab20").colors
            for ax in axes:
                for j, ((source, dataset, band), group) in enumerate(d.groupby(["source", "dataset", "band"], dropna=False)):
                    dates = pd.to_datetime(group["mjd"], unit="D", origin=pd.Timestamp("1858-11-17"))
                    label = f"{source}: {dataset} {band} ({len(group)})"
                    errors = pd.to_numeric(group["magnitude_err"], errors="coerce").where(lambda x: x.ge(0)).clip(upper=0.4).fillna(0)
                    ax.errorbar(dates, group["magnitude"], yerr=errors, fmt=".", ms=2.5, lw=.3, capsize=0, color=colors[j % len(colors)], label=label, alpha=.8)
                ax.invert_yaxis()
                ax.grid(alpha=.2)
                ax.set_ylabel("Magnitud (cada banda por separado)")
            axes[0].set_title(name + " — fotometría disponible; HSH/JS = noches de adquisición")
            axes[0].legend(loc="best", fontsize=6, ncol=2)
            for date in archival_dates.get(name, []):
                axes[0].axvline(date, color="gray", lw=.35, alpha=.25)
            axes[1].set_xlim(pd.Timestamp("2026-06-01"), pd.Timestamp("2026-10-16"))
            axes[1].set_title("Temporada 2026 (HJD de OGLE/OPM aproximado a UTC)", fontsize=10)
            h_dates = HSH_DATES.get(name, [])
            j_dates = sorted(set(js.loc[js["target"].eq(name) & js["product_status"].eq("observed"), "date"].dropna().astype(str).tolist() + JS_EXTRA_DATES.get(name, [])))
            for date in h_dates:
                axes[1].axvline(pd.Timestamp(date), color="black", lw=.8, alpha=.5)
            for date in j_dates:
                axes[1].axvline(pd.Timestamp(date), color="purple", lw=.8, ls="--", alpha=.5)
            for date in archival_dates.get(name, []):
                if date.year == 2026:
                    axes[1].axvline(date, color="gray", lw=.35, alpha=.25)
            old_hsh = archival_dates.get(name, [])
            old_summary = (
                f"{len(old_hsh)} noches {min(old_hsh):%Y-%m-%d}–{max(old_hsh):%Y-%m-%d}"
                if old_hsh else "ninguna en inventario"
            )
            axes[1].text(.01, .02, f"HSH 2026: {', '.join(h_dates) or 'sin adquisición en bitácora'}\nHSH histórico en campo: {old_summary} (gris, panel superior)\nJS 2026: {', '.join(j_dates) or 'sin adquisición en inventario'}", transform=axes[1].transAxes, fontsize=7, va="bottom", bbox={"facecolor":"white","alpha":.75,"edgecolor":"none"})
            pdf.savefig(fig)
            fig.savefig(ROOT / "plots" / f"{safe(name)}.png", dpi=150)
            plt.close(fig)
    print("Saved", ROOT)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Fetch current provider data instead of cached CSVs")
    main(refresh=parser.parse_args().refresh)
