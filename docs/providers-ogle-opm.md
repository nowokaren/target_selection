# OGLE and OPM providers

This page records the first verified integrations. It distinguishes public
data from authenticated data and avoids treating a web page as a complete API
until its response format has been inspected.

## OGLE EWS

The OGLE-IV Early Warning System publishes a current event table with event
identifier, field, star number, J2000 coordinates and preliminary point-lens
parameters: `Tmax`, `tau`, `Umin`, `Amax`, `Dmag`, blending and I-band baseline
quantities. Individual event pages link to finding charts and `phot.dat`
files. The EWS photometry is I-band DIA photometry and is described by OGLE as
provisional/not calibrated for final scientific use.

The implemented public features are:

```python
from target_selection import data_source

events = data_source.ogle.load_targets(season=2026)
observations = data_source.ogle.photometry(
    events,
    cache_dir="outputs/cache/ogle/photometry",
)
```

The normalized target fields are `target_id`, `ra_deg`, `dec_deg`, `provider`,
`t0_hjd`, `tE_days` and `u0`. Original OGLE fields and URLs are preserved.
The normalized observation fields include `epoch_hjd`, `band="I"`,
`magnitude`, `magnitude_err`, `seeing_arcsec`, `sky_background` and
`is_usable`.

### Meaning of the EWS microlensing parameters

The EWS table reports a preliminary point-lens model with blending:

| OGLE field | Normalized field | Meaning |
|---|---|---|
| `Tmax` | `t0_hjd` | Fitted time of maximum magnification, HJD. |
| `tau` | `tE_days` | Fitted Einstein timescale, in days. |
| `Umin` | `u0` | Minimum lens-source separation in Einstein-radius units. |
| `Amax` | — | Maximum amplification. |
| `Dmag` | — | Maximum brightening in magnitudes. |
| `fbl` | — | Blending ratio. |
| `Ibl` | — | Total baseline I-band magnitude. |
| `I0` | — | Estimated lens/base I-band magnitude from the model. |

These parameters describe the microlensing model, but they are not final
measurements. OGLE explicitly warns that the fit parameters may change as the
event develops.

Sources: [OGLE EWS](https://ogle.astrouw.edu.pl/ogle4/ews/ews.html) and the
[OGLE 2026 data directory](https://www.astrouw.edu.pl/ogle/ogle4/ews/2026/).

## OPM

The seasonal planner checks both OPM curated candidate lists on every run:
the [ranked microlensing targets](https://lensingtom.zah.uni-heidelberg.de/ranked-microlensing-targets/)
and [new and active targets](https://lensingtom.zah.uni-heidelberg.de/new_and_active_targets).
The first list includes target coordinates; the second is enriched from each
linked target page. List provenance and available page classifications are
retained with the candidate records. The regional target API query remains in place,
and candidates from all three OPM sources are merged before visibility is
evaluated. The curated pages are refreshed each run; the more expensive
overlapping-cone query continues to use the daily query cache.

The seasonal planner queries the OPM Target API with the standard
`cone_search=RA,Dec,radius` filter. It builds overlapping cones around the
zenith track during each configured observing window, retrieves the OPM
targets in that conservative region, then applies the exact local CASLEO
visibility criteria. This may include targets outside the final visibility
cut, which are removed by the local calculation.

The public ranking remains available for inspection:

```python
ranking = data_source.opm.ranking()
```

The target-region query and individual-target photometry use an authenticated
OPM session:

```python
session = data_source.opm.login()
```

Photometry is read only from the `photometry_data` Plotly payload in a target
page. OPM pages also embed classification and observation-planning plots; their
time-series arrays are not photometric measurements.

For persistent, cross-platform setup, install the optional credentials
dependency and run:

```bash
pip install -e '.[credentials]'
target-selection auth set opm
target-selection auth test opm
```

The default backend is the operating-system credential store through
`keyring`. On a headless server without a usable keyring backend, use the
`.netrc` fallback instead:

```bash
target-selection auth set opm --backend netrc
```

Credentials are never stored in the repository or in a notebook.

Source: [Galactic Science OPM](https://lensingtom.zah.uni-heidelberg.de/).
