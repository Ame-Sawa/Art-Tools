import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector


SUFFIX_PATTERN = re.compile(r"^(.*)\.(\d{3})$")
CHECKERBOARD_MATERIAL_NAME = "建模工具箱_棋盘格材质"
AUTO_UV_TIMEOUT_SECONDS = 120
AUTO_UV_SINGLE_TILE_MARGIN = 0.001
VEGETATION_NORMAL_EPSILON = 1.0e-8
VEGETATION_MATRIX_EPSILON = 1.0e-6


class MODELING_TOOLBOX_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    folder_path: bpy.props.StringProperty(
        name="Ministry of Flat 程序目录（可选）",
        description="留空使用工具箱内置程序，否则从此目录读取 UnWrapConsole3.exe",
        subtype="DIR_PATH",
    )

    def draw(self, _context):
        self.layout.prop(self, "folder_path")


class MODELING_TOOLBOX_PG_autouv_settings(bpy.types.PropertyGroup):
    show_advanced: bpy.props.BoolProperty(
        name="显示高级/调试参数",
        description="展开原工具中用于调试和算法实验的参数",
        default=False,
    )

    resolution: bpy.props.IntProperty(name="纹理分辨率", default=1024, min=1)
    separate: bpy.props.BoolProperty(name="分离硬边", default=False)
    aspect: bpy.props.FloatProperty(name="像素宽高比", default=1.0, min=0.0001)
    normals: bpy.props.BoolProperty(name="使用模型法线", default=False)
    udims: bpy.props.IntProperty(name="UDIM 数量", default=1, min=1)
    overlap: bpy.props.BoolProperty(name="重叠相同部件", default=False)
    mirror: bpy.props.BoolProperty(name="重叠镜像部件", default=False)
    worldscale: bpy.props.BoolProperty(name="按世界尺寸缩放 UV", default=False)
    density: bpy.props.IntProperty(name="纹素密度", default=1024, min=1)
    global_pack: bpy.props.BoolProperty(
        name="跨 Mesh 全局打包（先打包后统一归一化）",
        description=(
            "先按各 Mesh 原始 UV 相对尺寸进行不缩放打包，再统一归一化到 0-1 Tile；"
            "UDIM 必须为 1，世界尺寸 UV 可用但可能改变绝对纹素密度"
        ),
        default=True,
    )
    center: bpy.props.FloatVectorProperty(
        name="接缝方向中心",
        description="用于控制接缝方向的空间点",
        size=3,
        default=(0.0, 0.0, 0.0),
        subtype="XYZ",
    )

    suppress: bpy.props.BoolProperty(name="抑制验证错误", default=False)
    quad: bpy.props.BoolProperty(name="四边形识别", default=True)
    weld: bpy.props.BoolProperty(name="顶点焊接", default=True)
    flat: bpy.props.BoolProperty(name="平坦软表面", default=True)
    cone: bpy.props.BoolProperty(name="圆锥识别", default=True)
    cone_ratio: bpy.props.FloatProperty(name="圆锥比例", default=0.5, min=0.0)
    grids: bpy.props.BoolProperty(name="网格识别", default=True)
    strip: bpy.props.BoolProperty(name="条带识别", default=True)
    patch: bpy.props.BoolProperty(name="面片识别", default=True)
    planes: bpy.props.BoolProperty(name="平面识别", default=True)
    flatness: bpy.props.FloatProperty(name="平坦度", default=0.9, min=-1.0, max=1.0)
    merge: bpy.props.BoolProperty(name="合并面", default=True)
    merge_limit: bpy.props.FloatProperty(name="合并角度限制", default=0.0, min=0.0)
    presmooth: bpy.props.BoolProperty(name="预平滑", default=True)
    softunfold: bpy.props.BoolProperty(name="软表面展开", default=True)
    tubes: bpy.props.BoolProperty(name="管状体识别", default=True)
    junctions_debug: bpy.props.BoolProperty(name="管状体连接处理", default=True)
    extra_debug: bpy.props.BoolProperty(name="额外起始点", default=False)
    abf: bpy.props.BoolProperty(name="基于角度的展平", default=True)
    smooth: bpy.props.BoolProperty(name="平滑表面处理", default=True)
    repair_smooth: bpy.props.BoolProperty(name="修复平滑面", default=True)
    repair: bpy.props.BoolProperty(name="修复边", default=True)
    square: bpy.props.BoolProperty(name="直角面识别", default=True)
    relax: bpy.props.BoolProperty(name="松弛", default=True)
    relax_iterations: bpy.props.IntProperty(name="松弛迭代次数", default=50, min=0)
    expand: bpy.props.FloatProperty(name="展开量", default=0.25, min=0.0)
    cut_debug: bpy.props.BoolProperty(name="切割优化", default=True)
    stretch: bpy.props.BoolProperty(name="拉伸修正", default=True)
    match: bpy.props.BoolProperty(name="三角形匹配", default=True)
    packing: bpy.props.BoolProperty(name="打包", default=True)
    rasterization: bpy.props.IntProperty(name="打包栅格分辨率", default=64, min=1)
    packing_iterations: bpy.props.IntProperty(name="打包迭代次数", default=4, min=0)
    scale_to_fit: bpy.props.FloatProperty(name="适配缩放", default=0.5, min=0.0)
    validate: bpy.props.BoolProperty(name="阶段验证", default=False)


def get_autouv_executable_path(context=None):
    """Return a configured Ministry of Flat executable or the bundled one."""
    if context is not None:
        addon = context.preferences.addons.get(__name__)
        if addon is not None:
            folder_path = addon.preferences.folder_path.strip()
            if folder_path:
                return Path(folder_path) / "UnWrapConsole3.exe"
    return (
        Path(__file__).resolve().parent
        / "third_party"
        / "MinistryOfFlat"
        / "UnWrapConsole3.exe"
    )


def _safe_autouv_filename(name):
    """Turn a Blender object name into a safe temporary filename stem."""
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return safe_name or "mesh"


def _set_only_object_selected(obj, view_layer):
    for candidate in list(view_layer.objects):
        if candidate is not None:
            candidate.select_set(candidate == obj)
    view_layer.objects.active = obj


def _remove_imported_objects(objects):
    """Remove objects created by a temporary OBJ import."""
    for obj in objects:
        mesh = getattr(obj, "data", None)
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    bpy.context.view_layer.update()


