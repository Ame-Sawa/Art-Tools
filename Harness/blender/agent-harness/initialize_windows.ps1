[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$BlenderPath
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Write-Section {
    param([string]$Message)

    Write-Host ""
    Write-Host "=== $Message ===" -ForegroundColor Cyan
}

function Invoke-CheckedNative {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [string[]]$Arguments = @(),

        [Parameter(Mandatory = $true)]
        [string]$Description,

        [switch]$CaptureOutput
    )

    $lines = @(& $FilePath @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    $output = ($lines | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine

    if ($exitCode -ne 0) {
        $details = if ([string]::IsNullOrWhiteSpace($output)) {
            "没有收到原始错误输出。"
        } else {
            $output.Trim()
        }
        throw "$Description 失败，退出代码：$exitCode。`n原始错误输出：`n$details"
    }

    if ($CaptureOutput) {
        return $output
    }

    foreach ($line in $lines) {
        Write-Host $line
    }
}

function Invoke-PythonChecked {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Python,

        [string[]]$Arguments = @(),

        [Parameter(Mandatory = $true)]
        [string]$Description,

        [switch]$CaptureOutput
    )

    $allArguments = @($Python.PrefixArgs) + @($Arguments)
    return Invoke-CheckedNative `
        -FilePath $Python.Path `
        -Arguments $allArguments `
        -Description $Description `
        -CaptureOutput:$CaptureOutput
}

function Get-PythonVersion {
    param([string]$VersionOutput)

    if ($VersionOutput -notmatch "Python\s+(\d+)\.(\d+)(?:\.(\d+))?") {
        throw "无法识别 Python 版本：$VersionOutput"
    }

    $patch = if ($Matches[3]) { [int]$Matches[3] } else { 0 }
    return [version]::new([int]$Matches[1], [int]$Matches[2], $patch)
}

function Resolve-UsablePython {
    $candidates = @()

    $pyCommand = Get-Command "py" -ErrorAction SilentlyContinue
    if ($null -ne $pyCommand) {
        $candidates += [pscustomobject]@{
            Path       = $pyCommand.Path
            PrefixArgs = @("-3")
            Label      = "py -3"
        }
    }

    $pythonCommand = Get-Command "python" -ErrorAction SilentlyContinue
    if ($null -ne $pythonCommand) {
        $candidates += [pscustomobject]@{
            Path       = $pythonCommand.Path
            PrefixArgs = @()
            Label      = "python"
        }
    }

    if ($candidates.Count -eq 0) {
        throw "未找到 Python。请先安装 Python 3.10 或更高版本，再重新运行初始化脚本。"
    }

    $failures = @()
    foreach ($candidate in $candidates) {
        try {
            $versionOutput = Invoke-PythonChecked `
                -Python $candidate `
                -Arguments @("--version") `
                -Description "检查 $($candidate.Label)" `
                -CaptureOutput
            $version = Get-PythonVersion $versionOutput

            if ($version -lt [version]::new(3, 10, 0)) {
                throw "$($candidate.Label) 检测到 Python $version；需要 Python 3.10 或更高版本。"
            }

            [void](Invoke-PythonChecked `
                -Python $candidate `
                -Arguments @("-m", "pip", "--version") `
                -Description "检查 $($candidate.Label) 的 pip" `
                -CaptureOutput)

            return [pscustomobject]@{
                Path       = $candidate.Path
                PrefixArgs = $candidate.PrefixArgs
                Label      = $candidate.Label
                Version    = $version
            }
        } catch {
            $failures += "$($candidate.Label): $($_.Exception.Message)"
        }
    }

    throw "未找到可用的 Python 安装。`n$($failures -join [Environment]::NewLine)"
}

function Resolve-BlenderExecutable {
    param([string]$RequestedPath)

    $candidate = $RequestedPath
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        $candidate = Read-Host "请输入 Blender 可执行文件路径、无扩展名路径、安装目录，或 PATH 中的 blender 命令"
    }

    $candidate = $candidate.Trim().Trim('"')
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        throw "必须提供 Blender 路径或命令名。"
    }

    $resolvedCandidates = [System.Collections.Generic.List[string]]::new()

    function Add-ResolvedBlenderCandidate {
        param([string]$Path)

        if ([string]::IsNullOrWhiteSpace($Path)) {
            return
        }

        try {
            $resolved = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
            if ((Test-Path -LiteralPath $resolved -PathType Leaf) -and
                -not $resolvedCandidates.Contains($resolved)) {
                $resolvedCandidates.Add($resolved)
            }
        } catch {
            # This candidate is optional; the caller will try the next form.
        }
    }

    # 支持完整的 blender.exe 路径。
    Add-ResolvedBlenderCandidate $candidate

    # 兼容输入去掉 .exe 的可执行文件路径。
    if ([System.IO.Path]::GetExtension($candidate) -ne ".exe") {
        Add-ResolvedBlenderCandidate "$candidate.exe"
    }

    # 支持输入 Blender 安装目录，并自动补全 blender.exe。
    if (Test-Path -LiteralPath $candidate -PathType Container) {
        Add-ResolvedBlenderCandidate (Join-Path $candidate "blender.exe")
    }

    # 额外兼容 PATH 中的 blender 命令名。
    if ($candidate -notmatch "[\\/:]" -and
        [System.IO.Path]::GetExtension($candidate) -eq "") {
        $command = Get-Command $candidate -CommandType Application -ErrorAction SilentlyContinue
        if ($null -ne $command) {
            Add-ResolvedBlenderCandidate $command.Path
        }
        $commandWithExtension = Get-Command "$candidate.exe" -CommandType Application -ErrorAction SilentlyContinue
        if ($null -ne $commandWithExtension) {
            Add-ResolvedBlenderCandidate $commandWithExtension.Path
        }
    }

    if ($resolvedCandidates.Count -eq 0) {
        throw "未找到 Blender：$candidate。可输入完整 exe 路径、去掉 .exe 的路径、安装目录，或 PATH 中的 blender 命令。"
    }

    return $resolvedCandidates[0]
}

