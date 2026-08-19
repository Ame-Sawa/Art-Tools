# Blender UV Tools GUI

这是 `cli-anything-blender` 的 Windows 桌面前端，统一调用 `fbx auto-uv`
命令的 Blender Uniform UV 和 Ministry of Flat AutoUV 算法。GUI 不重复实现
FBX 或 UV 算法，只通过 JSON 模式启动现有 CLI。

## 启动

在 `agent-harness` 已初始化后，双击：

```text
launch_blender_uv_gui.bat
```

或者在 harness 虚拟环境中运行：

```powershell
python -m pip install -e ".[gui]"
python -m cli_anything.blender_gui
```

首次运行可通过“选择 exe…”指定 `blender.exe`。路径会保存到当前用户的
`%APPDATA%\ArtTools\uniform_uv_gui.json`。

## 固定操作顺序

GUI 固定采用“参数 → 输出方式 → 批次文件”的操作顺序。主窗口不提供单文件
输入或单文件输出路径，只显示当前待处理的 FBX 数量；最终处理按钮始终位于主窗口。

算法下拉框提供两种批处理方式：

- Blender Uniform UV：使用现有 Smart UV 角度搜索；
- Ministry of Flat AutoUV：覆盖当前活动 UV Map，并提供常用 AutoUV 参数。

AutoUV 默认启用 v2“拓扑风险预筛选”。安全拓扑筛选提供三个等级：关闭、标准（仅
跳过高风险，默认）和严格（跳过中风险与高风险）。它会在调用外部展开程序前检查每个唯一
Mesh datablock，重点评分开放边界比例、N-gon 密度、大边数面，以及开放边界与
N-gon 的组合；高风险文件会直接标记为“跳过”，不会生成输出，也不会启动
UnWrapConsole3.exe。结果会显示评分版本、最高风险 Mesh 和触发规则。筛选等级可在
AutoUV 参数中选择，评分规则固定，不提供阈值编辑。Uniform UV 模式不使用此筛选。

AutoUV 始终将导入后的原始网格拓扑交给 Ministry of Flat，不进行三角化 fallback
或失败重试。返回 OBJ 通过拓扑校验后，UV 直接写回当前活动 UV Map。每个 AutoUV FBX
的总处理时间固定不超过 10 秒，包含 Blender 导入、
外部程序、FBX 导出和 round-trip 校验。超过时该文件会跳过，不覆盖输出文件，
并在日志和结果中显示超时阶段。

默认算法为 Ministry of Flat AutoUV。选择输出方式后点击“选择批处理文件…”会
打开多选 FBX 窗口，可以通过“从文件夹导入”一次选择多个文件，也可以删除列表
中的文件。窗口使用表格显示文件名、完整输入路径和预计输出路径，每个文件占一行。
点击确认后，文件批次保存到主窗口；再次点击按钮可以重新编辑。一个批次使用同一
种算法和参数，执行结果会按文件显示成功或失败状态。

输出区域提供三个模式：

- “覆盖源文件”：处理成功并通过校验后替换源 FBX；
- “添加固定后缀”：Uniform UV 在源文件旁边生成 `<name>_uv.fbx`，AutoUV 生成 `<name>_autouv.fbx`；
- “输出到指定目录”：选择一个目录，输出保留每个源文件的原始文件名。

批量模式不扫描文件夹，只处理用户在文件对话框中多选的 `.fbx` 文件。输出文件
已存在时，GUI 会在启动 CLI 前请求确认并在确认后使用 `--overwrite`。批次文件列表
只保存在当前 GUI 会话中，不写入设置文件。处理时进度条按已完成文件数更新，
“运行设置”中的并行任务数默认是 2，每个任务使用独立的 Blender 和 Ministry of Flat
进程；设置为 1 可恢复串行模式。并行任务完成顺序可以不同，但表格和最终结果仍按输入
顺序对应文件。每个文件的处理耗时显示在详情中。
处理进度和最终结果现在合并在一个只读表格中，每个 FBX 固定一行，显示序号、状态、
文件名、输出路径和详情。输入完整路径和输出完整路径通过鼠标悬停 Tooltip 查看；成功、
失败、跳过、处理中和普通日志使用不同颜色。表格支持整行选择和横向滚动。部分失败或
跳过会继续处理剩余文件，并在同一张表中显示成功、失败、跳过三类逐文件汇总。
AutoUV 单文件超过 10 秒时会尝试清理外部程序及其子进程；日志会显示进程树清理
成功或失败，避免超时的 UnWrapConsole3.exe 残留影响后续批处理。

AutoUV 的 EXE 路径和常用参数会保存到 `%APPDATA%\ArtTools\uniform_uv_gui.json`，
并兼容旧版 Uniform UV 设置。
