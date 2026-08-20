from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli_anything.blender_gui.command import (
    AUTO_UV_DEFAULTS,
    BatchUVRequest,
    build_batch_uv_args,
    batch_output_paths,
    normalize_suffix,
    parse_cli_json,
    suffix_output_path,
    validate_batch_request,
)


def make_source(tmp_path: Path, name: str = "角色 模型.fbx") -> Path:
    source = tmp_path / name
    source.write_bytes(b"not a real fbx; path logic only")
    return source


def test_batch_source_mode_overwrites_all_inputs(tmp_path: Path):
    source_a = make_source(tmp_path, "a.fbx")
    source_b = make_source(tmp_path, "b.fbx")
    request = validate_batch_request(
        [str(source_a), str(source_b)], "autouv", "source", "", None,
        300, 120, None,
    )
    assert isinstance(request, BatchUVRequest)
    assert request.overwrite_source is True
    assert batch_output_paths(request) == (str(source_a.resolve()), str(source_b.resolve()))
    args = build_batch_uv_args(request)
    assert args[:8] == [
        "--json", "fbx", "auto-uv", str(source_a.resolve()),
        str(source_b.resolve()), "--algorithm", "autouv", "--overwrite-source",
    ]
    assert "--output" not in args
    assert "--output-dir" not in args
    assert args[args.index("--topology-prefilter-level") + 1] == "high"
    assert args[args.index("--jobs") + 1] == "2"


def test_batch_request_accepts_parallel_jobs_and_rejects_invalid_value(tmp_path: Path):
    source = make_source(tmp_path, "asset.fbx")
    request = validate_batch_request(
        [str(source)], "autouv", "source", "", None, 10, 10, None, jobs=4,
    )
    assert request.jobs == 4
    args = build_batch_uv_args(request)
    assert args[args.index("--jobs") + 1] == "4"

    with pytest.raises(ValueError, match="并行任务数"):
        validate_batch_request(
            [str(source)], "autouv", "source", "", None, 10, 10, None, jobs=0,
        )


def test_batch_suffix_mode_uses_fixed_algorithm_suffix(tmp_path: Path):
    source = make_source(tmp_path)
    request = validate_batch_request(
        [str(source)], "autouv", "suffix", "", "_autouv", 300, 120, None,
    )
    assert batch_output_paths(request) == (str(tmp_path / "角色 模型_autouv.fbx"),)
    args = build_batch_uv_args(request)
    assert "--suffix" in args
    assert args[args.index("--suffix") + 1] == "_autouv"


def test_batch_output_dir_preserves_names_without_suffix(tmp_path: Path):
    source = make_source(tmp_path, "asset.fbx")
    output_dir = tmp_path / "out"
    request = validate_batch_request(
        [str(source)], "uniform", "path", str(output_dir), None, 300, 120, None,
        angle_degrees=[10, 30], rotate_method="AXIS_ALIGNED_X",
    )
    assert batch_output_paths(request) == (str(output_dir / "asset.fbx"),)
    args = build_batch_uv_args(request)
    assert "--output-dir" in args
    assert str(output_dir.resolve()) in args
    assert "--suffix" not in args
    assert args.count("--angle-deg") == 2
    assert "--topology-prefilter-level" not in args


def test_batch_autouv_can_disable_topology_prefilter(tmp_path: Path):
    source = make_source(tmp_path, "asset.fbx")
    request = validate_batch_request(
        [str(source)], "autouv", "source", "", None, 300, 120, None,
        topology_prefilter=False,
    )
    args = build_batch_uv_args(request)
    assert args[args.index("--topology-prefilter-level") + 1] == "off"


def test_batch_autouv_supports_strict_topology_prefilter(tmp_path: Path):
    source = make_source(tmp_path, "asset.fbx")
    request = validate_batch_request(
        [str(source)], "autouv", "source", "", None, 300, 120, None,
        topology_prefilter_level="medium",
    )
    assert request.topology_prefilter_level == "medium"
    args = build_batch_uv_args(request)
    assert args[args.index("--topology-prefilter-level") + 1] == "medium"


