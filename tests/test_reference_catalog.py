import json

import numpy as np
import pandas as pd
from astropy.table import Table

from target_selection.reference_catalog import (
    ReferenceCatalogConfig,
    load_target_catalog,
    pivot_visit_summary,
    query_visit_summary_by_band,
    query_direct_coadd_coverage,
    rank_cutout_candidates,
    run_reference_catalog,
    sample_coadd_properties,
)


class FakeResult:
    def __init__(self, frame):
        self.frame = frame

    def to_table(self):
        return Table.from_pandas(self.frame)


class FakeJob:
    phase = "PENDING"

    def __init__(self, frame):
        self.frame = frame

    def run(self):
        self.phase = "EXECUTING"

    def wait(self, phases, timeout=None):
        self.phase = "COMPLETED"

    def fetch_result(self):
        return FakeResult(self.frame)


class FakeTapService:
    def __init__(self, frame):
        self.frame = frame
        self.calls = []

    def submit_job(self, query, uploads=None):
        self.calls.append((query, uploads))
        return FakeJob(self.frame)


class FakeMap:
    def __init__(self, values, valid=None):
        self.values = np.asarray(values, dtype=float)
        self.valid = np.ones(len(self.values), dtype=bool) if valid is None else np.asarray(valid, dtype=bool)

    def get_values_pos(self, ra, dec, lonlat=True, valid_mask=False):
        size = len(np.asarray(ra))
        values = self.values[:size]
        valid = self.valid[:size]
        return valid.copy() if valid_mask else values.copy()


class FakeButler:
    def get(self, dataset_type, **kwargs):
        band = kwargs["band"]
        index = {"g": 0, "r": 1}.get(band, 0)
        if "maglim" in dataset_type:
            return FakeMap([25.0 + index, 24.0 + index], [True, False])
        if "psf_size" in dataset_type:
            return FakeMap([2.0, 3.0], [True, False])
        if "exposure_time" in dataset_type:
            return FakeMap([300.0, -1.0], [True, False])
        if "sky_noise" in dataset_type:
            return FakeMap([4.0, 5.0], [True, False])
        raise LookupError(dataset_type)



def _detector_frame():
    return pd.DataFrame(
        {
            "band": ["g"], "visit_id": [101], "detector": [42],
            "seeing": [0.8], "mag_lim": [24.5], "exp_time": [30.0],
            "eff_time": [28.0], "sky_noise": [4.0], "zero_point": [31.0],
            "n_psf_star": [100.0], "mjd": [60000.0],
            "detector_ra": [10.0], "detector_dec": [-10.0],
            "llcra": [9.9], "llcdec": [-10.1],
            "ulcra": [9.9], "ulcdec": [-9.9],
            "urcra": [10.1], "urcdec": [-9.9],
            "lrcra": [10.1], "lrcdec": [-10.1],
        }
    )

def _targets():
    return pd.DataFrame(
        {
            "target_id": ["a", "b"],
            "ra_deg": [10.0, 20.0],
            "dec_deg": [-10.0, -20.0],
        }
    )


def test_load_target_catalog_extracts_preferred_grade(tmp_path):
    path = tmp_path / "targets.csv"
    pd.DataFrame(
        {
            "id": ["a", "b", "c"],
            "longitude": [10.0, 20.0, 30.0],
            "latitude": [-10.0, -20.0, -30.0],
            "metadata": [
                "vetting_grade=A,Grade=B",
                "vetting_grade=nan,Grade=nan § B",
                "vetting_grade=nan,Grade=ABorC",
            ],
        }
    ).to_csv(path, index=False)
    config = ReferenceCatalogConfig(
        input_path=str(path),
        target_id_column="id",
        ra_column="longitude",
        dec_column="latitude",
        extra_info_column="metadata",
    )
    result = load_target_catalog(config)
    assert result["selection_grade"].tolist()[:2] == ["A", None]
    assert pd.isna(result.loc[2, "selection_grade"])
    assert result["selection_grade_source"].tolist() == ["extra_info_first", "unavailable", "unavailable"]


def test_visit_query_uses_spatial_tiles_and_exact_detector_polygons(tmp_path):
    service = FakeTapService(_detector_frame())
    result = query_visit_summary_by_band(
        _targets(), tap_service=service, tile_nside=64, max_workers=1,
        cache_dir=tmp_path, verbose=False,
    )
    assert len(service.calls) == 2
    assert all(uploads is None for _, uploads in service.calls)
    assert "CIRCLE('ICRS'" in service.calls[0][0]
    assert "vd.llcra" in service.calls[0][0]
    assert result[["target_id", "band", "n_images"]].to_dict("records") == [
        {"target_id": "a", "band": "g", "n_images": 1}
    ]


