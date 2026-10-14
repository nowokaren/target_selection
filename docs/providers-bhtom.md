# BHTOM access setup

BHTOM2 uses a username and password to issue an API token at
`/api/token-auth/`. The token is kept only in the Python HTTP session.

## Store credentials locally

Install the optional keyring backend and enter your BHTOM account details in
your own terminal:

```bash
python -m pip install -e '.[credentials]'
target-selection auth set bhtom
target-selection auth test bhtom
target-selection auth test bhtom --remote
```

The first test checks only local credential resolution. The `--remote` test
requests a token from BHTOM and reports success without printing the token.

On a headless machine without a usable keyring, run
`target-selection auth set bhtom --backend netrc`. This stores the account
under `bh-tom2.astrouw.edu.pl` in `~/.netrc` with file mode `0600`.
Alternatively, set both `TARGET_SELECTION_BHTOM_USERNAME` and
`TARGET_SELECTION_BHTOM_PASSWORD` in your shell or job environment. A `.env`
file is not loaded automatically. Do not put credentials in a notebook,
tracked config, issue, or chat message.

From Python, the prepared authentication entry point is:

```python
from target_selection import data_source

session = data_source.bhtom.login()
```

## Read targets and photometry

The read-only adapter follows BHTOM's documented target-list and photometry
endpoints. It has been checked against BHTOM's published server code, local
response fixtures, and live authenticated responses. Pagination, a server-side
classification query, a local magnitude filter, and CSV photometry parsing
have been verified.

```python
from target_selection import data_source

targets = data_source.bhtom.load_targets(
    filters={"magnitude": {"maximum": 19.5}},
    max_pages=2,  # omit to read every page
)
photometry = data_source.bhtom.photometry(targets.head(5))
```

`load_targets()` queries only `Microlensing Event` by default. Use
`load_targets(classification="Star")` for another BHTOM classification or
`load_targets(classification=None)` for every class. Other BHTOM server-side
criteria can be passed through `query`; an explicit `query["classification"]`
overrides the dedicated argument. The cache key includes the effective query,
so changing the classification does not reuse a different class's cache.

The target table has canonical `target_id`, `ra_deg`, `dec_deg`, `provider`,
`category`, and `magnitude_last` fields, plus BHTOM's other published fields.
The photometry table uses `epoch_mjd`, `magnitude`, `magnitude_err`, `facility`,
`band`, `observer`, and `is_usable`. BHTOM supplies MJD; this adapter does not
label it as heliocentric JD. The download endpoint records a download event in
BHTOM's own system.

Source: [BHTOM2 API documentation](https://github.com/BHTOM-Team/bhtom2/blob/master/Documentation/DocumentationAPI.md).
