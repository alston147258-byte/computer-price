# 在本机查看最新数据：从 GitHub 拉取云端任务刚提交的数据 → 生成网页 → 用浏览器打开
# 不用等每天 19:00 的网站发布；云端重试任务每 30 分钟提交一次数据。
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$git = Get-ChildItem "$env:LOCALAPPDATA\GitHubDesktop\app-*\resources\app\git\cmd\git.exe" -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
if (-not $git) {
    $g = Get-Command git -ErrorAction SilentlyContinue
    if ($g) { $git = $g.Source }
}

Write-Host '1/3 从 GitHub 拉取最新数据...' -ForegroundColor Cyan
if ($git) {
    & $git pull --ff-only
    if ($LASTEXITCODE -ne 0) { Write-Host '拉取失败，下面显示的是本机已有的数据。' -ForegroundColor Yellow }
} else {
    Write-Host '没找到 git（GitHub Desktop），跳过拉取。' -ForegroundColor Yellow
}

Write-Host '2/3 生成网页...' -ForegroundColor Cyan
python scraper\build_site.py

Write-Host '3/3 打开网页' -ForegroundColor Cyan
Start-Process (Join-Path $root 'site\index.html')

Write-Host ''
if (Test-Path README.md) {
    Get-Content README.md -Encoding UTF8 | Where-Object { $_ -notmatch '^## 覆盖' } | Select-Object -First 32
}

