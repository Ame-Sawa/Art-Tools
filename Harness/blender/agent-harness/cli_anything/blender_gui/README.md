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
的处理时间受“Blender 总超时”控制，外部 UnWrapConsole3.exe 受“外部程序超时”控制，
两项都包含在 GUI 的可配置运行设置中。超过时该文件会跳过，不覆盖输出文件，并在日志
和结果中显示超时阶段。

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
“运行设置”中的并行任务数默认是 2，最多可设置为 50。每个任务使用独立的 Blender 和 Ministry of Flat
进程；设置为 1 可恢复串行模式。并行任务完成顺序可以不同，但表格和最终结果仍按输入
顺序对应文件。每个文件的处理耗时显示在详情中。
处理进度和最终结果现在合并在一个只读表格中，每个 FBX 固定一行，显示序号、状态、
文件名、输出路径和人类可读的简短详情。输入完整路径、输出完整路径和原始技术错误
通过鼠标悬停 Tooltip 查看；成功、失败、跳过、处理中和普通日志使用不同颜色。表格
支持整行选择和横向滚动。部分失败或跳过会继续处理剩余文件，并在同一张表中显示
成功、失败、跳过和取消四类逐文件汇总。

主窗口只保留算法参数、输出方式、批次文件和运行控制，不显示日志表格。窗口初始高度
根据当前可见参数内容自动匹配。点击“打开日志窗口”可打开独立的非模态日志与结果表格，
批处理期间会实时追加易读的开始、完成、失败、跳过、取消和汇总信息；窗口还提供复制
日志按钮。原始技术诊断仍可通过详情单元格的 Tooltip 查看。

AutoUV 单文件超过“Blender 总超时”或外部程序超过“外部程序超时”时会尝试清理外部程序
及其子进程；日志会显示进程树清理成功或失败，避免超时的 UnWrapConsole3.exe 残留影响
后续批处理。

点击“取消”时，GUI 会先写入本次任务专用的取消标记文件，等待 CLI 主动停止排队任务
并清理当前 Blender/AutoUV 进程。最多等待 5 秒；如果 CLI 仍未退出，GUI 才会使用
`taskkill /T /F` 强制清理整个 CLI 子进程树。已经成功提交的文件会保留，正在处理和
尚未开始的文件会显示为“已取消”。异常强杀留下的 Harness 临时运行目录会在后续启动
时自动清理，不会扫描或删除用户输入输出目录中的普通 FBX 文件。

AutoUV 的 EXE 路径和常用参数会保存到 `%APPDATA%\ArtTools\uniform_uv_gui.json`，
并兼容旧版 Uniform UV 设置。AutoUV 参数中的“跨 Mesh 合并调用”和“UV 归一化”默认开启，
且会保存到同一设置文件；旧版 `global_pack` 键会作为合并开关兼容读取。

当 `UDIM=1` 时，跨 Mesh 合并调用会在每个 FBX 内将唯一 Mesh 合并为一个临时 OBJ，
一次调用 Ministry of Flat，再按 Loop 映射写回。UV 归一化使用统一的均匀变换，
不会使用 Blender Pack Islands。`UDIM>1` 时会自动逐 Mesh 调用并跳过归一化；开启世界
尺寸 UV 仍可归一化，但界面会提示绝对纹素密度可能改变。GUI 只生成 CLI 参数，
不会重复实现 FBX 或 UV 算法。

Blender 总超时默认 300 秒，外部程序超时默认 120 秒，均可在运行设置中修改，不使用
固定的 10 秒硬上限。
