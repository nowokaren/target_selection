from target_selection.cli import build_parser


def test_catalog_command_accepts_a_dedicated_config():
    args = build_parser().parse_args(
        ["catalog", "--config", "configs/lastberu_dp2.toml"]
    )
    assert args.command == "catalog"
    assert args.config == "configs/lastberu_dp2.toml"
