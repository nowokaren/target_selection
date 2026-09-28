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
    cache_dir="outputs/cache/ogle_photometry",
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

The public OPM ranking page exposes target name, broker links, latest magnitude
and band, coordinates, several probability/variability scores and an update
time. The general target list and individual targets require login.

The implemented public feature is:

```python
ranking = data_source.opm.ranking()
```

The authenticated session is prepared but the private target/observation
parser remains intentionally pending inspection with an authorized account:

```python
session = data_source.opm.login()
```

Credentials are read from `TARGET_SELECTION_OPM_USERNAME` and
`TARGET_SELECTION_OPM_PASSWORD`, or passed explicitly at runtime. They are
never stored in the repository.

Source: [Galactic Science OPM](https://lensingtom.zah.uni-heidelberg.de/).
