# 发布构建脚本（Windows x64 · conda py312 环境）
#
# 一条命令重建全部发布产物：
#   pwsh -File release/build.ps1
#
# 依赖：
#   · conda 环境 py312（3.12.13）——**禁用 base**（3.13.9，缺 hypothesis）
#   · uv（构建后端 uv_build）
#   · PyInstaller 6.22.3（首次需装，见下方 INSTALL 段）
#
# 环境变量可覆盖（CI / 别的机器）：
#   TETRIS_PYTHON —— 解释器路径，默认 D:\miniconda3\envs\py312\python.exe
#   TETRIS_UV     —— uv 可执行文件，默认 uv

$ErrorActionPreference = 'Stop'

$Repo = Split-Path -Parent $PSScriptRoot
$Python = if ($env:TETRIS_PYTHON) { $env:TETRIS_PYTHON } else { 'D:\miniconda3\envs\py312\python.exe' }
$Uv = if ($env:TETRIS_UV) { $env:TETRIS_UV } else { 'uv' }

if (-not (Test-Path $Python)) { throw "找不到解释器：$Python" }

# INSTALL（首次 / 换机器时手动跑一次）：
#   uv pip install --python $Python pyinstaller==6.22.3

$Dist = Join-Path $Repo 'dist'
$Work = Join-Path $Repo 'build'

# ── 1. 非 editable 安装（ADR-009）────────────────────────────────────
# editable 的 .pth 在 --onefile 打包时偶发解析失败，所以先装一份真的。
Write-Host '[1/4] 非 editable 安装（ADR-009）' -ForegroundColor Cyan
& $Uv pip install --python $Python . --reinstall
if ($LASTEXITCODE -ne 0) { throw '非 editable 安装失败' }

# ── 2. sdist + wheel（uv build）──────────────────────────────────────
Write-Host '[2/4] 构建 sdist + wheel' -ForegroundColor Cyan
& $Uv build --out-dir $Dist
if ($LASTEXITCODE -ne 0) { throw 'uv build 失败' }

# ── 3. 两个 exe（PyInstaller）────────────────────────────────────────
Write-Host '[3/4] 构建 exe' -ForegroundColor Cyan
foreach ($Spec in @('tetris-cli', 'tetris-play')) {
    & $Python -m PyInstaller --clean --noconfirm --distpath $Dist --workpath $Work `
        (Join-Path $Repo "release\$Spec.spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败：$Spec" }
}

# ── 4. 产物清单 + 校验和 ─────────────────────────────────────────────
Write-Host '[4/4] 产物清单' -ForegroundColor Cyan
Get-ChildItem $Dist -File | Where-Object { $_.Extension -in '.exe', '.whl', '.gz' } |
    ForEach-Object {
        $hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash
        '{0,-52} {1,10}  {2}' -f $_.Name, $_.Length, $hash
    }
