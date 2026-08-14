import pytest

from target_region import classify_target_region


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("OGLE-2025-BLG-0451", "BLG"),
        ("OGLE-2024-GD-0006", "GD"),
        ("OGLE-2026-DG-0001", "GD"),
        ("OGLE-2019-LMC-0123", "LMC"),
        ("OGLE-2018-SMC-0001", "SMC"),
        ("M83-2024-001", "XGAL"),
        ("Gaia24bsi", "UNK"),
    ],
)
def test_classify_target_region_from_standard_designations(target, expected):
    assert classify_target_region(target) == expected
