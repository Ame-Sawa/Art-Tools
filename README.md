# Art-Tools

Art-Tools 是面向 Blender 资产处理与程序化工作流的工具集，覆盖建模整理、UV 展开、
FBX 批处理、场景编辑、渲染和预览。

项目同时服务于两类使用者：

- 艺术家与技术美术：使用 Blender 插件和 Windows GUI 完成交互式处理；
- 开发者与 Agent：使用带有 JSON 输出、退出码和结果校验的 CLI 执行可复现任务。

## 组件

| 组件 | 主要功能 | 入口 |
| --- | --- | --- |
| `cli-anything-blender` | 场景编辑、材质和修改器管理、动画、渲染、预览、FBX 处理、Smart UV 和 AutoUV | `Harness/blender/agent-harness` |
| Blender UV Tools GUI | 通过文件选择器批量执行 Uniform UV 和 Ministry of Flat AutoUV，查看逐文件结果 | `Harness/blender/agent-harness/launch_blender_uv_gui.bat` |
| 建模工具箱 | 变换、原点、植被法线、材质整理、棋盘格诊断和 AutoUV | `tools/modeling_toolbox-1.4.0.zip` |
| FBX Modifier Tool | FBX GUI 修改、结构差异检查和 DCC handoff | `tools/FbxModifierTool` |

### CLI

CLI 使用 JSON 场景项目保存结构化状态，支持 headless Blender 渲染和真实预览。FBX
工具支持单文件和批处理，并对 UV 处理结果执行 round-trip 校验。适合 Agent、批处理
脚本和需要稳定输出的生产流水线。

### UV Tools GUI

GUI 是 CLI 的 Windows 前端，不重复实现 FBX 或 UV 算法。它支持多选 FBX、输出到新文件
或目录、覆盖前确认、并行任务、超时控制、AutoUV 拓扑风险筛选、UDIM 和 UV 归一化。

### 建模工具箱

插件面板位于 `3D View → N → Tool → 建模工具箱`，包含：

- 变换和原点处理；
- 世界空间“法线向上”和“法线离心”；
- Ministry of Flat AutoUV、UDIM、跨 Mesh 合并和 UV 归一化；
- FBX 材质序号修复、棋盘格材质诊断和未使用材质槽清理。

AutoUV 会校验外部程序返回的几何拓扑，失败时回滚 UV 修改；`UDIM=1` 支持合并处理和
归一化，`UDIM>1` 保留 UDIM 坐标。插件不会使用 Blender `Pack Islands`，也不会对源网格
执行三角化 fallback。

### FBX Modifier Tool

`tools/FbxModifierTool` 是独立的 Windows/Python 工具，提供 PySide6 GUI、FBX 结构差异
CLI，以及供 Max/Maya 使用的无界面 handoff CLI。它依赖 `tools/DccExportCommon` 中的
共享命名和兼容性契约。

## 安装

### CLI 和 GUI（Windows）

运行初始化脚本：

```bat
Harness\blender\agent-harness\initialize_windows.bat
```

脚本要求 Python 3.10+ 和 `pip`，会在 harness 内创建 `.venv`、安装 CLI，并将本机
Blender 路径写入 `.env.local`。也可以传入 Blender 安装目录或可执行文件：

```bat
Harness\blender\agent-harness\initialize_windows.bat "C:\Program Files\Blender Foundation\Blender 5.1"
```

初始化完成后，双击 `launch_blender_uv_gui.bat` 可启动 GUI。首次启动会安装 PySide6。

CLI 的完整调用方式、Agent 约定和参数参考见：

- `.agents/skills/cli-anything-blender/SKILL.md`
- `.agents/skills/cli-anything-blender/references/command-reference.md`

### 建模工具箱

Blender 5.1+ 中打开 Preferences 的 Extensions/Add-ons 安装入口，选择 **Install from
Disk**，安装 `tools/modeling_toolbox-1.4.0.zip`，然后启用“建模工具箱”。插件内置
Ministry of Flat；如需使用其他版本，可在插件偏好设置中指定 `UnWrapConsole3.exe`
所在目录。

### FBX Modifier Tool（Windows）

在 `tools/FbxModifierTool` 中运行：

```bat
init_venv.bat "C:\path\to\fbx-2020.3.9-cp310-none-win_amd64.whl"
run_dev.bat
```

源码包不包含 Autodesk FBX SDK wheel、虚拟环境或 `dist` 发布目录。wheel 必须与本机
Python 版本和架构匹配。官方 SDK 下载页：
<https://aps.autodesk.com/developer/overview/fbx-sdk>。工具的完整命令行、测试和打包说明见
`tools/FbxModifierTool/README.md`。

## 环境与依赖

- CLI harness：Python 3.10+；渲染、预览和 FBX 处理需要 Blender；
- UV Tools GUI：CLI harness、Blender 和 PySide6；
- 建模工具箱：Blender 5.1+；
- FBX Modifier Tool：Windows、Python 3.10 64-bit、PySide6，以及 Autodesk FBX Python SDK；
- `.env.local` 为机器本地配置，不应提交到版本库；模板位于
  `Harness/blender/agent-harness/.env.example`。

## 项目结构

```text
Art-Tools/
├── Harness/blender/agent-harness/       # Blender CLI harness 和 Windows GUI
│   ├── cli_anything/blender/            # CLI、核心模块、测试和内置第三方程序
│   └── cli_anything/blender_gui/        # UV 批处理 GUI
├── tools/modeling_toolbox/              # Blender 插件源码
├── tools/modeling_toolbox-1.4.0.zip     # 插件发布包
├── tools/FbxModifierTool/               # FBX GUI、CLI 和 handoff 源码
├── tools/DccExportCommon/               # DCC/FBX 共享契约模块
└── .agents/skills/cli-anything-blender/ # Agent skill 和 CLI 参数参考
```

## 开发与许可

CLI harness 的 Python 包 metadata 声明为 MIT；建模工具箱 manifest 声明为
GPL-3.0-or-later。Ministry of Flat 相关文件的许可和再分发要求见各自的
`THIRD_PARTY_NOTICES.txt`。

在 harness 目录安装开发依赖并运行测试：

```powershell
python -m pip install -e ".[dev]"
python -m pytest cli_anything/blender/tests -v
python -m pytest cli_anything/blender_gui/tests -v
python -m pytest tools/FbxModifierTool/tests -q
python -m pytest tools/DccExportCommon/tests -q
```

FBX Modifier Tool 的本地构建产物、日志和虚拟环境不会提交；真实 FBX 读写验证需要先
安装 Autodesk FBX SDK。SDK 的许可证和再分发条件以 Autodesk 提供的版本为准。