function Update-LocalEnvFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$HarnessRoot,

        [Parameter(Mandatory = $true)]
        [string]$ResolvedBlenderPath
    )

    $envFile = Join-Path $HarnessRoot ".env.local"
    $key = "CLI_ANYTHING_BLENDER_PATH"
    $replacement = "$key=$ResolvedBlenderPath"
    $lines = [System.Collections.Generic.List[string]]::new()
    $replaced = $false

    if (Test-Path -LiteralPath $envFile -PathType Leaf) {
        foreach ($line in [System.IO.File]::ReadAllLines($envFile)) {
            if ($line -match "^\s*$([regex]::Escape($key))\s*=") {
                if (-not $replaced) {
                    $lines.Add($replacement)
                    $replaced = $true
                }
                continue
            }
            $lines.Add($line)
        }
    }

    if (-not $replaced) {
        $lines.Add($replacement)
    }

    # Write UTF-8 without BOM. The Python backend reads this file as utf-8 and
    # must see the key name without a leading U+FEFF character.
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $content = ($lines -join [Environment]::NewLine) + [Environment]::NewLine
    [System.IO.File]::WriteAllText($envFile, $content, $utf8NoBom)

    return $envFile
}

$harnessRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$venvRoot = Join-Path $harnessRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$venvCli = Join-Path $venvRoot "Scripts\cli-anything-blender.exe"
$tempRoot = Join-Path $harnessRoot ".tmp"
$pipCacheRoot = Join-Path $harnessRoot ".pip-cache"

$previousTemp = $env:TEMP
$previousTmp = $env:TMP
$previousPipCache = $env:PIP_CACHE_DIR