def test_visit_summary_is_pivoted_to_counts_per_band():
    data = pd.DataFrame(
        {
            "target_id": ["a", "a"],
            "band": ["g", "r"],
            "n_images": [3, 4],
            "visit_maglim_mean": [24.0, 24.5],
        }
    )
    result = pivot_visit_summary(data, ["a", "b"], ["g", "r"])
    assert result.loc[0, ["n_images_g", "n_images_r", "n_images_total"]].tolist() == [3, 4, 7]
    assert result.loc[1, "n_images_total"] == 0



class DirectRef:
    def __init__(self, band):
        self.dataId = {"band": band}


class DirectFakeButler:
    def query_datasets(self, dataset_type, *, where=None, bind=None):
        if bind["ra"] < 15:
            return [DirectRef("g"), DirectRef("r")]
        return []


def test_direct_coadd_query_is_independent_of_property_maps(tmp_path):
    result = query_direct_coadd_coverage(
        _targets(), butler=DirectFakeButler(), data_release="DP2",
        cache_path=tmp_path / "direct.csv", reuse_cache=False, verbose=False,
    )
    assert result["has_coadd"].tolist() == [True, False]
    assert result.loc[0, "coadd_bands"] == "g,r"
    assert result.loc[1, "coadd_n_bands"] == 0


def test_coadd_maps_are_sampled_vectorially_and_psf_size_becomes_fwhm():
    result = sample_coadd_properties(
        _targets(),
        butler=FakeButler(),
        bands=("g", "r"),
        property_maps=("maglim", "psf_size", "exposure_time", "sky_noise"),
        pixel_scale_arcsec=0.2,
        partial_reads=False,
        verbose=False,
    )
    assert result.loc[0, "coadd_n_bands"] == 2
    assert bool(result.loc[0, "has_coadd"])
    assert not bool(result.loc[1, "has_coadd"])
    assert round(result.loc[0, "coadd_psf_fwhm_median_arcsec"], 3) == 0.942


def test_cutout_ranking_keeps_grade_order_before_band_count():
    catalog = pd.DataFrame(
        {
            "target_id": ["a-poor", "a-good", "b-best", "c"],
            "selection_grade": ["A", "A", "B", "C"],
            "has_coadd": [True, True, True, True],
            "coadd_n_bands": [2, 6, 6, 6],
            "coadd_maglim_median": [23.0, 26.0, 27.0, 28.0],
            "coadd_psf_fwhm_median_arcsec": [1.5, 0.8, 0.6, 0.5],
        }
    )
    result = rank_cutout_candidates(catalog, bands="ugrizy", max_cutouts=3)
    ranked = result.loc[result["cutout_selected"]].sort_values("cutout_priority_rank")
    assert ranked["target_id"].tolist() == ["a-good", "a-poor", "b-best"]
    assert not bool(result.loc[result["target_id"].eq("c"), "cutout_eligible"].iloc[0])


def test_full_catalog_run_writes_dataset_plan_and_manifest(tmp_path):
    input_path = tmp_path / "input.csv"
    pd.DataFrame(
        {
            "object_id": ["a", "b"],
            "ra": [10.0, 20.0],
            "dec": [-10.0, -20.0],
            "extra_info": ["vetting_grade=A,Grade=A", "vetting_grade=B,Grade=B"],
        }
    ).to_csv(input_path, index=False)
    service = FakeTapService(_detector_frame())
    config = ReferenceCatalogConfig(
        input_path=str(input_path),
        name="test",
        output_dir=str(tmp_path / "outputs"),
        cache_dir=str(tmp_path / "cache"),
        bands=("g", "r"),
        generate_cutouts=False,
        verbose=False,
    )
    result = run_reference_catalog(config, tap_service=service, butler=FakeButler())
    assert result.catalog_path.exists()
    assert result.cutout_plan_path.exists()
    public_columns = pd.read_csv(result.catalog_path).columns
    assert not any(str(column).startswith("cutout_") for column in public_columns)
    assert json.loads(result.manifest_path.read_text())["summary"]["n_targets"] == 2