def _validate_autouv_topology(source_mesh, imported_mesh):
    if len(source_mesh.vertices) != len(imported_mesh.vertices):
        return False, "顶点数量不一致"
    if len(source_mesh.polygons) != len(imported_mesh.polygons):
        return False, "面数量不一致"
    if len(source_mesh.loops) != len(imported_mesh.loops):
        return False, "Loop 数量不一致"

    for source_polygon, imported_polygon in zip(
        source_mesh.polygons, imported_mesh.polygons
    ):
        if source_polygon.loop_total != imported_polygon.loop_total:
            return False, "面 Loop 结构不一致"

    return True, ""


def _normalize_autouv_uv_layer(uv_layer, margin=AUTO_UV_SINGLE_TILE_MARGIN):
    """Fit UVs into one 0-1 tile without changing their aspect ratio."""
    if uv_layer is None or not uv_layer.data:
        return False

    min_x = min(item.uv.x for item in uv_layer.data)
    max_x = max(item.uv.x for item in uv_layer.data)
    min_y = min(item.uv.y for item in uv_layer.data)
    max_y = max(item.uv.y for item in uv_layer.data)
    if (
        min_x >= 0.0
        and max_x <= 1.0
        and min_y >= 0.0
        and max_y <= 1.0
    ):
        return False

    span_x = max_x - min_x
    span_y = max_y - min_y
    largest_span = max(span_x, span_y)
    if largest_span <= 1.0e-12:
        return False

    usable_size = max(1.0e-6, 1.0 - (2.0 * margin))
    scale = usable_size / largest_span
    center_x = (min_x + max_x) * 0.5
    center_y = (min_y + max_y) * 0.5
    for item in uv_layer.data:
        item.uv.x = (item.uv.x - center_x) * scale + 0.5
        item.uv.y = (item.uv.y - center_y) * scale + 0.5
    return True


def _copy_autouv_layer(source_obj, imported_obj, normalize_to_unit_tile=False):
    source_mesh = source_obj.data
    imported_mesh = imported_obj.data
    imported_uv_layer = imported_mesh.uv_layers.active
    if imported_uv_layer is None or len(imported_uv_layer.data) != len(source_mesh.loops):
        raise RuntimeError("外部程序没有返回有效 UV 数据")

    topology_ok, topology_error = _validate_autouv_topology(
        source_mesh, imported_mesh
    )
    if not topology_ok:
        raise RuntimeError(f"拓扑校验失败：{topology_error}")

    uv_layer = source_mesh.uv_layers.active
    if uv_layer is None:
        uv_layer = source_mesh.uv_layers.new(name="UVMap")

    for index, imported_loop_uv in enumerate(imported_uv_layer.data):
        uv_layer.data[index].uv = imported_loop_uv.uv

    normalized = False
    if normalize_to_unit_tile:
        normalized = _normalize_autouv_uv_layer(uv_layer)

    source_mesh.uv_layers.active_index = source_mesh.uv_layers.find(uv_layer.name)
    source_mesh.update()
    return normalized


def _unique_mesh_objects(objects):
    """Return one representative object for each shared Mesh datablock."""
    unique = []
    seen = set()
    for obj in objects:
        key = obj.data.as_pointer()
        if key in seen:
            continue
        seen.add(key)
        unique.append(obj)
    return unique


def _normalize_meshes_globally(objects, settings):
    """Apply one uniform UV transform to all active UV layers after global packing."""
    representatives = _unique_mesh_objects(objects)
    active_layers = []
    min_x = float("inf")
    min_y = float("inf")
    max_x = float("-inf")
    max_y = float("-inf")

    for obj in representatives:
        uv_layer = obj.data.uv_layers.active
        if uv_layer is None or not uv_layer.data:
            raise RuntimeError(f"Mesh {obj.name} 没有可归一化的活动 UV")
        active_layers.append((obj.data, uv_layer))
        for item in uv_layer.data:
            min_x = min(min_x, item.uv.x)
            min_y = min(min_y, item.uv.y)
            max_x = max(max_x, item.uv.x)
            max_y = max(max_y, item.uv.y)

    if not active_layers:
        raise RuntimeError("没有可归一化的活动 UV")

    span_x = max_x - min_x
    span_y = max_y - min_y
    largest_span = max(span_x, span_y)
    if largest_span <= 1.0e-12:
        raise RuntimeError("全局 UV 范围过小，无法归一化到 0-1 Tile")

    margin = 1.0 / max(1, settings.resolution)
    usable_size = max(1.0e-6, 1.0 - (2.0 * margin))
    scale = usable_size / largest_span
    center_x = (min_x + max_x) * 0.5
    center_y = (min_y + max_y) * 0.5

    for mesh, uv_layer in active_layers:
        for item in uv_layer.data:
            item.uv.x = (item.uv.x - center_x) * scale + 0.5
            item.uv.y = (item.uv.y - center_y) * scale + 0.5
        mesh.update()

    return scale


def _snapshot_uv_state(objects):
    """Capture UV layers for an atomic global AutoUV rollback."""
    snapshot = []
    for obj in _unique_mesh_objects(objects):
        mesh = obj.data
        layers = []
        for layer in mesh.uv_layers:
            layers.append(
                {
                    "name": layer.name,
                    "uvs": [(item.uv.x, item.uv.y) for item in layer.data],
                }
            )
        snapshot.append(
            {
                "mesh": mesh,
                "layers": layers,
                "active_index": mesh.uv_layers.active_index,
            }
        )
    return snapshot


def _restore_uv_state(snapshot):
    """Restore the UV state captured by _snapshot_uv_state."""
    for state in snapshot:
        mesh = state["mesh"]
        saved_layers = state["layers"]
        while len(mesh.uv_layers) > len(saved_layers):
            mesh.uv_layers.remove(mesh.uv_layers[-1])
        while len(mesh.uv_layers) < len(saved_layers):
            layer_index = len(mesh.uv_layers)
            mesh.uv_layers.new(name=saved_layers[layer_index]["name"])

        for layer, saved_layer in zip(mesh.uv_layers, saved_layers):
            layer.name = saved_layer["name"]
            if len(layer.data) != len(saved_layer["uvs"]):
                raise RuntimeError(f"无法恢复 Mesh {mesh.name} 的 UV Loop 数量")
            for item, (u, v) in zip(layer.data, saved_layer["uvs"]):
                item.uv = (u, v)
        if mesh.uv_layers:
            mesh.uv_layers.active_index = min(
                max(state["active_index"], 0), len(mesh.uv_layers) - 1
            )
        mesh.update()


