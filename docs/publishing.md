# Publish on Read the Docs

The repository already contains the files required by Read the Docs:

- `.readthedocs.yaml` selects Python and the MkDocs builder;
- `mkdocs.yml` defines navigation and the Read the Docs theme; and
- `docs/requirements.txt` pins the documentation dependency range.

## Preview locally

```bash
python -m pip install -r docs/requirements.txt
mkdocs serve
```

Open the local URL printed by MkDocs. To run the same strict validation used by
the hosted build:

```bash
mkdocs build --strict
```

## Create the hosted project

After these files are committed and pushed:

1. Sign in to Read the Docs with the GitHub account that can access the
   repository.
2. Choose **Add project** and import `nowokaren/target_selection`.
3. Confirm that the configuration file is `.readthedocs.yaml`.
4. Build the default branch. Activate the refactor branch first if the
   documentation has not yet been merged.

Once connected, Read the Docs installs the documentation requirements and
rebuilds the site from repository updates. The hosted project setup is the only
step that requires the repository owner's Read the Docs account.
