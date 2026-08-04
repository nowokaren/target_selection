"""Generación de reportes gráficos individuales de targets."""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.visualization import ImageNormalize, AsinhStretch
from astropy.visualization.wcsaxes import SphericalCircle
from lsst.daf.butler import EmptyQueryResultError

from photometry import prepare_lightcurve_data, select_lightcurve_filters


def plot_target(target, *, butler, tap_service, data_release, calexps=None, photometry=None, photometry_loader=None, bands="ugrizy", search_radius=11/60, zoom_arcsec=20, circle_arcsec=3):
    name, ra, dec = ((target["Target"], target["RA_deg"], target["Dec_deg"])
                     if hasattr(target, "index") else target)
    coord = SkyCoord(ra*u.deg, dec*u.deg)

    # El pipeline entrega estas filas; la consulta queda sólo como fallback.
    if calexps is None:
        query = f"""SELECT {data_release.visit_select("vd")}
                    FROM {data_release.tap_visit_table} AS vd
                    WHERE CONTAINS(POINT('ICRS',vd.{data_release.tap_ra},vd.{data_release.tap_dec}),
                    CIRCLE('ICRS',{ra},{dec},{search_radius}))=1"""
        job = tap_service.submit_job(query); job.run(); job.wait(phases=["COMPLETED","ERROR"])
        if job.phase == "ERROR": job.raise_if_error()
        calexps = job.fetch_result().to_table().to_pandas()

    # Una única consulta Butler devuelve todas las bandas que cubren el punto.
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

    fig = plt.figure(figsize=(26,15))
    gs = fig.add_gridspec(5,len(bands),height_ratios=[3,3,1.15,2.1,2.2],hspace=.62,wspace=.55)

    for j, band in enumerate(bands):
        result = coadds.get(band)
        if result is None:
            for row in range(4):
                ax = fig.add_subplot(gs[row,j]); ax.axis("off")
                if row == 0: ax.text(.5,.5,f"Sin coadd {band}",ha="center")
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

        d = calexps[calexps.band == band].sort_values("expMidptMJD")
        ax = fig.add_subplot(gs[2,j])
        ax.scatter(d.expMidptMJD,d.seeing,s=12)
        if d.seeing.notna().any(): ax.axhline(d.seeing.median(),ls="--",lw=1,label=f"med={d.seeing.median():.2f}")
        ax.set(xlabel="MJD",ylabel="seeing",title="MJD vs seeing"); ax.grid(alpha=.3)
        ax.tick_params(labelsize=8); ax.legend(fontsize=7,frameon=False)

        n = d[["visitId","detector"]].drop_duplicates().shape[0]
        desc = d[["seeing","magLim"]].describe().round(2)
        cells = [[idx,*values] for idx,values in zip(desc.index,desc.values)]
        ax = fig.add_subplot(gs[3,j]); ax.axis("off")
        tab = ax.table(cellText=cells,colLabels=["","seeing","magLim"],cellLoc="center",
                       colLoc="center",loc="upper center",colWidths=[.20,.29,.29])
        tab.auto_set_font_size(False); tab.set_fontsize(8); tab.scale(1,1.05)
        for cell in tab.get_celld().values(): cell.PAD=.025
        ax.set_title(f"N visitId + detector = {n}",fontsize=9,pad=1)

    ax_lc = fig.add_subplot(gs[4, :])
    if photometry is None:
        photometry = photometry_loader(name) if photometry_loader is not None else pd.DataFrame()
    selected_filters = select_lightcurve_filters(photometry)
    if not selected_filters:
        message = photometry.attrs.get("error", "Sin fotometría disponible")
        ax_lc.text(.5, .5, message, ha="center", va="center", transform=ax_lc.transAxes)
        ax_lc.set_axis_off()
    else:
        for filter_name in selected_filters:
            data = prepare_lightcurve_data(photometry, filter_name)
            if data.empty:
                continue
            dates = mdates.date2num(data["Timestamp"].to_numpy(dtype="datetime64[us]"))
            magnitudes = data["Magnitude"].to_numpy(dtype=float)
            errors = data["Error"].to_numpy(dtype=float) if "Error" in data else None
            valid = np.isfinite(dates) & np.isfinite(magnitudes)
            if errors is not None:
                errors = np.where(np.isfinite(errors[valid]) & (errors[valid] >= 0), errors[valid], np.nan)
            ax_lc.errorbar(dates[valid], magnitudes[valid], yerr=errors, fmt=".", ms=3, alpha=.75, label=f"{filter_name} (N={valid.sum()})")
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
        if release_dates.size:
            y_min, y_max = ax_lc.get_ylim()
            ax_lc.vlines(
                release_dates, y_min, y_max, color="tab:purple", alpha=.18, linewidth=.7,
                label=f"Épocas {data_release.name} (N={release_dates.size})", zorder=0,
            )
            ax_lc.set_ylim(y_min, y_max)

        locator = mdates.AutoDateLocator(minticks=4, maxticks=10)
        ax_lc.xaxis.set_major_locator(locator)
        ax_lc.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        ax_lc.invert_yaxis()
        ax_lc.set(xlabel="Fecha", ylabel="Magnitud", title=f"Fotometría MOP + épocas {data_release.name}")
        ax_lc.grid(alpha=.25)
        ax_lc.legend(frameon=False, ncol=min(3, len(selected_filters) + bool(release_dates.size)))

    priority = any(str(target.get(k, "")).strip().lower() not in {"","0","0.0","false","nan","none"}
                   for k in ("priority","tap_priority","tap_priority_longte") if hasattr(target, "get"))
    flag = " [PRIORITY]" if priority else ""
    meta_cols = [c for c in ("mag_now","Min airmass","n_visible_nights","coverage_n_visits") if c in target.index]
    mop_cols = [c for c in target.index if str(c).startswith("mop_") and c not in {"mop_link", "mop_parameters_error"}]
    preferred = ("t_e", "te", "u_0", "u0", "t_0", "t0", "rho", "pi_e", "magnitude", "baseline", "parameters_status")
    mop_cols.sort(key=lambda c: (not any(key in str(c).lower() for key in preferred), str(c)))

    label_names = {
        "mag_now": "Magnitud actual", "Min airmass": "Airmass mínimo",
        "n_visible_nights": "Noches visibles", "coverage_n_visits": "Visitas Rubin",
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
    panel_lines = ["DATOS DEL TARGET", "", f"RA: {ra:.5f}°", f"Dec: {dec:.5f}°", f"Filas cobertura: {len(calexps)}"]
    if priority:
        panel_lines.extend(["", "PRIORITY"])
    if valid_cols:
        panel_lines.extend(["", "PARÁMETROS", ""] + [panel_line(c) for c in valid_cols])

    fig.suptitle(f"{name}{flag}", y=.985, fontsize=14,
                 color="crimson" if priority else "black",
                 fontweight="bold" if priority else "normal")
    fig.text(.825, .91, "\n".join(panel_lines), ha="left", va="top", fontsize=9.5,
             linespacing=1.45, family="monospace",
             bbox=dict(boxstyle="round,pad=.7", facecolor="whitesmoke", edgecolor="0.75"))
    fig.subplots_adjust(top=.94,bottom=.055,left=.04,right=.79)
    return fig
