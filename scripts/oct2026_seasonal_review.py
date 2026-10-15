#!/usr/bin/env python
"""Plot the open candidates from the October seasonal plan.

Inputs are the provider CSV snapshots in outputs/oct2026_followup/seasonal_candidates.
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import pandas as pd

from target_selection.cache_paths import OUTPUT_ROOT


ROOT = OUTPUT_ROOT / "oct2026_followup" / "seasonal_candidates"
PLOTS = ROOT / "plots"
PLOTS.mkdir(exist_ok=True)
OPM = pd.read_csv(ROOT / "opm_ztf_photometry.csv")
ALL_OPM = pd.read_csv(ROOT / "all_opm_photometry.csv")


def dates_from_hjd(values):
    return pd.to_datetime(values - 2440587.5, unit="D", origin="unix")


def plot(name, data, *, baseline=None, subtitle=""):
    fig, axes = plt.subplots(2, 1, figsize=(11.7, 8.3), constrained_layout=True)
    for ax in axes:
        for band, group in data.groupby("band"):
            ax.errorbar(group.date, group.magnitude, yerr=group.magnitude_err.clip(0, .4).fillna(0),
                        fmt=".", markersize=2.5, linewidth=.4, alpha=.65, label=str(band))
        if baseline is not None:
            ax.axhline(baseline, color="black", linestyle="--", linewidth=.8,
                       label=f"OGLE I de base = {baseline:.3f}")
        ax.invert_yaxis()
        ax.grid(alpha=.2)
        ax.set_ylabel("Magnitud (comparar solo dentro de cada banda)")
    axes[0].set_title(f"{name} — {subtitle}")
    axes[0].legend(fontsize=8, ncol=3)
    axes[1].set_xlim(pd.Timestamp("2026-04-01"), pd.Timestamp("2026-10-16"))
    axes[1].set_xlabel("Fecha UTC")
    fig.savefig(PLOTS / f"{name}.png", dpi=150)
    return fig


def main():
    with PdfPages(ROOT / "candidate_lightcurves.pdf") as pdf:
        name = "OGLE-2026-BLG-0128"
        ogle = pd.read_csv(ROOT / f"{name}_ogle.csv")
        ogle["date"] = dates_from_hjd(ogle.epoch_hjd)
        ogle["band"] = "OGLE I"
        bhtom = pd.read_csv("outputs/notebooks/v2/seasonal_plan/observations.csv")
        bhtom = bhtom.loc[bhtom.target_id.eq(name) & bhtom.provider.eq("bhtom") & bhtom.band.eq("i(GaiaSP)")].copy()
        bhtom["date"] = pd.to_datetime(bhtom.epoch_mjd, unit="D", origin="1858-11-17")
        bhtom["band"] = "BHTOM i"
        bhtom = bhtom.groupby(bhtom.date.dt.floor("D"), as_index=False).agg(
            date=("date", "min"), magnitude=("magnitude", "median"), magnitude_err=("magnitude_err", "median"), band=("band", "first")
        )
        fig = plot(name, pd.concat([ogle, bhtom]), subtitle="OGLE I hasta julio; BHTOM i hasta 29-jul (bandas distintas)")
        pdf.savefig(fig)
        plt.close(fig)

        name = "OGLE-2026-BLG-0419"
        ogle = pd.read_csv(ROOT / f"{name}_ogle.csv")
        ogle["date"] = dates_from_hjd(ogle.epoch_hjd)
        ogle["band"] = "OGLE I"
        ztf = ALL_OPM.loc[ALL_OPM.target_id.eq(name) & ALL_OPM.band.isin(["g", "r"])].copy()
        ztf["date"] = dates_from_hjd(ztf.epoch_hjd)
        ztf["band"] = "ZTF " + ztf["band"]
        fig = plot(name, pd.concat([ogle, ztf]), subtitle="OGLE I hasta julio; ZTF g/r hasta 8-ago")
        pdf.savefig(fig)
        plt.close(fig)

        for name in ["OGLE-2024-BLG-0808", "OGLE-2026-GD-0001"]:
            f = pd.read_csv(ROOT / f"{name}_ogle.csv")
            f = f.rename(columns={"magnitude_err": "magnitude_err"})
            f["date"] = dates_from_hjd(f.epoch_hjd)
            f["band"] = "OGLE I"
            baseline = 16.589 if name.endswith("0808") else 17.181
            fig = plot(name, f, baseline=baseline, subtitle="OGLE EWS; no hay puntos posteriores a la última fecha mostrada")
            pdf.savefig(fig)
            plt.close(fig)
        for name in ["ZTF26abbbguw", "ZTF26aawmspr"]:
            f = OPM.loc[OPM.target_id.eq(name)].copy()
            f["date"] = dates_from_hjd(f.epoch_hjd)
            fig = plot(name, f, subtitle="OPM/ZTF; variación lenta y clasificación LPV según Martín")
            pdf.savefig(fig)
            plt.close(fig)
    print(ROOT / "candidate_lightcurves.pdf")


if __name__ == "__main__":
    main()
