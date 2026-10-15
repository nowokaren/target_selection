import pandas as pd

from target_selection.planning import apply_magnitude_limit


def test_magnitude_limit_filters_targets_and_keeps_visibility_aligned(tmp_path):
    targets = pd.DataFrame({
        "target_id": ["bright", "faint", "unknown"],
        "magnitude_last": [15.8, 18.1, None],
    })
    visibility = pd.DataFrame({
        "target_id": ["bright", "faint", "unknown"],
        "n_visible_nights": [3, 4, 2],
    })

    selected, selected_visibility = apply_magnitude_limit(
        targets, 16.5, visibility_summary=visibility, output_dir=tmp_path,
    )

    assert selected.target_id.tolist() == ["bright"]
    assert selected_visibility.target_id.tolist() == ["bright"]
    assert (tmp_path / "targets_after_magnitude.csv").exists()


def test_no_magnitude_limit_keeps_targets_with_missing_magnitude():
    targets = pd.DataFrame({"target_id": ["known", "unknown"], "magnitude_last": [15.8, None]})
    result = apply_magnitude_limit(targets, None)
    assert result.target_id.tolist() == ["known", "unknown"]
