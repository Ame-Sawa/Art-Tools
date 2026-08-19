# Blender: Project-Specific Analysis & SOP

## Architecture Summary

Blender is a 3D creation suite supporting modeling, animation, rendering,
compositing, and video editing. Its native `.blend` format is a custom binary
format. The CLI uses a JSON scene description that can generate Blender Python
(`bpy`) scripts for actual rendering.

```
┌──────────────────────────────────────────┐
│              Blender GUI                 │
│  ┌──────────┐ ┌──────────┐ ┌─────────┐  │
│  │ 3D View  │ │ Timeline │ │ Props   │  │
│  └────┬─────┘ └────┬─────┘ └────┬────┘  │
│       │             │            │        │
│  ┌────┴─────────────┴────────────┴─────┐ │
│  │       bpy (Blender Python API)      │ │
│  │  Full scripting access to all       │ │
│  │  objects, materials, modifiers      │ │
│  └─────────────────┬───────────────────┘ │
│                    │                      │
│  ┌─────────────────┴───────────────────┐ │
│  │     Render Engines                  │ │
│  │  Cycles | EEVEE | Workbench         │ │
│  └─────────────────────────────────────┘ │
└──────────────────────────────────────────┘
```

## CLI Strategy: JSON Scene + bpy Script Generation

Since `.blend` is binary, we maintain scene state in JSON and generate
complete `bpy` Python scripts that Blender can execute:

```bash
blender --background --python generated_script.py
```

### Core Domains

| Domain | Module | Key Operations |
|--------|--------|----------------|
| Scene | `scene.py` | Create, open, save, profiles, info |
| Objects | `objects.py` | Add primitives, transform, duplicate, remove |
| Materials | `materials.py` | Principled BSDF, color, metallic, roughness |
| Modifiers | `modifiers.py` | Subdivision, mirror, array, bevel, boolean |
| Lighting | `lighting.py` | Cameras (perspective/ortho), lights (point/sun/spot/area) |
| Animation | `animation.py` | Keyframes, frame range, FPS, interpolation |
| Render | `render.py` | Cycles/EEVEE settings, resolution, samples, output |
| Session | `session.py` | Undo/redo with deep-copy snapshots |

### Modifier Registry

8 modifiers with full parameter validation:
- `subdivision_surface`: levels, render_levels
- `mirror`: axis_x/y/z, use_bisect
- `array`: count, offset
- `bevel`: width, segments
- `solidify`: thickness, offset
- `decimate`: ratio, type
- `boolean`: operation (union/intersect/difference), object
- `smooth`: factor, iterations

### Render Presets

7 presets covering Cycles, EEVEE, and Workbench:
- `cycles_default`: 128 samples, denoising
- `cycles_high`: 4096 samples, denoising, transparent film
- `cycles_preview`: 32 samples, fast preview
- `eevee_default`: 64 samples
- `eevee_high`: 256 samples, bloom, AO, SSR
- `eevee_preview`: 16 samples
- `workbench`: Flat/studio lighting preview

### Rendering Gap: Low Risk

Blender's Python API (`bpy`) provides complete access to all functionality.
The generated scripts create the exact scene described in JSON, then render.
No translation gap — bpy is the native API.

## Export: bpy Script Generation

The `render execute` command generates a complete Python script:
1. Creates all objects with correct mesh types and transforms
2. Creates and assigns materials with all Principled BSDF properties
3. Adds and configures modifiers
4. Sets up cameras and lights
5. Configures animation keyframes
6. Sets render engine and settings
7. Renders to output file

Generated scripts are validated as syntactically correct Python in tests.

## Windows 初始化

harness 内包含一个可以双击运行的 Windows 初始化脚本：

```text
initialize_windows.bat
```

脚本会在当前 `agent-harness` 目录内创建虚拟环境、安装本地 CLI、检查
Python/pip 和 Blender，并将本机 Blender 路径写入 `.env.local`。它不会安装或
修改系统 Python、系统 pip、PATH 或 Windows 注册表。

双击 `initialize_windows.bat` 后，按提示输入以下任一种 Blender 信息：

- Blender 安装目录：`H:\Blender5.1`
- Blender 可执行文件：`H:\Blender5.1\blender.exe`

输入安装目录时，脚本会自动使用目录下的 `blender.exe`。也可以将目录或
可执行文件路径作为参数传入：

```powershell
initialize_windows.bat "H:\Blender5.1"
initialize_windows.bat "H:\Blender5.1\blender.exe"
```

初始化脚本要求 Python 3.10+，并且 `python -m pip` 可用。Blender 必须已经安装。
脚本生成的本地文件都位于 harness 目录内：

```text
.venv\
.tmp\
.env.local
```

`.tmp` 仅在创建或修复虚拟环境、安装 CLI 时按需创建。

重复运行初始化脚本时，会优先复用 `.env.local` 中仍然有效的 Blender 路径，
并跳过已经可用的虚拟环境、CLI 安装和重复验证。

初始化完成后，无需激活虚拟环境即可调用 CLI：

```powershell
.\.venv\Scripts\cli-anything-blender.exe --help
.\.venv\Scripts\cli-anything-blender.exe scene profiles
```

## Uniform UV / AutoUV Windows GUI

如果更适合使用文件选择器和拖放，可以双击 harness 目录中的：

```text
launch_blender_uv_gui.bat
```

该 GUI 只支持批量选择 FBX，按“参数 → 输出方式 → 批次文件”的顺序操作，选择
Blender Uniform UV 或 Ministry of Flat AutoUV 后统一调用 `fbx auto-uv --algorithm ...`
完成导出与 FBX round-trip 校验。主窗口只显示待处理文件数量；批量导入窗口支持
多选文件、删除列表项和预览预计输出路径，批次内使用同一种算法和参数。AutoUV
模式还可以选择 `UnWrapConsole3.exe` 并设置常用的分辨率、UDIM、硬边分离、重叠和
世界尺度参数。首次启动会在虚拟环境中安装 PySide6；默认算法为 AutoUV，默认输出
模式为覆盖源文件，也可以添加固定 `_uv`/`_autouv` 后缀或选择批量输出目录。
