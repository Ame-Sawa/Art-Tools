"""Pure command and path logic used by the batch-only UV GUI.

Keeping this module independent from Qt makes the safety-critical path and
argument behavior easy to test without requiring a desktop display or Blender.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence


DEFAULT_ANGLE_DEGREES = (10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0, 60.0, 66.0)
ROTATE_METHOD_VALUES = ("AXIS_ALIGNED", "AXIS_ALIGNED_X", "AXIS_ALIGNED_Y")
OUTPUT_MODE_VALUES = ("source", "suffix", "path")
AUTO_UV_DEFAULTS = {
    "resolution": 1024,
    "separate_hard_edges": False,
    "aspect": 1.0,
    "use_normals": False,
    "udims": 1,
    "overlap_identical": False,
    "overlap_mirrored": False,
    "world_scale": False,
    "density": 1024,
    "merge_meshes": True,
    "normalize_uv": True,
}
AUTO_UV_ALGORITHMS = ("autouv", "uniform")
TOPOLOGY_PREFILTER_LEVELS = ("off", "high", "medium")
DEFAULT_PARALLEL_JOBS = 2


@dataclass(frozen=True)
class CliInvocation:
    """Executable and working directory used to launch the existing CLI."""

    program: str
    prefix_args: tuple[str, ...]
    working_directory: str


@dataclass(frozen=True)
class BatchUVRequest:
    """Validated values needed to run one algorithm for multiple FBX files."""

    source_paths: tuple[str, ...]
    algorithm: str
    output_dir: Optional[str]
    suffix: Optional[str]
    overwrite: bool
    overwrite_source: bool
    timeout: int
    external_timeout: int
    jobs: int
    topology_prefilter: bool
    topology_prefilter_level: str
    unwrap_exe: Optional[str]
    resolution: int
    separate_hard_edges: bool
    aspect: float
    use_normals: bool
    udims: int
    overlap_identical: bool
    overlap_mirrored: bool
    world_scale: bool
    density: int
    merge_meshes: bool
    normalize_uv: bool
    angle_degrees: tuple[float, ...]
    rotate_method: Optional[str]


def _absolute_path(value: str | os.PathLike[str]) -> str:
    return os.path.abspath(os.path.expanduser(os.fspath(value).strip().strip('"')))


def validate_source_path(source_path: str | os.PathLike[str]) -> str:
    """Validate and normalize a single FBX input path."""

    normalized = _absolute_path(source_path)
    if not os.path.isfile(normalized):
        raise ValueError(f"FBX 文件不存在：{normalized}")
    if Path(normalized).suffix.lower() != ".fbx":
        raise ValueError("输入文件必须是 .fbx 文件。")
    return normalized


def normalize_suffix(suffix: str | os.PathLike[str]) -> str:
    """Validate a filename suffix used beside the source FBX."""

    normalized = os.fspath(suffix).strip()
    if not normalized:
        raise ValueError("请输入输出后缀。")
    if "/" in normalized or "\\" in normalized:
        raise ValueError("输出后缀不能包含路径分隔符。")
    return normalized


def suffix_output_path(
    source_path: str | os.PathLike[str],
    suffix: str | os.PathLike[str],
) -> str:
    """Return a sibling FBX path with ``suffix`` inserted before the extension."""

    source = Path(_absolute_path(source_path))
    normalized_suffix = normalize_suffix(suffix)
    return str(source.with_name(f"{source.stem}{normalized_suffix}{source.suffix}"))


def normalize_angles(angle_degrees: Sequence[float]) -> tuple[float, ...]:
    """Validate and deduplicate the UI's degree-based candidate angles."""

    values = sorted({float(value) for value in angle_degrees})
    if not values:
        raise ValueError("至少选择一个角度候选。")
    if any(not math.isfinite(value) or value <= 0.0 or value > 180.0 for value in values):
        raise ValueError("角度候选必须大于 0 且不超过 180 度。")
    return tuple(values)


def _format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def normalize_executable_path(value: str | os.PathLike[str] | None) -> Optional[str]:
    """Validate an optional external executable path."""

    raw = os.fspath(value).strip().strip('"') if value else ""
    if not raw:
        return None
    normalized = _absolute_path(raw)
    if not os.path.isfile(normalized):
        raise ValueError(f"AutoUV 程序不存在：{normalized}")
    return normalized


def validate_batch_source_paths(source_paths: Sequence[str]) -> tuple[str, ...]:
    """Validate, normalize, and de-duplicate a multi-file FBX selection."""

    if not source_paths:
        raise ValueError("请至少导入一个 FBX 文件。")
    normalized = []
    seen = set()
    for source_path in source_paths:
        value = validate_source_path(source_path)
        key = os.path.normcase(value)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(value)
    return tuple(normalized)


