import pandas as pd
import pytest

from target_selection.catalog_matching import add_match_flag
from target_selection.catalog_products import describe_dp2_products, open_dp2_hats_catalog
from target_selection.quality import apply_coadd_quality_flags


def test_add_match_flag_uses_radius():
    result = add_match_flag(
        pd.DataFrame({"_dist_arcsec": [0.2, 1.0, 1.1, None]}),
        radius_arcsec=1.0,
        flag_column="object_match",
    )
    assert result["object_match"].tolist() == [True, True, False, False]


def test_dp2_products_include_spatial_and_associated_products():
    products = describe_dp2_products()
    assert {"object", "dia_object", "dia_source"}.issubset(products)
    assert products["dia_object_forced_source"]["parent"] == "dia_object"


def test_associated_product_is_not_opened_as_spatial_catalog():
    with pytest.raises(ValueError, match="associated product"):
        open_dp2_hats_catalog("dia_object_forced_source")


def test_coadd_quality_flags_are_available_from_generic_module():
    frame = pd.DataFrame({
        "target_id": ["T1"],
        "coadd_bands": ["r"],
        "coadd_maglim_r": [24.0],
        "coadd_psf_size_pixel_r": [2.0],
        "coadd_exposure_time_s_r": [30.0],
        "coadd_sky_noise_njy_r": [1.0],
    })
    result = apply_coadd_quality_flags(frame, bands=("r",), manifest=[])
    assert result.loc[0, "has_coadd_r"]
    assert not result.loc[0, "has_valid_coadd_r"]