try {
    Set-Location -LiteralPath $harnessRoot
    [void](New-Item -ItemType Directory -Force -Path $tempRoot)
    [void](New-Item -ItemType Directory -Force -Path $pipCacheRoot)

    # Keep child-process temporary and pip-cache writes inside this harness.
    $env:TEMP = $tempRoot
    $env:TMP = $tempRoot
    $env:PIP_CACHE_DIR = $pipCacheRoot

    Write-Section "检查 Python 和 pip"
    $python = Resolve-UsablePython
    Write-Host "使用 $($python.Label)：Python $($python.Version)"

    Write-Section "检查 Blender"
    $resolvedBlender = Resolve-BlenderExecutable $BlenderPath
    $blenderVersionOutput = Invoke-CheckedNative `
        -FilePath $resolvedBlender `
        -Arguments @("--version") `
        -Description "检查 Blender" `
        -CaptureOutput
    $blenderVersionLine = ($blenderVersionOutput -split "\r?\n" | Select-Object -First 1).Trim()
    $blenderVersion = $blenderVersionLine -replace "^Blender\s+", ""
    Write-Host "检测到 Blender 版本：$blenderVersion"

    Write-Section "创建或复用本地虚拟环境"
    if (-not (Test-Path -LiteralPath $venvRoot -PathType Container)) {
        Invoke-PythonChecked `
            -Python $python `
            -Arguments @("-m", "venv", $venvRoot) `
            -Description "创建本地虚拟环境" `
            -CaptureOutput
    } elseif (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw "已有 .venv 目录不完整，缺少：$venvPython。请手动删除 .venv 后重新运行初始化脚本。"
    } else {
        Write-Host "复用已有虚拟环境：$venvRoot"
    }

    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw "虚拟环境创建失败，未生成：$venvPython"
    }

    try {
        [void](Invoke-CheckedNative `
            -FilePath $venvPython `
            -Arguments @("-m", "pip", "--version") `
            -Description "检查虚拟环境中的 pip" `
            -CaptureOutput)
    } catch {
        Write-Host ".venv 中缺少 pip，正在仅在 .venv 内补充 pip……" -ForegroundColor Yellow
        [void](Invoke-CheckedNative `
            -FilePath $venvPython `
            -Arguments @("-m", "ensurepip", "--upgrade") `
            -Description "在虚拟环境中补充 pip" `
            -CaptureOutput)
    }

    Write-Section "安装本地 Blender CLI"
    [void](Invoke-CheckedNative `
        -FilePath $venvPython `
        -Arguments @("-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir", "-e", $harnessRoot) `
        -Description "安装 cli-anything-blender" `
        -CaptureOutput)

    Write-Section "写入本地 Blender 配置"
    $envFile = Update-LocalEnvFile `
        -HarnessRoot $harnessRoot `
        -ResolvedBlenderPath $resolvedBlender
    Write-Host "已写入配置：$envFile"

    Write-Section "验证已安装的 CLI"
    if (-not (Test-Path -LiteralPath $venvCli -PathType Leaf)) {
        throw "未生成 CLI 入口：$venvCli"
    }

    [void](Invoke-CheckedNative `
        -FilePath $venvCli `
        -Arguments @("--help") `
        -Description "验证 cli-anything-blender --help" `
        -CaptureOutput)
    [void](Invoke-CheckedNative `
        -FilePath $venvCli `
        -Arguments @("scene", "profiles") `
        -Description "验证场景配置预设" `
        -CaptureOutput)

    Write-Host ""
    Write-Host "初始化完成。" -ForegroundColor Green
    Write-Host "Harness 目录：$harnessRoot"
    Write-Host "虚拟环境：$venvRoot"
    Write-Host "CLI 路径：$venvCli"
    Write-Host "Blender 路径：$resolvedBlender"
    Write-Host ""
    Write-Host "CLI 使用方式："
    Write-Host "  `"$venvCli`" --help"
    exit 0
} catch {
    Write-Host ""
    Write-Host "[错误] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally {
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
    $env:PIP_CACHE_DIR = $previousPipCache
}