def _normalize_batch_output_dir(output_dir: str | os.PathLike[str]) -> str:
    raw = os.fspath(output_dir).strip().strip('"')
    if not raw:
        raise ValueError("请选择批量输出目录。")
    normalized = _absolute_path(raw)
    if os.path.isfile(normalized):
        raise ValueError("批量输出目录不能是文件。")
    return normalized


def validate_batch_request(
    source_paths: Sequence[str],
    algorithm: str,
    output_mode: str,
    output_dir: str,
    suffix: Optional[str],
    timeout: int,
    external_timeout: int,
    unwrap_exe: Optional[str],
    topology_prefilter: Optional[bool] = None,
    *,
    resolution: int = AUTO_UV_DEFAULTS["resolution"],
    separate_hard_edges: bool = AUTO_UV_DEFAULTS["separate_hard_edges"],
    aspect: float = AUTO_UV_DEFAULTS["aspect"],
    use_normals: bool = AUTO_UV_DEFAULTS["use_normals"],
    udims: int = AUTO_UV_DEFAULTS["udims"],
    overlap_identical: bool = AUTO_UV_DEFAULTS["overlap_identical"],
    overlap_mirrored: bool = AUTO_UV_DEFAULTS["overlap_mirrored"],
    world_scale: bool = AUTO_UV_DEFAULTS["world_scale"],
    density: int = AUTO_UV_DEFAULTS["density"],
    merge_meshes: bool = AUTO_UV_DEFAULTS["merge_meshes"],
    normalize_uv: bool = AUTO_UV_DEFAULTS["normalize_uv"],
    angle_degrees: Sequence[float] = (),
    rotate_method: Optional[str] = None,
    overwrite: bool = False,
    topology_prefilter_level: Optional[str] = None,
    jobs: int = DEFAULT_PARALLEL_JOBS,
) -> BatchUVRequest:
    """Validate shared algorithm settings and batch output behavior."""

    paths = validate_batch_source_paths(source_paths)
    selected_algorithm = str(algorithm or "").lower()
    if selected_algorithm not in AUTO_UV_ALGORITHMS:
        raise ValueError(f"不支持的 UV 算法：{algorithm}")
    if output_mode not in OUTPUT_MODE_VALUES:
        raise ValueError(f"不支持的输出模式：{output_mode}")
    if topology_prefilter_level is None:
        resolved_prefilter_level = "high" if topology_prefilter is not False else "off"
    else:
        resolved_prefilter_level = str(topology_prefilter_level).lower()
        if resolved_prefilter_level not in TOPOLOGY_PREFILTER_LEVELS:
            raise ValueError("不支持的拓扑筛选等级：" + resolved_prefilter_level)
        if topology_prefilter is not None:
            raise ValueError("拓扑筛选等级不能与旧版拓扑筛选开关同时使用。")
    if output_mode == "source":
        normalized_dir = None
        normalized_suffix = None
        overwrite_source = True
    elif output_mode == "suffix":
        normalized_dir = None
        normalized_suffix = normalize_suffix(suffix or ("_autouv" if selected_algorithm == "autouv" else "_uv"))
        overwrite_source = False
    else:
        normalized_dir = _normalize_batch_output_dir(output_dir)
        normalized_suffix = normalize_suffix(suffix) if suffix else None
        overwrite_source = False

    timeout_value = int(timeout)
    external_timeout_value = int(external_timeout)
    jobs_value = int(jobs)
    if timeout_value < 1 or external_timeout_value < 1:
        raise ValueError("超时时间必须大于 0 秒。")
    if jobs_value < 1:
        raise ValueError("并行任务数必须大于 0。")
    resolution_value = int(resolution)
    udims_value = int(udims)
    density_value = int(density)
    aspect_value = float(aspect)
    if resolution_value < 1 or udims_value < 1 or density_value < 1:
        raise ValueError("AutoUV 分辨率、UDIM 数量和密度必须大于 0。")
    if not math.isfinite(aspect_value) or aspect_value <= 0.0:
        raise ValueError("AutoUV 像素宽高比必须是正数。")

    angles = tuple(float(value) for value in angle_degrees)
    if selected_algorithm == "uniform":
        angles = normalize_angles(angles)
        if not math.isfinite(aspect_value):
            raise ValueError("Uniform UV 参数无效。")
        normalized_exe = None
    else:
        if angles or rotate_method is not None:
            raise ValueError("Uniform UV 参数不能用于 Ministry of Flat AutoUV。")
        normalized_exe = normalize_executable_path(unwrap_exe)
        angles = ()
    if rotate_method not in (None, *ROTATE_METHOD_VALUES):
        raise ValueError(f"不支持的旋转方式：{rotate_method}")

    return BatchUVRequest(
        paths,
        selected_algorithm,
        normalized_dir,
        normalized_suffix,
        bool(overwrite),
        overwrite_source,
        timeout_value,
        external_timeout_value,
        jobs_value,
        resolved_prefilter_level != "off",
        resolved_prefilter_level,
        normalized_exe,
        resolution_value,
        bool(separate_hard_edges),
        aspect_value,
        bool(use_normals),
        udims_value,
        bool(overlap_identical),
        bool(overlap_mirrored),
        bool(world_scale),
        density_value,
        bool(merge_meshes),
        bool(normalize_uv),
        angles,
        rotate_method,
    )