def test_batch_request_can_add_internal_cancel_file(tmp_path: Path):
    source = make_source(tmp_path, "asset.fbx")
    request = validate_batch_request(
        [str(source)], "autouv", "source", "", None, 10, 10, None,
    )
    args = build_batch_uv_args(request, cancel_file=str(tmp_path / "cancel.marker"))
    assert args[args.index("--cancel-file") + 1] == str(tmp_path / "cancel.marker")


def test_batch_autouv_defaults_to_merge_and_normalize(tmp_path: Path):
    source = make_source(tmp_path, "pipeline.fbx")
    request = validate_batch_request(
        [str(source)], "autouv", "source", "", None, 300, 120, None,
    )
    assert request.merge_meshes is True
    assert request.normalize_uv is True
    args = build_batch_uv_args(request)
    assert "--merge-meshes" in args
    assert "--normalize-uv" in args


def test_batch_autouv_can_disable_merge_and_normalize(tmp_path: Path):
    source = make_source(tmp_path, "pipeline.fbx")
    request = validate_batch_request(
        [str(source)], "autouv", "source", "", None, 913, 47, None,
        merge_meshes=False, normalize_uv=False,
    )
    args = build_batch_uv_args(request)
    assert "--no-merge-meshes" in args
    assert "--no-normalize-uv" in args
    assert args[args.index("--timeout") + 1] == "913"
    assert args[args.index("--external-timeout") + 1] == "47"


def test_batch_topology_prefilter_level_conflicts_with_legacy_bool(tmp_path: Path):
    source = make_source(tmp_path, "asset.fbx")
    with pytest.raises(ValueError, match="同时使用"):
        validate_batch_request(
            [str(source)], "autouv", "source", "", None, 300, 120, None,
            topology_prefilter=True,
            topology_prefilter_level="off",
        )


def test_batch_request_deduplicates_paths_and_keeps_order(tmp_path: Path):
    source_a = make_source(tmp_path, "a.fbx")
    source_b = make_source(tmp_path, "b.fbx")
    request = validate_batch_request(
        [str(source_a), str(source_a), str(source_b)],
        "autouv", "suffix", "", "_autouv", 300, 120, None,
    )
    assert request.source_paths == (str(source_a.resolve()), str(source_b.resolve()))


def test_batch_uniform_rejects_autouv_only_parameters(tmp_path: Path):
    source = make_source(tmp_path)
    with pytest.raises(ValueError, match="不能用于"):
        validate_batch_request(
            [str(source)], "autouv", "suffix", "", "_autouv", 300, 120, None,
            angle_degrees=[30],
        )


def test_batch_rejects_empty_inputs_and_missing_output_dir(tmp_path: Path):
    with pytest.raises(ValueError, match="至少导入"):
        validate_batch_request([], "autouv", "source", "", None, 300, 120, None)
    source = make_source(tmp_path)
    with pytest.raises(ValueError, match="输出目录"):
        validate_batch_request([str(source)], "autouv", "path", "", None, 300, 120, None)


def test_batch_validates_common_autouv_defaults(tmp_path: Path):
    source = make_source(tmp_path)
    request = validate_batch_request(
        [str(source)], "autouv", "source", "", None, 300, 120, None,
    )
    assert request.resolution == AUTO_UV_DEFAULTS["resolution"]
    assert request.udims == AUTO_UV_DEFAULTS["udims"]


def test_suffix_helpers_reject_paths_and_empty_values(tmp_path: Path):
    source = make_source(tmp_path)
    with pytest.raises(ValueError, match="后缀"):
        normalize_suffix("")
    with pytest.raises(ValueError, match="路径分隔符"):
        suffix_output_path(source, "subdir/_uv")


def test_parse_cli_json_accepts_object_and_trailing_text():
    value = {"results": [], "validation": {"errors": []}}
    assert parse_cli_json(json.dumps(value)) == value
    assert parse_cli_json("progress\n" + json.dumps(value)) == value


def test_parse_cli_json_rejects_non_object():
    with pytest.raises(ValueError, match="必须是对象"):
        parse_cli_json("[]")
