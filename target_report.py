"""Generate graphical reports for individual targets."""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.visualization import ImageNormalize, AsinhStretch
from astropy.visualization.wcsaxes import SphericalCircle
from lsst.daf.butler import EmptyQueryResultError

from mop_photometry import prepare_lightcurve_data, select_lightcurve_filters
from release_photometry import prepare_release_lightcurve_data


def plot_target(target, *, butler, tap_service, data_release, calexps=None, photometry=None, photometry_loader=None, release_photometry=None, bands="ugrizy", search_radius=11/60, zoom_arcsec=20, circle_arcsec=3):
    name, ra, dec = ((target["Target"], target["RA_deg"], target["Dec_deg"])
                     if hasattr(target, "index") else target)
    coord = SkyCoord(ra*u.deg, dec*u.deg)

    # The pipeline supplies these rows; the query is only a fallback.
    if calexps is None:
        query = f"""SELECT {data_release.visit_select("vd")}
                    FROM {data_release.tap_visit_table} AS vd
                    WHERE CONTAINS(POINT('ICRS',vd.{data_release.tap_ra},vd.{data_release.tap_dec}),
                    CIRCLE('ICRS',{ra},{dec},{search_radius}))=1"""
        job = tap_service.submit_job(query); job.run(); job.wait(phases=["COMPLETED","ERROR"])
        if job.phase == "ERROR": job.raise_if_error()
        calexps = job.fetch_result().to_table().to_pandas()

    # One Butler query returns every band covering the position.
    refs = []
    for dataset_type in data_release.coadd_dataset_types:
        try:
            refs = list(butler.query_datasets(
                dataset_type,
                where=data_release.coadd_spatial_where,
                bind={"ra": ra, "dec": dec},
            ))
        except (EmptyQueryResultError, LookupError):
            refs = []
        if refs:
            break
    coadds = {}
    for ref in refs:
        band = str(ref.dataId["band"])
        if band not in bands or band in coadds:
            continue
        coadd = butler.get(ref)
        x, y = coadd.fits_wcs.world_to_pixel(coord)
        ny, nx = coadd.image.array.shape
        if 0 <= x < nx and 0 <= y < ny:
            coadds[band] = (coadd, x, y)
    if not coadds:
        return None

    fig = plt.figure(figsize=(26,16))
    gs = fig.add_gridspec(4, len(bands), height_ratios=[3, 3, 2.25, 3.8], hspace=.66, wspace=.55)

    for j, band in enumerate(bands):
        result = coadds.get(band)
        if result is None:
            for row in range(3):
                ax = fig.add_subplot(gs[row,j]); ax.axis("off")
                if row == 0: ax.text(.5,.5,f"No coadd {band}",ha="center")
            continue
        coadd, x, y = result

        arr, wcs = np.asarray(coadd.image.array,float), coadd.fits_wcs
        finite = arr[np.isfinite(arr)]; vmin,vmax = np.percentile(finite,[5,99.8])
        norm = ImageNormalize(vmin=vmin,vmax=vmax,stretch=AsinhStretch(.08),clip=True)
        pixscale = np.mean([s.to_value(u.arcsec) for s in wcs.proj_plane_pixel_scales()])
        zoom = zoom_arcsec/pixscale

        for row,lim,title in [(0,None,f"{band}: coadd"),(1,zoom,f"zoom ±{zoom_arcsec}″")]:
            ax = fig.add_subplot(gs[row,j],projection=wcs)
            im = ax.imshow(arr,origin="lower",cmap="gray",norm=norm)
            ax.add_patch(SphericalCircle(coord,circle_arcsec*u.arcsec,transform=ax.get_transform("icrs"),
                                         edgecolor="red",facecolor="none",lw=1.5))
            if lim: ax.set(xlim=(x-lim,x+lim),ylim=(y-lim,y+lim))
            ax.set_title(title,fontsize=10,pad=5)
            ax.coords[0].set_axislabel("RA [deg]",minpad=1.2); ax.coords[1].set_axislabel("Dec [deg]",minpad=.5)
            ax.coords[0].set_major_formatter("d.ddd"); ax.coords[1].set_major_formatter("d.ddd")
            ax.coords[0].set_ticks_position("b")
            ax.coords[0].set_ticklabel_position("b")
            ax.coords[0].set_axislabel_position("b")
            ax.coords[0].set_ticks(direction="out")
            ax.coords[0].set_ticklabel(size=7, rotation=30, pad=10)
            ax.coords[1].set_ticklabel(size=7)
            if row == 0:
                cb = fig.colorbar(im,ax=ax,fraction=.04,pad=.075)
                cb.ax.tick_params(labelsize=7); cb.set_label(str(coadd.unit),fontsize=8)

        detail_grid = gs[2, j].subgridspec(1, 2, width_ratios=[1.05, .95], wspace=.32)
        d = calexps[calexps.band == band].sort_values("expMidptMJD")
        ax = fig.add_subplot(detail_grid[0, 0])
        ax.scatter(d.expMidptMJD, d.seeing, s=7)
        if d.seeing.notna().any():
            ax.axhline(d.seeing.median(), ls="--", lw=.8, label=f"med={d.seeing.median():.2f}")
        ax.set(xlabel="MJD", ylabel="seeing", title="Seeing")
        ax.grid(alpha=.3); ax.tick_params(labelsize=6)
        if d.seeing.notna().any():
            ax.legend(fontsize=5.5, frameon=False, handlelength=1.1)

        n = d[["visitId", "detector"]].drop_duplicates().shape[0]
        desc = d[["seeing", "magLim"]].describe().round(2)
        cells = [[idx, *values] for idx, values in zip(desc.index, desc.values)]
        ax = fig.add_subplot(detail_grid[0, 1]); ax.axis("off")
        tab = ax.table(cellText=cells, colLabels=["", "seeing", "magLim"], cellLoc="center",
                       colLoc="center", loc="center", colWidths=[.30, .35, .35])
        tab.auto_set_font_size(False); tab.set_fontsize(5.6); tab.scale(1, .72)
        for cell in tab.get_celld().values(): cell.PAD = .015
        ax.set_title(f"N visitId + detector = {n}", fontsize=6.5, pad=1)

    ax_lc = fig.add_subplot(gs[3, :])
    if photometry is None:
        photometry = photometry_loader(name) if photometry_loader is not None else pd.DataFrame()
    release_photometry = (
        release_photometry if release_photometry is not None else pd.DataFrame()
    )
    selected_filters = select_lightcurve_filters(photometry)
    lightcurve_start = pd.Timestamp("2024-01-01")
    pre_2024_counts: dict[str, int] = {}
    release_bands = []
    if not release_photometry.empty and "band" in release_photometry:
        release_bands = sorted(
            str(band) for band in release_photometry["band"].dropna().unique()
        )

    has_mop_data = False
    for filter_name in selected_filters:
        data = prepare_lightcurve_data(photometry, filter_name)
        if data.empty:
            continue
        before_start = data["Timestamp"] < lightcurve_start
        if before_start.any():
            pre_2024_counts[str(filter_name)] = int(before_start.sum())
        data = data.loc[~before_start]
        if data.empty:
            continue
        dates = mdates.date2num(data["Timestamp"].to_numpy(dtype="datetime64[us]"))
        magnitudes = data["Magnitude"].to_numpy(dtype=float)
        errors = data["Error"].to_numpy(dtype=float) if "Error" in data else None
        valid = np.isfinite(dates) & np.isfinite(magnitudes)
        if not valid.any():
            continue
        if errors is not None:
            errors = np.where(
                np.isfinite(errors[valid]) & (errors[valid] >= 0), errors[valid], np.nan,
            )
        ax_lc.errorbar(
            dates[valid], magnitudes[valid], yerr=errors, fmt=".", ms=3,
            alpha=.75, label=f"MOP {filter_name} (N={valid.sum()})", zorder=2,
        )
        has_mop_data = True

    has_release_data = False
    release_cmap = plt.get_cmap("tab10")
    for index, band in enumerate(release_bands):
        data = prepare_release_lightcurve_data(release_photometry, band)
        if data.empty:
            continue
        dates = mdates.date2num(data["Timestamp"].to_numpy(dtype="datetime64[us]"))
        magnitudes = data["Magnitude"].to_numpy(dtype=float)
        errors = data["Error"].to_numpy(dtype=float)
        valid = np.isfinite(dates) & np.isfinite(magnitudes)
        if not valid.any():
            continue
        errors = np.where(
            np.isfinite(errors[valid]) & (errors[valid] >= 0), errors[valid], np.nan,
        )
        ax_lc.errorbar(
            dates[valid], magnitudes[valid], yerr=errors, fmt="s", ms=3.2,
            mfc=release_cmap(index % 10), mec="black", mew=.35,
            color=release_cmap(index % 10), alpha=.95,
            label=(
                f"{data_release.name} {band} "
                f"{'coadd forced' if (release_photometry.get('measurement_method', pd.Series(dtype=str)).astype(str) == 'coadd_forced').any() else 'release photometry'} "
                f"(N={valid.sum()})"
            ), zorder=3,
        )
        has_release_data = True

    release_dates = np.array([])
    if calexps is not None and not calexps.empty and "expMidptMJD" in calexps:
        release_mjd = (
            pd.to_numeric(calexps["expMidptMJD"], errors="coerce")
            .dropna().round(5).drop_duplicates().sort_values()
        )
        if not release_mjd.empty:
            release_datetimes = pd.to_datetime(
                release_mjd.to_numpy(dtype=float), unit="D",
                origin=pd.Timestamp("1858-11-17"), errors="coerce",
            )
            release_datetimes = release_datetimes[~pd.isna(release_datetimes)]
            release_dates = mdates.date2num(release_datetimes.to_numpy(dtype="datetime64[us]"))
    if has_mop_data and release_dates.size:
        y_min, y_max = ax_lc.get_ylim()
        ax_lc.vlines(
            release_dates, y_min, y_max, color="tab:purple", alpha=.18, linewidth=.7,
            label=f"{data_release.name} epochs (N={release_dates.size})", zorder=0,
        )
        ax_lc.set_ylim(y_min, y_max)

    if not has_mop_data and not has_release_data:
        message = photometry.attrs.get("error", "No MOP or release photometry available")
        ax_lc.text(.5, .5, message, ha="center", va="center", transform=ax_lc.transAxes)
        ax_lc.set_axis_off()
    else:
        locator = mdates.AutoDateLocator(minticks=4, maxticks=10)
        ax_lc.xaxis.set_major_locator(locator)
        ax_lc.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        ax_lc.invert_yaxis()
        if has_mop_data and has_release_data:
            title = f"MOP + {data_release.name} release photometry"
        elif has_release_data:
            title = f"{data_release.name} release photometry"
        else:
            title = f"MOP photometry + {data_release.name} epochs"
        ax_lc.set(xlabel="Date", ylabel="Magnitude", title=title)
        start_number = mdates.date2num(lightcurve_start.to_datetime64())
        _, current_right = ax_lc.get_xlim()
        ax_lc.set_xlim(start_number, max(current_right, start_number + 30))
        if has_release_data:
            valid_release_points = int(pd.to_numeric(
                release_photometry.get("magnitude"), errors="coerce",
            ).notna().sum())
            ax_lc.text(
                .01, .02, f"{data_release.name} valid photometry points: {valid_release_points}",
                transform=ax_lc.transAxes, ha="left", va="bottom", fontsize=8,
                bbox={"facecolor": "white", "edgecolor": "0.65", "alpha": .88, "pad": 2},
            )
        if pre_2024_counts:
            earlier = ", ".join(f"{name}: {count}" for name, count in pre_2024_counts.items())
            ax_lc.text(
                .99, .02, f"MOP points before 2024 (not shown): {earlier}",
                transform=ax_lc.transAxes, ha="right", va="bottom", fontsize=8,
                bbox={"facecolor": "white", "edgecolor": "0.65", "alpha": .88, "pad": 2},
            )
        ax_lc.grid(alpha=.25)
        ax_lc.legend(frameon=False, ncol=min(3, len(selected_filters) + len(release_bands) + bool(release_dates.size)))

    priority = any(str(target.get(k, "")).strip().lower() not in {"","0","0.0","false","nan","none"}
                   for k in ("priority","tap_priority","tap_priority_longte") if hasattr(target, "get"))
    flag = " [PRIORITY]" if priority else ""
    meta_cols = [c for c in ("mag_now","Min airmass","n_visible_nights","coverage_n_visits") if c in target.index]
    mop_cols = [c for c in target.index if str(c).startswith("mop_") and c not in {"mop_link", "mop_parameters_error"}]
    preferred = ("t_e", "te", "u_0", "u0", "t_0", "t0", "rho", "pi_e", "magnitude", "baseline", "parameters_status")
    mop_cols.sort(key=lambda c: (not any(key in str(c).lower() for key in preferred), str(c)))

    label_names = {
        "mag_now": "Current magnitude", "Min airmass": "Minimum airmass",
        "n_visible_nights": "Visible nights", "coverage_n_visits": "Rubin visits",
    }
    def panel_line(column):
        raw_label = str(column).removeprefix("mop_")
        label = label_names.get(column, raw_label)
        value = target[column]
        if isinstance(value, (float, np.floating)):
            value = f"{value:.4g}"
        unit = " days" if raw_label.casefold() in {"t_e", "te"} and "day" not in str(value).casefold() else ""
        return f"{label}: {value}{unit}"

    valid_cols = [c for c in meta_cols + mop_cols if str(target[c]).strip().lower() not in {"nan", "none", ""}]
    panel_lines = ["TARGET DATA", "", f"RA: {ra:.5f}°", f"Dec: {dec:.5f}°", f"Coverage rows: {len(calexps)}"]
    if priority:
        panel_lines.extend(["", "PRIORITY"])
    if valid_cols:
        panel_lines.extend(["", "PARAMETERS", ""] + [panel_line(c) for c in valid_cols])

    fig.suptitle(f"{name}{flag}", y=.985, fontsize=14,
                 color="crimson" if priority else "black",
                 fontweight="bold" if priority else "normal")
    fig.text(.825, .91, "\n".join(panel_lines), ha="left", va="top", fontsize=9.5,
             linespacing=1.45, family="monospace",
             bbox=dict(boxstyle="round,pad=.7", facecolor="whitesmoke", edgecolor="0.75"))
    fig.subplots_adjust(top=.94,bottom=.055,left=.04,right=.79)
    return fig