def _global_pack_enabled(settings):
    return bool(
        settings.global_pack
        and settings.udims == 1
    )


def _global_pack_status_text(settings):
    if not settings.global_pack:
        return "当前：已关闭跨 Mesh 全局打包"
    if settings.udims > 1:
        return "当前：UDIM>1，将跳过跨 Mesh 全局打包"
    if settings.worldscale:
        return "当前：将执行跨 Mesh 打包并归一化 UV（绝对纹素密度可能改变）"
    return "当前：将执行跨 Mesh 打包并统一归一化 UV"


def _pack_meshes_globally(objects, settings, view_layer):
    """Pack active UVs from selected unique Mesh datablocks together."""
    representatives = _unique_mesh_objects(objects)
    if not representatives:
        raise RuntimeError("没有可用于全局打包的 Mesh")

    for candidate in list(view_layer.objects):
        if candidate is not None:
            candidate.select_set(candidate in representatives)
    view_layer.objects.active = representatives[0]
    margin = 1.0 / max(1, settings.resolution)

    try:
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.select_all(action="SELECT")
        result = bpy.ops.uv.pack_islands(
            rotate=True,
            scale=False,
            margin=margin,
        )
        if "FINISHED" not in result:
            raise RuntimeError(f"Blender UV 全局打包未完成：{result}")
    finally:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in representatives:
            obj.data.update()
    return margin


def _autouv_bool(value):
    return "TRUE" if value else "FALSE"


def _autouv_number(value):
    return format(value, ".6g")


def _build_autouv_command(settings, executable_path, input_path, output_path):
    command = [str(executable_path), str(input_path), str(output_path)]
    values = (
        ("RESOLUTION", settings.resolution),
        ("SEPARATE", settings.separate),
        ("ASPECT", settings.aspect),
        ("NORMALS", settings.normals),
        ("UDIMS", settings.udims),
        ("OVERLAP", settings.overlap),
        ("MIRROR", settings.mirror),
        ("WORLDSCALE", settings.worldscale),
        ("DENSITY", settings.density),
        ("SUPRESS", settings.suppress),
        ("QUAD", settings.quad),
        ("WELD", settings.weld),
        ("FLAT", settings.flat),
        ("CONE", settings.cone),
        ("CONERATIO", settings.cone_ratio),
        ("GRIDS", settings.grids),
        ("STRIP", settings.strip),
        ("PATCH", settings.patch),
        ("PLANES", settings.planes),
        ("FLATT", settings.flatness),
        ("MERGE", settings.merge),
        ("MERGELIMIT", settings.merge_limit),
        ("PRESMOOTH", settings.presmooth),
        ("SOFTUNFOLD", settings.softunfold),
        ("TUBES", settings.tubes),
        ("JUNCTIONSDEBUG", settings.junctions_debug),
        ("EXTRADEBUG", settings.extra_debug),
        ("ABF", settings.abf),
        ("SMOOTH", settings.smooth),
        ("REPAIRSMOOTH", settings.repair_smooth),
        ("REPAIR", settings.repair),
        ("SQUARE", settings.square),
        ("RELAX", settings.relax),
        ("RELAX_ITERATIONS", settings.relax_iterations),
        ("EXPAND", settings.expand),
        ("CUTDEBUG", settings.cut_debug),
        ("STRETCH", settings.stretch),
        ("MATCH", settings.match),
        ("PACKING", settings.packing),
        ("RASTERIZATION", settings.rasterization),
        ("PACKING_ITERATIONS", settings.packing_iterations),
        ("SCALETOFIT", settings.scale_to_fit),
        ("VALIDATE", settings.validate),
    )
    for flag, value in values:
        command.extend((f"-{flag}", _autouv_bool(value) if isinstance(value, bool) else _autouv_number(value)))
    command.extend(
        (
            "-CENTER",
            _autouv_number(settings.center[0]),
            _autouv_number(settings.center[1]),
            _autouv_number(settings.center[2]),
        )
    )
    return command