def batch_output_paths(request: BatchUVRequest) -> tuple[str, ...]:
    """Return the planned output path for every batch input."""

    outputs = []
    for source_path in request.source_paths:
        source = Path(source_path)
        if request.overwrite_source:
            outputs.append(str(source))
        elif request.output_dir:
            suffix = request.suffix or ""
            outputs.append(str(Path(request.output_dir) / f"{source.stem}{suffix}{source.suffix}"))
        else:
            outputs.append(suffix_output_path(source, request.suffix or "_uv"))
    return tuple(outputs)


def build_batch_uv_args(
    request: BatchUVRequest,
    *,
    cancel_file: Optional[str] = None,
) -> list[str]:
    """Build the unified multi-file AutoUV CLI invocation."""

    args = ["--json", "fbx", "auto-uv", *request.source_paths, "--algorithm", request.algorithm]
    if request.overwrite_source:
        args.append("--overwrite-source")
    elif request.output_dir:
        args.extend(("--output-dir", request.output_dir))
        if request.suffix:
            args.extend(("--suffix", request.suffix))
    else:
        args.extend(("--suffix", request.suffix or "_uv"))
    args.extend(
        (
            "--timeout", str(request.timeout),
            "--external-timeout", str(request.external_timeout),
            "--jobs", str(request.jobs),
        )
    )
    if cancel_file:
        args.extend(("--cancel-file", os.path.abspath(cancel_file)))
    if request.algorithm == "autouv":
        args.extend(("--topology-prefilter-level", request.topology_prefilter_level))
        if request.unwrap_exe:
            args.extend(("--unwrap-exe", request.unwrap_exe))
        args.extend(
            (
                "--resolution", str(request.resolution),
                "--aspect", _format_number(request.aspect),
                "--udims", str(request.udims),
                "--density", str(request.density),
                "--separate-hard-edges" if request.separate_hard_edges else "--no-separate-hard-edges",
                "--use-normals" if request.use_normals else "--no-use-normals",
                "--overlap-identical" if request.overlap_identical else "--no-overlap-identical",
                "--overlap-mirrored" if request.overlap_mirrored else "--no-overlap-mirrored",
                "--world-scale" if request.world_scale else "--no-world-scale",
                "--merge-meshes" if request.merge_meshes else "--no-merge-meshes",
                "--normalize-uv" if request.normalize_uv else "--no-normalize-uv",
            )
        )
    else:
        for angle in request.angle_degrees:
            args.extend(("--angle-deg", _format_number(angle)))
        if request.rotate_method:
            args.extend(("--rotate-method", request.rotate_method))
    if request.overwrite:
        args.append("--overwrite")
    return args


def resolve_cli_invocation() -> CliInvocation:
    """Find the installed CLI or fall back to the current Python module."""

    package_root = Path(__file__).resolve().parents[2]
    executable_name = "cli-anything-blender.exe" if os.name == "nt" else "cli-anything-blender"
    candidates = [
        package_root / ".venv" / ("Scripts" if os.name == "nt" else "bin") / executable_name,
        Path(sys.executable).with_name(executable_name),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return CliInvocation(str(candidate), (), str(package_root))
    on_path = shutil.which("cli-anything-blender")
    if on_path:
        return CliInvocation(on_path, (), str(package_root))
    return CliInvocation(sys.executable, ("-m", "cli_anything.blender"), str(package_root))


def parse_cli_json(raw_output: str) -> dict[str, Any]:
    """Parse the CLI's JSON response, tolerating a trailing newline."""

    text = raw_output.strip()
    if not text:
        raise ValueError("CLI 没有返回 JSON 结果。")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        value = None
        for index in range(len(text) - 1, -1, -1):
            if text[index] not in "{[":
                continue
            try:
                candidate, end = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if not text[index + end :].strip():
                value = candidate
                break
        if value is None:
            raise ValueError("CLI 返回内容不是有效 JSON。")
    if not isinstance(value, dict):
        raise ValueError("CLI JSON 结果必须是对象。")
    return value
