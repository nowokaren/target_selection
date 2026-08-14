"""Generate deterministic synthetic images used by the public documentation."""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ASSETS = Path(__file__).parent / "assets"
RNG = np.random.default_rng(42)


def save_summary() -> None:
    rows = [
        ["OGLE-2026-BLG-0001", "BLG", "15.2", "4.8", "128", "2.1", "0.3", "31.4"],
        ["OGLE-2025-GD-0002", "GD", "16.8", "3.7", "65", "0.8", "0.0", "54.2"],
        ["Gaia26abcd", "BLG", "17.4", "5.1", "42", "0.0", "0.2", "18.6"],
        ["ZTF26abcde", "UNK", "18.1", "2.9", "19", "0.4", "0.0", "92.5"],
    ]
    columns = ["Target", "Region", "mag\nnow", "Visible\n[h]", "MOP peak\n[N]", "HSH peak\n[h]", "DP2 peak\n[h]", "t_E\n[days]"]
    figure, axis = plt.subplots(figsize=(11, 3.2))
    axis.axis("off")
    axis.set_title("Observing selection summary — example", fontsize=14, fontweight="bold", pad=12)
    table = axis.table(cellText=rows, colLabels=columns, cellLoc="center", loc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1.0, 1.7)
    for (row, _), cell in table.get_celld().items():
        if row == 0:
            cell.set_facecolor("#c9dfef")
            cell.set_text_props(weight="bold")
        elif row % 2 == 0:
            cell.set_facecolor("#f6f6f6")
    figure.savefig(ASSETS / "observing_selection_summary_example.png", dpi=160, bbox_inches="tight")
    plt.close(figure)


def save_visibility() -> None:
    hours = np.linspace(18, 30, 241)
    names = ["OGLE-2026-BLG-0001", "OGLE-2025-GD-0002", "Gaia26abcd"]
    phases = [20.5, 22.0, 23.2]
    figure, (curves, strip) = plt.subplots(
        2, 1, figsize=(9.5, 5.8), sharex=True,
        gridspec_kw={"height_ratios": [2.3, 1], "hspace": 0.15},
    )
    for name, phase in zip(names, phases, strict=True):
        altitude = np.maximum(0, 72 - 4.2 * (hours - phase) ** 2)
        curves.plot(hours, altitude, label=name, lw=1.8)
    curves.axhline(40, color="#334155", ls="--", lw=1, label="Selection altitude")
    curves.set(ylim=(0, 90), ylabel="Altitude [deg]", title="Nightly visibility — example")
    curves.legend(ncol=2, fontsize=8, frameon=False, loc="upper center")
    curves.grid(alpha=.25)
    cmap = plt.get_cmap("Blues")
    for index, (name, phase) in enumerate(zip(names, phases, strict=True)):
        altitude = np.maximum(0, 72 - 4.2 * (hours - phase) ** 2)
        for left, right, value in zip(hours[:-1], hours[1:], altitude[:-1], strict=True):
            strip.broken_barh([(left, right - left)], (index - .38, .76), facecolors=cmap(max(.30, value / 90)))
        strip.text(30.08, index, f"{[15.2, 16.8, 17.4][index]:.1f}", va="center", fontsize=8)
    strip.set(yticks=range(len(names)), yticklabels=names, ylim=(-.5, len(names) - .5), xlabel="Local time [h]")
    strip.grid(axis="x", alpha=.25)
    figure.savefig(ASSETS / "visibility_plot_example.png", dpi=160, bbox_inches="tight")
    plt.close(figure)


def save_sky_map() -> None:
    longitude = RNG.normal(0, 18, 36)
    latitude = RNG.normal(0, 5, 36)
    magnitudes = RNG.uniform(14.5, 19.0, 36)
    visits = RNG.integers(1, 25, 36)
    figure, axis = plt.subplots(figsize=(8.8, 4.8))
    scatter = axis.scatter(longitude, latitude, c=magnitudes, s=20 + 5 * visits, cmap="GnBu_r", edgecolor="#1e3a5f", linewidth=.35)
    axis.axhspan(-8, 8, color="#dbeafe", zorder=-1)
    axis.scatter(0, 0, marker="+", color="#b45309", s=80, lw=1.5)
    axis.set(title="Locally selected targets — sky map example", xlabel="Galactic longitude offset [deg]", ylabel="Galactic latitude [deg]")
    axis.grid(alpha=.2)
    colorbar = figure.colorbar(scatter, ax=axis, pad=.02)
    colorbar.set_label("MOP mag_now")
    figure.savefig(ASSETS / "sky_map_example.png", dpi=160, bbox_inches="tight")
    plt.close(figure)