def _run_autouv_for_object(
    obj, executable_path, settings, temporary_directory, index, view_layer
):
    _set_only_object_selected(obj, view_layer)
    stem = f"{index:03d}_{_safe_autouv_filename(obj.name)}"
    input_path = Path(temporary_directory) / f"{stem}.obj"
    output_path = Path(temporary_directory) / f"{stem}_unwrapped.obj"

    bpy.ops.wm.obj_export(
        filepath=str(input_path),
        export_selected_objects=True,
        export_materials=False,
    )
    if not input_path.is_file():
        raise RuntimeError("Blender 没有生成临时 OBJ")

    completed = subprocess.run(
        _build_autouv_command(settings, executable_path, input_path, output_path),
        cwd=str(executable_path.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=AUTO_UV_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0 and not output_path.is_file():
        details = (completed.stderr or completed.stdout or "").strip()
        if len(details) > 400:
            details = details[-400:]
        suffix = f"：{details}" if details else ""
        raise RuntimeError(f"UnWrapConsole3.exe 返回错误码 {completed.returncode}{suffix}")
    if not output_path.is_file():
        raise RuntimeError("外部程序没有生成输出 OBJ")
    if completed.returncode != 0:
        print(
            "AutoUV 外部程序返回非零状态，但已生成有效输出，继续导入："
            f" {completed.returncode}"
        )

    objects_before = set(bpy.data.objects)
    imported_objects = []
    try:
        bpy.ops.wm.obj_import(filepath=str(output_path))
        imported_objects = [
            item for item in bpy.data.objects if item not in objects_before
        ]
        imported_meshes = [item for item in imported_objects if item.type == "MESH"]
        if len(imported_meshes) != 1:
            raise RuntimeError(
                f"外部 OBJ 应包含 1 个网格，实际得到 {len(imported_meshes)} 个"
            )
        normalized = _copy_autouv_layer(
            obj,
            imported_meshes[0],
            normalize_to_unit_tile=(
                settings.udims == 1 and not settings.worldscale and not settings.global_pack
            ),
        )
        return {"normalized": normalized}
    finally:
        _remove_imported_objects(imported_objects)


class OBJECT_OT_autouv_ministry_of_flat(bpy.types.Operator):
    bl_idname = "object.autouv_ministry_of_flat"
    bl_label = "AutoUV（Ministry of Flat）"
    bl_description = "使用 Ministry of Flat 为所有选中的网格对象生成 AutoUV"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if context.mode != "OBJECT":
            self.report({"ERROR"}, "请先切换到 Object Mode 再执行 AutoUV")
            return {"CANCELLED"}

        executable_path = get_autouv_executable_path(context)
        if not executable_path.is_file():
            self.report(
                {"ERROR"},
                f"找不到 AutoUV 程序：{executable_path}",
            )
            return {"CANCELLED"}

        objects = [
            obj
            for obj in context.selected_objects
            if obj.type == "MESH" and obj.data is not None
        ]
        if not objects:
            self.report({"ERROR"}, "请至少选择一个网格对象")
            return {"CANCELLED"}

        mesh_objects = _unique_mesh_objects(objects)
        view_layer = context.view_layer
        settings = context.scene.modeling_toolbox_autouv
        active_before = view_layer.objects.active
        selected_before = list(context.selected_objects)
        global_pack_enabled = _global_pack_enabled(settings)
        uv_snapshot = _snapshot_uv_state(mesh_objects) if global_pack_enabled else []
        temporary_directory = tempfile.mkdtemp(
            prefix="modeling_toolbox_autouv_",
            dir=bpy.app.tempdir,
        )
        completed = 0
        succeeded = 0
        normalized_count = 0
        pack_margin = None
        pack_applied = False
        global_normalized = False
        rolled_back = False
        failures = []

        try:
            for index, obj in enumerate(mesh_objects, start=1):
                try:
                    result = _run_autouv_for_object(
                        obj,
                        executable_path,
                        settings,
                        temporary_directory,
                        index,
                        view_layer,
                    )
                    completed += 1
                    normalized_count += int(result.get("normalized", False))
                except Exception as error:  # Keep processing the remaining selection.
                    failures.append(f"{obj.name}: {error}")

            if global_pack_enabled:
                if failures:
                    _restore_uv_state(uv_snapshot)
                    rolled_back = True
                    normalized_count = 0
                else:
                    try:
                        pack_margin = _pack_meshes_globally(
                            mesh_objects,
                            settings,
                            view_layer,
                        )
                        _normalize_meshes_globally(mesh_objects, settings)
                        global_normalized = True
                        pack_applied = True
                        succeeded = completed
                    except Exception as error:
                        failures.append(f"跨 Mesh 全局打包失败：{error}")
                        _restore_uv_state(uv_snapshot)
                        rolled_back = True
                        normalized_count = 0
                        global_normalized = False
            else:
                succeeded = completed
        finally:
            shutil.rmtree(temporary_directory, ignore_errors=True)
            if bpy.context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            for candidate in list(view_layer.objects):
                if candidate is not None:
                    candidate.select_set(candidate in selected_before)
            if active_before is not None and active_before.name in bpy.data.objects:
                view_layer.objects.active = active_before

        if failures:
            print("AutoUV 失败详情：")
            for failure in failures:
                print(f"- {failure}")
            first_failure = failures[0]
            if len(first_failure) > 180:
                first_failure = first_failure[:177] + "..."
            rollback_text = "；已回滚本次全部 UV" if rolled_back else ""
            self.report(
                {"WARNING"},
                f"AutoUV 完成：成功提交 {succeeded} 个独立 Mesh，失败 {len(failures)} 个；"
                f"{first_failure}{rollback_text}",
            )
        else:
            if global_normalized:
                normalized_text = "，已统一归一化到 0-1 Tile"
                if settings.worldscale:
                    normalized_text += "（世界尺寸 UV，绝对纹素密度可能改变）"
            else:
                normalized_text = (
                    f"，已归一化到 0-1 Tile：{normalized_count} 个"
                    if normalized_count
                    else ""
                )
            if pack_applied:
                pack_text = f"，已跨 Mesh 不缩放打包（边距 {pack_margin:.6f}）"
            elif settings.global_pack:
                pack_text = "，已跳过全局打包（UDIM>1）"
            else:
                pack_text = "，未启用跨 Mesh 全局打包"
            self.report(
                {"INFO"},
                f"AutoUV 完成：成功处理 {succeeded} 个独立 Mesh"
                f"{normalized_text}{pack_text}",
            )
        return {"FINISHED"}


def fix_materials(objects):
    relinked = 0
    renamed = 0
    removed = 0
    skipped = 0

    for obj in objects:
        data = getattr(obj, "data", None)
        if data is None or not hasattr(data, "materials"):
            continue

        mats = data.materials
        for index, mat in enumerate(mats):
            if mat is None:
                continue

            match = SUFFIX_PATTERN.match(mat.name)
            if not match:
                continue

            base_name = match.group(1)
            base_mat = bpy.data.materials.get(base_name)

            if base_mat is not None and base_mat != mat:
                mats[index] = base_mat
                relinked += 1

    for mat in list(bpy.data.materials):
        match = SUFFIX_PATTERN.match(mat.name)
        if not match:
            continue

        base_name = match.group(1)
        base_mat = bpy.data.materials.get(base_name)

        if base_mat is not None and base_mat != mat:
            if mat.users == 0:
                bpy.data.materials.remove(mat)
                removed += 1
            else:
                skipped += 1
            continue

        if base_mat is None:
            try:
                mat.name = base_name
                renamed += 1
            except RuntimeError:
                skipped += 1

    return relinked, renamed, removed, skipped


def get_or_create_checkerboard_material():
    material = bpy.data.materials.get(CHECKERBOARD_MATERIAL_NAME)
    if material is None:
        material = bpy.data.materials.new(CHECKERBOARD_MATERIAL_NAME)

    material.use_nodes = True
    node_tree = material.node_tree
    nodes = node_tree.nodes
    links = node_tree.links
    nodes.clear()

    output_node = nodes.new("ShaderNodeOutputMaterial")
    output_node.location = (420, 0)

    shader_node = nodes.new("ShaderNodeBsdfPrincipled")
    shader_node.location = (120, 0)

    checker_node = nodes.new("ShaderNodeTexChecker")
    checker_node.location = (-120, 0)
    checker_node.inputs["Color1"].default_value = (0.04, 0.04, 0.04, 1.0)
    checker_node.inputs["Color2"].default_value = (0.8, 0.8, 0.8, 1.0)
    checker_node.inputs["Scale"].default_value = 12.0

    texture_coordinate_node = nodes.new("ShaderNodeTexCoord")
    texture_coordinate_node.location = (-360, 0)

    links.new(
        texture_coordinate_node.outputs["UV"],
        checker_node.inputs["Vector"],
    )
    links.new(checker_node.outputs["Color"], shader_node.inputs["Base Color"])
    links.new(shader_node.outputs["BSDF"], output_node.inputs["Surface"])

    return material


def replace_all_materials_with_checkerboard(objects):
    checkerboard_material = get_or_create_checkerboard_material()
    changed_slots = 0
    changed_objects = 0

    for obj in objects:
        data = getattr(obj, "data", None)
        if data is None or not hasattr(data, "materials"):
            continue

        object_changed = False
        for index in range(len(data.materials)):
            if data.materials[index] != checkerboard_material:
                data.materials[index] = checkerboard_material
                changed_slots += 1
                object_changed = True

        if object_changed:
            changed_objects += 1

    return changed_objects, changed_slots


def remove_unused_mesh_material_slots(objects):
    cleaned_objects = 0
    removed_slots = 0
    skipped_objects = 0
    view_layer = bpy.context.view_layer

    for obj in objects:
        if obj.type != 'MESH':
            continue

        data = getattr(obj, "data", None)
        if data is None or not hasattr(data, "materials"):
            continue

        slot_count_before = len(obj.material_slots)
        if slot_count_before == 0:
            continue

        used_indices = {poly.material_index for poly in data.polygons}
        unused_indices = [
            index for index in range(slot_count_before)
            if index not in used_indices
        ]

        if not unused_indices:
            continue

        try:
            with bpy.context.temp_override(
                object=obj,
                active_object=obj,
                selected_objects=[obj],
                selected_editable_objects=[obj],
                view_layer=view_layer,
            ):
                bpy.ops.object.material_slot_remove_unused()
        except RuntimeError:
            skipped_objects += 1
            continue

        slot_count_after = len(obj.material_slots)
        removed_now = slot_count_before - slot_count_after
        if removed_now > 0:
            cleaned_objects += 1
            removed_slots += removed_now

    return cleaned_objects, removed_slots, skipped_objects


def make_mesh_single_user(obj):
    if obj.data is not None and obj.data.users > 1:
        obj.data = obj.data.copy()


def compute_lowest_world_z(obj):
    if obj.type != 'MESH' or obj.data is None or len(obj.data.vertices) == 0:
        return None

    world_matrix = obj.matrix_world
    world_vertices = [world_matrix @ vertex.co for vertex in obj.data.vertices]
    return min(vertex.z for vertex in world_vertices)


def apply_all_transforms(objects):
    candidates = [
        obj for obj in objects
        if obj.type in {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT', 'GPENCIL', 'GREASEPENCIL', 'ARMATURE', 'LATTICE', 'EMPTY'}
    ]
    if not candidates:
        return 0, 0

    with bpy.context.temp_override(
        active_object=candidates[0],
        object=candidates[0],
        selected_objects=candidates,
        selected_editable_objects=candidates,
    ):
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    return len(candidates), max(0, len(objects) - len(candidates))


def set_origin_to_lowest_center(objects):
    moved = 0
    skipped = 0
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    cursor_location = scene.cursor.location.copy()
    active_object = view_layer.objects.active

    try:
        for obj in objects:
            lowest_z = compute_lowest_world_z(obj)
            if lowest_z is None:
                skipped += 1
                continue

            make_mesh_single_user(obj)
            with bpy.context.temp_override(
                object=obj,
                active_object=obj,
                selected_objects=[obj],
                selected_editable_objects=[obj],
                view_layer=view_layer,
                scene=scene,
            ):
                bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='MEDIAN')

            current_origin = obj.matrix_world.translation.copy()
            scene.cursor.location = Vector((current_origin.x, current_origin.y, lowest_z))
            with bpy.context.temp_override(
                object=obj,
                active_object=obj,
                selected_objects=[obj],
                selected_editable_objects=[obj],
                view_layer=view_layer,
                scene=scene,
            ):
                bpy.ops.object.origin_set(type='ORIGIN_CURSOR', center='MEDIAN')

            moved += 1
    finally:
        scene.cursor.location = cursor_location
        view_layer.objects.active = active_object

    return moved, skipped


def set_origin_to_lowest_volume_center(objects):
    moved = 0
    skipped = 0
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    cursor_location = scene.cursor.location.copy()
    active_object = view_layer.objects.active

    try:
        for obj in objects:
            lowest_z = compute_lowest_world_z(obj)
            if lowest_z is None:
                skipped += 1
                continue

            make_mesh_single_user(obj)
            with bpy.context.temp_override(
                object=obj,
                active_object=obj,
                selected_objects=[obj],
                selected_editable_objects=[obj],
                view_layer=view_layer,
                scene=scene,
            ):
                bpy.ops.object.origin_set(type='ORIGIN_CENTER_OF_VOLUME', center='MEDIAN')

            current_origin = obj.matrix_world.translation.copy()
            scene.cursor.location = Vector((current_origin.x, current_origin.y, lowest_z))
            with bpy.context.temp_override(
                object=obj,
                active_object=obj,
                selected_objects=[obj],
                selected_editable_objects=[obj],
                view_layer=view_layer,
                scene=scene,
            ):
                bpy.ops.object.origin_set(type='ORIGIN_CURSOR', center='MEDIAN')

            moved += 1
    finally:
        scene.cursor.location = cursor_location
        view_layer.objects.active = active_object

    return moved, skipped


def set_origin_to_world_origin(objects):
    moved = 0
    skipped = 0
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    cursor_location = scene.cursor.location.copy()
    active_object = view_layer.objects.active

    try:
        scene.cursor.location = Vector((0.0, 0.0, 0.0))
        for obj in objects:
            if obj.type != 'MESH' or obj.data is None:
                skipped += 1
                continue

            make_mesh_single_user(obj)
            with bpy.context.temp_override(
                object=obj,
                active_object=obj,
                selected_objects=[obj],
                selected_editable_objects=[obj],
                view_layer=view_layer,
                scene=scene,
            ):
                bpy.ops.object.origin_set(type='ORIGIN_CURSOR', center='MEDIAN')
            moved += 1
    finally:
        scene.cursor.location = cursor_location
        view_layer.objects.active = active_object

    return moved, skipped


def move_objects_to_world_origin(objects):
    moved = 0
    skipped = 0

    for obj in objects:
        if obj.type == 'MESH' and obj.data is not None:
            obj.location = (0.0, 0.0, 0.0)
            moved += 1
        else:
            skipped += 1

    return moved, skipped


def _vegetation_matrix_equal(left, right, epsilon=VEGETATION_MATRIX_EPSILON):
    return all(
        abs(left[row][column] - right[row][column]) <= epsilon
        for row in range(4)
        for column in range(4)
    )


def _vegetation_target_groups(context):
    edit_mode = context.mode == "EDIT_MESH"
    groups = {}
    all_world_points = []

    for obj in context.selected_objects:
        if obj.type != "MESH" or obj.data is None:
            continue
        if edit_mode and obj.mode != "EDIT":
            continue

        selected_positions = {}
        if edit_mode:
            bmesh_data = bmesh.from_edit_mesh(obj.data)
            bmesh_data.verts.ensure_lookup_table()
            selected_vertices = [vertex for vertex in bmesh_data.verts if vertex.select]
            for vertex in selected_vertices:
                selected_positions[vertex.index] = vertex.co.copy()
        else:
            selected_positions = {
                vertex.index: vertex.co.copy() for vertex in obj.data.vertices
            }

        if not selected_positions:
            continue

        mesh_key = obj.data.as_pointer()
        group = groups.setdefault(
            mesh_key,
            {
                "mesh": obj.data,
                "representative": obj,
                "indices": set(),
                "world_positions": {},
            },
        )
        group["indices"].update(selected_positions)
        for vertex_index, local_position in selected_positions.items():
            world_position = obj.matrix_world @ local_position
            group["world_positions"].setdefault(vertex_index, world_position)
            all_world_points.append(world_position)

    return list(groups.values()), all_world_points


def _vegetation_world_bounds_center(world_points):
    if not world_points:
        return None

    minimum = Vector(
        (
            min(point.x for point in world_points),
            min(point.y for point in world_points),
            min(point.z for point in world_points),
        )
    )
    maximum = Vector(
        (
            max(point.x for point in world_points),
            max(point.y for point in world_points),
            max(point.z for point in world_points),
        )
    )
    return (minimum + maximum) * 0.5


def _vegetation_shared_transform_conflicts(mesh, reference_object):
    conflicts = []
    for obj in bpy.data.objects:
        if obj.type != "MESH" or obj.data is None:
            continue
        if obj.data.as_pointer() != mesh.as_pointer():
            continue
        if not _vegetation_matrix_equal(obj.matrix_world, reference_object.matrix_world):
            conflicts.append(obj)
    return conflicts


def _vegetation_local_normal(obj, world_normal):
    # Object-space normals transform to world space with inverse-transpose.
    # Therefore, converting a desired world normal back to object space uses
    # the transpose of the object's 3x3 transform.
    local_normal = obj.matrix_world.to_3x3().transposed() @ world_normal
    if local_normal.length <= VEGETATION_NORMAL_EPSILON:
        return None
    return local_normal.normalized()


def _vegetation_write_normals(group, mode, center):
    mesh = group["mesh"]
    obj = group["representative"]
    target_indices = group["indices"]
    local_normals = {}
    zero_length_count = 0

    for vertex_index in target_indices:
        if mode == "UP":
            world_normal = Vector((0.0, 0.0, 1.0))
        else:
            world_normal = group["world_positions"][vertex_index] - center
            if world_normal.length <= VEGETATION_NORMAL_EPSILON:
                zero_length_count += 1
                continue
            world_normal.normalize()

        local_normal = _vegetation_local_normal(obj, world_normal)
        if local_normal is None:
            zero_length_count += 1
            continue
        local_normals[vertex_index] = local_normal

    if not local_normals:
        return 0, zero_length_count

    if len(target_indices) == len(mesh.vertices):
        vertex_normals = [
            tuple(local_normals.get(index, (0.0, 0.0, 0.0)))
            for index in range(len(mesh.vertices))
        ]
        mesh.normals_split_custom_set_from_vertices(vertex_normals)
    else:
        mesh.update()
        current_corner_normals = [
            Vector(corner_normal.vector) for corner_normal in mesh.corner_normals
        ]
        if len(current_corner_normals) != len(mesh.loops):
            raise RuntimeError("无法读取 Mesh Corner 法线")

        loop_normals = [tuple(normal) for normal in current_corner_normals]
        for loop in mesh.loops:
            normal = local_normals.get(loop.vertex_index)
            if normal is not None:
                loop_normals[loop.index] = tuple(normal)
        mesh.normals_split_custom_set(loop_normals)

    mesh.update()
    if mesh.is_editmode:
        bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
    return len(local_normals), zero_length_count


def _vegetation_poll(context):
    if context.mode not in {"OBJECT", "EDIT_MESH"}:
        return False
    return any(
        obj.type == "MESH"
        and obj.data is not None
        and (context.mode != "EDIT_MESH" or obj.mode == "EDIT")
        for obj in context.selected_objects
    )


def _execute_vegetation_normals(operator, context, mode):
    groups, world_points = _vegetation_target_groups(context)
    if not groups or not world_points:
        operator.report({"WARNING"}, "没有可处理的选中网格顶点")
        return {"CANCELLED"}

    center = _vegetation_world_bounds_center(world_points)
    processed_meshes = 0
    processed_vertices = 0
    skipped_meshes = 0
    zero_length_vertices = 0
    failures = []

    for group in groups:
        mesh = group["mesh"]
        representative = group["representative"]
        conflicts = _vegetation_shared_transform_conflicts(mesh, representative)
        if conflicts:
            conflict_names = ", ".join(obj.name for obj in conflicts)
            failures.append(
                f"{mesh.name}: 共享 Mesh 的世界变换不同（{conflict_names}），已跳过"
            )
            skipped_meshes += 1
            continue

        try:
            changed, zero_count = _vegetation_write_normals(group, mode, center)
            zero_length_vertices += zero_count
            if changed:
                processed_meshes += 1
                processed_vertices += changed
            else:
                skipped_meshes += 1
                failures.append(f"{mesh.name}: 没有可归一化的法线方向")
        except Exception as error:
            skipped_meshes += 1
            failures.append(f"{mesh.name}: {error}")

    mode_text = "法线向上" if mode == "UP" else "法线离心"
    warning_parts = []
    if zero_length_vertices:
        warning_parts.append(f"中心重合/无效方向 {zero_length_vertices} 个，已保留原法线")
    if failures:
        warning_parts.append("；".join(failures[:3]))
        if len(failures) > 3:
            warning_parts.append(f"另有 {len(failures) - 3} 个 Mesh 被跳过")

    if processed_meshes == 0:
        message = f"{mode_text}未修改任何 Mesh"
        if warning_parts:
            message += "；" + "；".join(warning_parts)
        operator.report({"WARNING"}, message)
        print(message)
        return {"CANCELLED"}

    message = (
        f"{mode_text}完成：处理 {processed_meshes} 个 Mesh、"
        f"{processed_vertices} 个顶点，跳过 {skipped_meshes} 个 Mesh"
    )
    if warning_parts:
        message += "；" + "；".join(warning_parts)
    report_level = {"WARNING"} if warning_parts else {"INFO"}
    operator.report(report_level, message)
    print(message)
    return {"FINISHED"}


class OBJECT_OT_vegetation_normals_up(bpy.types.Operator):
    bl_idname = "object.vegetation_normals_up"
    bl_label = "法线向上"
    bl_description = "将选中顶点或选中网格对象的全部顶点法线设置为世界空间向上"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _vegetation_poll(context)

    def execute(self, context):
        return _execute_vegetation_normals(self, context, "UP")


class OBJECT_OT_vegetation_normals_outward(bpy.types.Operator):
    bl_idname = "object.vegetation_normals_outward"
    bl_label = "法线离心"
    bl_description = "将选中顶点法线设置为从选中顶点世界空间包围盒中心指向顶点"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _vegetation_poll(context)

    def execute(self, context):
        return _execute_vegetation_normals(self, context, "OUTWARD")


class OBJECT_OT_fix_fbx_material_names(bpy.types.Operator):
    bl_idname = "object.fix_fbx_material_names"
    bl_label = "Fix FBX Material Names"
    bl_description = "Merge duplicate material names or remove numeric suffixes"
    bl_options = {'REGISTER', 'UNDO'}

    selected_only: bpy.props.BoolProperty(
        name="Selected Objects Only",
        description="Only process materials used by selected objects",
        default=True,
    )

    def execute(self, context):
        objects = context.selected_objects if self.selected_only else bpy.data.objects
        relinked, renamed, removed, skipped = fix_materials(objects)

        message = (
            f"材质槽合并 {relinked} 个，材质重命名 {renamed} 个，"
            f"删除重复材质 {removed} 个，跳过 {skipped} 个"
        )
        self.report({'INFO'}, message)
        print(message)
        return {'FINISHED'}


class OBJECT_OT_replace_all_materials_with_checkerboard(bpy.types.Operator):
    bl_idname = "object.replace_all_materials_with_checkerboard"
    bl_label = "Replace All Materials With Checkerboard"
    bl_description = "Replace every material slot in the current file with a checkerboard material"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        changed_objects, changed_slots = replace_all_materials_with_checkerboard(bpy.data.objects)
        message = f"棋盘格材质已应用到 {changed_objects} 个对象、{changed_slots} 个材质槽"
        self.report({'INFO'}, message)
        print(message)
        return {'FINISHED'}


class OBJECT_OT_remove_unused_mesh_material_slots(bpy.types.Operator):
    bl_idname = "object.remove_unused_mesh_material_slots_batch"
    bl_label = "Remove Unused Mesh Material Slots"
    bl_description = "Batch remove mesh material slots that are not used by any faces"
    bl_options = {'REGISTER', 'UNDO'}

    selected_only: bpy.props.BoolProperty(
        name="Selected Objects Only",
        description="Only process selected mesh objects",
        default=True,
    )

    def execute(self, context):
        objects = context.selected_objects if self.selected_only else bpy.data.objects
        cleaned_objects, removed_slots, skipped_objects = remove_unused_mesh_material_slots(objects)

        message = (
            f"清理对象 {cleaned_objects} 个，删除未使用材质槽 {removed_slots} 个，"
            f"跳过对象 {skipped_objects} 个"
        )
        self.report({'INFO'}, message)
        print(message)
        return {'FINISHED'}


class OBJECT_OT_apply_all_transforms_selected(bpy.types.Operator):
    bl_idname = "object.apply_all_transforms_selected_batch"
    bl_label = "Apply All Transforms"
    bl_description = "Apply location, rotation, and scale to selected objects"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        processed, skipped = apply_all_transforms(list(context.selected_objects))
        message = f"应用变换对象 {processed} 个，跳过 {skipped} 个"
        self.report({'INFO'}, message)
        print(message)
        return {'FINISHED'}


class OBJECT_OT_set_origin_to_lowest_center(bpy.types.Operator):
    bl_idname = "object.set_origin_to_lowest_center_batch"
    bl_label = "Origin To Lowest Center"
    bl_description = "Move the origin to the center of the model's lowest points"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        moved, skipped = set_origin_to_lowest_center(list(context.selected_objects))
        message = f"移动原点对象 {moved} 个，跳过 {skipped} 个"
        self.report({'INFO'}, message)
        print(message)
        return {'FINISHED'}


class OBJECT_OT_set_origin_to_lowest_volume_center(bpy.types.Operator):
    bl_idname = "object.set_origin_to_lowest_volume_center_batch"
    bl_label = "Origin To Lowest Volume Center"
    bl_description = "Move the origin to the volume center, then place it at the model's lowest world Z"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        moved, skipped = set_origin_to_lowest_volume_center(list(context.selected_objects))
        message = f"移动体积中心原点对象 {moved} 个，跳过 {skipped} 个"
        self.report({'INFO'}, message)
        print(message)
        return {'FINISHED'}


class OBJECT_OT_set_origin_to_world_origin(bpy.types.Operator):
    bl_idname = "object.set_origin_to_world_origin_batch"
    bl_label = "Origin To World Origin"
    bl_description = "Move the origin to the world origin while keeping mesh geometry in place"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        moved, skipped = set_origin_to_world_origin(list(context.selected_objects))
        message = f"移动到世界原点对象 {moved} 个，跳过 {skipped} 个"
        self.report({'INFO'}, message)
        print(message)
        return {'FINISHED'}


class OBJECT_OT_move_objects_to_world_origin(bpy.types.Operator):
    bl_idname = "object.move_objects_to_world_origin_batch"
    bl_label = "Move Objects To World Origin"
    bl_description = "Move selected mesh objects to the world origin"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        moved, skipped = move_objects_to_world_origin(list(context.selected_objects))
        message = f"移动物体到世界中心 {moved} 个，跳过 {skipped} 个"
        self.report({'INFO'}, message)
        print(message)
        return {'FINISHED'}


def draw_autouv_settings(layout, settings):
    main_box = layout.box()
    main_box.label(text="主要参数", icon="SETTINGS")
    main_box.prop(settings, "resolution")
    main_box.prop(settings, "aspect")
    main_box.prop(settings, "density")
    main_box.prop(settings, "udims")
    main_box.prop(settings, "center")
    main_box.prop(settings, "global_pack")

    condition_box = main_box.box()
    condition_box.label(text="全局打包使用条件", icon="INFO")
    condition_box.label(
        text="使用条件：UDIM 数量必须为 1；开启世界尺寸 UV 仍可执行。"
    )
    condition_box.label(
        text="先按各 Mesh 原始相对尺寸不缩放打包，再统一归一化到单个 0-1 Tile。"
    )
    condition_box.label(
        text="世界尺寸 UV 会改变绝对纹素密度；仅 UDIM 大于 1 时跳过全局打包。"
    )
    status_row = condition_box.row()
    status_row.alert = bool(settings.global_pack and settings.udims > 1)
    status_row.label(text=_global_pack_status_text(settings))

    row = main_box.row(align=True)
    row.prop(settings, "separate")
    row.prop(settings, "normals")
    row = main_box.row(align=True)
    row.prop(settings, "overlap")
    row.prop(settings, "mirror")
    main_box.prop(settings, "worldscale")

    advanced_box = layout.box()
    advanced_box.prop(settings, "show_advanced", text="显示高级/调试参数")
    if not settings.show_advanced:
        return

    advanced_box.label(text="高级算法参数", icon="PREFERENCES")
    boolean_properties = (
        "suppress",
        "quad",
        "weld",
        "flat",
        "cone",
        "grids",
        "strip",
        "patch",
        "planes",
        "merge",
        "presmooth",
        "softunfold",
        "tubes",
        "junctions_debug",
        "extra_debug",
        "abf",
        "smooth",
        "repair_smooth",
        "repair",
        "square",
        "relax",
        "cut_debug",
        "stretch",
        "match",
        "packing",
        "validate",
    )
    for property_name in boolean_properties:
        advanced_box.prop(settings, property_name)

    numeric_properties = (
        "cone_ratio",
        "flatness",
        "merge_limit",
        "relax_iterations",
        "expand",
        "rasterization",
        "packing_iterations",
        "scale_to_fit",
    )
    for property_name in numeric_properties:
        advanced_box.prop(settings, property_name)


class VIEW3D_PT_modeling_toolbox(bpy.types.Panel):
    bl_label = "建模工具箱"
    bl_idname = "VIEW3D_PT_modeling_toolbox"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Tool'

    def draw(self, context):
        layout = self.layout
        transform_box = layout.box()
        transform_box.label(text="变换工具", icon='OBJECT_ORIGIN')
        transform_box.operator(
            "object.apply_all_transforms_selected_batch",
            text="对选中物体应用所有变换",
            icon='FILE_REFRESH',
        )

        origin_box = layout.box()
        origin_box.label(text="原点工具", icon='PIVOT_BOUNDBOX')
        origin_box.operator(
            "object.set_origin_to_lowest_center_batch",
            text="原点移到模型最低点中心",
            icon='TRIA_DOWN_BAR',
        )
        origin_box.operator(
            "object.set_origin_to_lowest_volume_center_batch",
            text="原点移到体积中心最低点",
            icon='SNAP_FACE_CENTER',
        )
        origin_box.operator(
            "object.set_origin_to_world_origin_batch",
            text="原点移到世界原点",
            icon='WORLD_DATA',
        )
        origin_box.operator(
            "object.move_objects_to_world_origin_batch",
            text="移动物体到世界中心",
            icon='EMPTY_ARROWS',
        )

        vegetation_box = layout.box()
        vegetation_box.label(text="植被处理", icon='OBJECT_DATA')
        vegetation_box.operator(
            "object.vegetation_normals_up",
            text="法线向上",
        )
        vegetation_box.operator(
            "object.vegetation_normals_outward",
            text="法线离心",
        )
        vegetation_box.label(text="Object Mode：选中物体的全部顶点")
        vegetation_box.label(text="Edit Mode：仅处理选中的顶点")

        uv_box = layout.box()
        uv_box.label(text="UV 工具", icon='UV_DATA')
        uv_box.operator(
            "object.autouv_ministry_of_flat",
            text="AutoUV（Ministry of Flat）",
            icon='UV_DATA',
        )
        draw_autouv_settings(uv_box, context.scene.modeling_toolbox_autouv)

        material_box = layout.box()
        material_box.label(text="材质工具", icon='MATERIAL')
        op = material_box.operator(
            "object.fix_fbx_material_names",
            text="修复材质序号（选中物体）",
            icon='MATERIAL',
        )
        op.selected_only = True
        op = material_box.operator(
            "object.fix_fbx_material_names",
            text="修复材质序号（全部物体）",
            icon='NODE_MATERIAL',
        )
        op.selected_only = False
        material_box.operator(
            "object.replace_all_materials_with_checkerboard",
            text="所有材质替换为棋盘格",
            icon='TEXTURE',
        )
        op = material_box.operator(
            "object.remove_unused_mesh_material_slots_batch",
            text="删除未使用材质槽（选中物体）",
            icon='TRASH',
        )
        op.selected_only = True
        op = material_box.operator(
            "object.remove_unused_mesh_material_slots_batch",
            text="删除未使用材质槽（全部物体）",
            icon='TRASH',
        )
        op.selected_only = False


classes = (
    MODELING_TOOLBOX_AddonPreferences,
    MODELING_TOOLBOX_PG_autouv_settings,
    OBJECT_OT_autouv_ministry_of_flat,
    OBJECT_OT_fix_fbx_material_names,
    OBJECT_OT_replace_all_materials_with_checkerboard,
    OBJECT_OT_remove_unused_mesh_material_slots,
    OBJECT_OT_apply_all_transforms_selected,
    OBJECT_OT_set_origin_to_lowest_center,
    OBJECT_OT_set_origin_to_lowest_volume_center,
    OBJECT_OT_set_origin_to_world_origin,
    OBJECT_OT_move_objects_to_world_origin,
    OBJECT_OT_vegetation_normals_up,
    OBJECT_OT_vegetation_normals_outward,
    VIEW3D_PT_modeling_toolbox,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.modeling_toolbox_autouv = bpy.props.PointerProperty(
        type=MODELING_TOOLBOX_PG_autouv_settings
    )


def unregister():
    if hasattr(bpy.types.Scene, "modeling_toolbox_autouv"):
        del bpy.types.Scene.modeling_toolbox_autouv
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
