# FBX Modifier Tool

独立的 Windows FBX 修改工具，使用 PySide6 提供 GUI，使用 Autodesk FBX
Python SDK 读取和写出 FBX，并支持无界面的差异检查和 DCC handoff 处理。

本目录只保存源码、测试和开发/打包脚本。虚拟环境、构建产物、发布目录以及
Autodesk FBX SDK wheel 不提交到仓库。

## 平台和依赖

- Windows
- Python 3.10 64-bit；Autodesk 官方 Windows Python FBX 发行版面向此版本
- PySide6、PyInstaller 和 pytest（由 `init_venv.bat` 安装）
- `tools/DccExportCommon/` 中的共享 handoff 契约模块

## 开发环境

在本目录执行基础初始化：

```bat
init_venv.bat
```

该脚本会创建 `.venv`、安装 Python 依赖并以可编辑模式安装当前工具包。
如果已经下载了匹配的 FBX SDK wheel，也可以一次完成全部初始化：

```bat
init_venv.bat "C:\path\to\fbx-2020.3.9-cp310-none-win_amd64.whl"
```

Autodesk FBX SDK 官方下载页：

<https://aps.autodesk.com/developer/overview/fbx-sdk>

如果基础初始化时没有提供 wheel，之后仍可单独安装：

```bat
install_fbx_sdk_wheel.bat "C:\path\to\fbx-2020.3.9-cp310-none-win_amd64.whl"
```

如果使用的 Python 版本不是 SDK wheel 支持的版本，需要准备对应版本的 SDK
wheel。SDK 安装成功后，脚本会执行导入探针。

## 启动和命令行入口

启动源码 GUI：

```bat
run_dev.bat
```

也可以手动执行：

```powershell
call .venv\Scripts\activate.bat
python launcher.py
```

项目入口：

- `fbx-modifier-tool`：启动 GUI；
- `fbx-diff-tool LEFT.fbx RIGHT.fbx`：比较两个 FBX 的结构摘要，可用 `--json` 输出 JSON；
- `python run_handoff.py --source ... --output ... --mapping-json ...`：执行无界面 DCC handoff；
- `python launcher.py --handoff ...`：通过统一 launcher 执行 handoff。

## 当前功能

- 扫描文件夹第一层的 `.fbx`/`.FBX` 文件并从列表导入；
- Mesh 和 Material 重命名，Mesh 名称自动补充 `Mesh_` 前缀；
- Mesh / Material 快捷设为 `Main`；
- 可选将按顶点位置平均后的平滑法线写入顶点色 RGB，供描边 shader 使用；
- 以原文件为默认导出路径，并执行覆盖工作流；
- 通过结构摘要检查节点、Mesh、材质槽、UV、法线和变形器相关差异。

## 测试

在仓库根目录执行：

```powershell
python -m pytest tools/FbxModifierTool/tests -q
python -m pytest tools/DccExportCommon/tests -q
```

真实 FBX 读写测试需要先安装 Autodesk FBX SDK。当前服务测试包含 SDK API
替身，因此可以在未安装 SDK 的环境中验证大部分逻辑。

## 重新打包

准备好 `.venv` 和匹配的 FBX SDK 后，在本目录执行：

```bat
build_onedir.bat
```

脚本会生成 `dist/FbxModifierTool/`。该目录是本地发布产物，已被仓库忽略。

## 目录结构

```text
tools/FbxModifierTool/
  src/fbx_modifier_tool/   # GUI、FBX 服务、差异 CLI 和 handoff
  tests/                   # 工具测试
  launcher.py              # GUI/handoff 统一启动器
  run_handoff.py           # handoff 独立入口
  pyproject.toml
  requirements.txt
  *.bat                    # 开发、SDK 安装和打包脚本

tools/DccExportCommon/
  asset_contract.py        # DCC 与 FBX handoff 共享契约
```