def save_lightcurves() -> None:
    dates = pd.date_range("2026-01-01", periods=90, freq="3D")
    time = np.linspace(-2.5, 2.5, len(dates))
    magnitude = 17.8 - 1.4 * np.exp(-time**2 / .35) + RNG.normal(0, .035, len(dates))
    figure, (main, zoom) = plt.subplots(1, 2, figsize=(11, 3.6), gridspec_kw={"width_ratios": [2.3, 1]})
    for axis, limited in ((main, False), (zoom, True)):
        axis.errorbar(dates, magnitude, yerr=.04, fmt=".", ms=4, color="#2563eb", label="MOP OGLE_I")
        for epoch in dates[55:65:2]:
            axis.axvline(epoch, color="#6b7280", alpha=.18, lw=5, zorder=0)
        axis.invert_yaxis()
        axis.grid(alpha=.22)
        axis.set_ylabel("Magnitude")
        axis.tick_params(axis="x", rotation=25, labelsize=8)
        if limited:
            axis.set_xlim(dates[32], dates[62])
            axis.set_title("MOP zoom: $t_0 \\pm 2t_E$", fontsize=9)
        else:
            axis.set_title("OGLE-2026-BLG-0001 — MOP visible", fontsize=9)
            axis.legend(fontsize=8, frameon=False)
    figure.suptitle("Light curves and temporal coverage — example", fontsize=13, fontweight="bold")
    figure.tight_layout()
    figure.savefig(ASSETS / "lightcurves_example.png", dpi=160, bbox_inches="tight")
    plt.close(figure)


def save_target_report() -> None:
    figure = plt.figure(figsize=(10.5, 6.3))
    grid = figure.add_gridspec(2, 4, height_ratios=(1.35, 1), hspace=.45, wspace=.35)
    for index, band in enumerate(("g", "r", "i")):
        image = RNG.normal(0, .16, (100, 100))
        y, x = np.indices(image.shape)
        image += 2.5 * np.exp(-((x - 50) ** 2 + (y - 48) ** 2) / 70)
        axis = figure.add_subplot(grid[0, index])
        axis.imshow(image, cmap="gray", origin="lower")
        axis.set(title=f"{band}: coadd", xticks=[], yticks=[])
        axis.add_patch(plt.Circle((50, 48), 8, fill=False, color="red", lw=1.2))
    parameters = figure.add_subplot(grid[0, 3])
    parameters.axis("off")
    parameters.text(0, 1, "TARGET DATA\n\nRA: 269.54096°\nDec: -19.97800°\n\nPARAMETERS\n\nt_E: 31.4 d\nt_0: 2026-06-08\nu_0: 0.2\nmag_now: 15.2", va="top", family="monospace", fontsize=8)
    seeing = figure.add_subplot(grid[1, 0])
    seeing.scatter([61000, 61002, 61004], [1.1, .9, 1.0], s=18)
    seeing.set(title="Seeing", xlabel="MJD", ylabel="arcsec")
    seeing.grid(alpha=.25)
    table = figure.add_subplot(grid[1, 1])
    table.axis("off")
    table.table(cellText=[["N visits", "12"], ["median seeing", "1.0"], ["mag. limit", "23.8"]], cellLoc="center", loc="center")
    lightcurve = figure.add_subplot(grid[1, 2:])
    time = np.linspace(0, 100, 75)
    lightcurve.errorbar(time, 17.5 - 1.1 * np.exp(-((time - 55) / 11) ** 2), yerr=.04, fmt=".", ms=3)
    lightcurve.invert_yaxis()
    lightcurve.set(title="MOP photometry + DP2 epochs", xlabel="Date", ylabel="Magnitude")
    lightcurve.grid(alpha=.25)
    figure.suptitle("Individual target report — example", fontsize=14, fontweight="bold")
    figure.savefig(ASSETS / "target_report_example.png", dpi=160, bbox_inches="tight")
    plt.close(figure)


if __name__ == "__main__":
    ASSETS.mkdir(parents=True, exist_ok=True)
    save_summary()
    save_visibility()
    save_sky_map()
    save_lightcurves()
    save_target_report()
