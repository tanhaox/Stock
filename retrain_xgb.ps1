<#
  AlphaFlow XGBoost V3 重训脚本
  用法: .\retrain_xgb.ps1

  步骤:
    1. 备份当前模型到 models/backups/
    2. 运行训练 (统一特征管线, 48维)
    3. 验证 meta 一致性
#>
$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$backend = Join-Path $root "backend"
$models  = Join-Path $backend "models"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  AlphaFlow XGBoost V3 Retrain" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# ── Step 1: 备份当前模型 ──
$backupDir = Join-Path $models "backups"
if (-not (Test-Path $backupDir)) {
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
}
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$modelFile = Join-Path $models "alphaflow_xgb.json"
$metaFile  = Join-Path $models "alphaflow_xgb_meta.json"

if (Test-Path $modelFile) {
    $backupModel = Join-Path $backupDir "alphaflow_xgb_$ts.json"
    Copy-Item $modelFile $backupModel
    Write-Host "[OK] Model backed up -> $backupModel" -ForegroundColor Green
}
if (Test-Path $metaFile) {
    $backupMeta = Join-Path $backupDir "alphaflow_xgb_meta_$ts.json"
    Copy-Item $metaFile $backupMeta
    Write-Host "[OK] Meta backed up  -> $backupMeta" -ForegroundColor Green
}

# ── Step 2: 运行训练 ──
Write-Host "`n[Step 2] Running training..." -ForegroundColor Yellow
Push-Location $backend
try {
    python -m scripts.alphaflow_train_v2
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] Training exited with code $LASTEXITCODE" -ForegroundColor Red
        exit 1
    }
} finally {
    Pop-Location
}

# ── Step 3: 验证 meta ──
Write-Host "`n[Step 3] Verifying meta..." -ForegroundColor Yellow
if (-not (Test-Path $metaFile)) {
    Write-Host "[FAIL] Meta file not found after training" -ForegroundColor Red
    exit 1
}

$meta = Get-Content $metaFile -Raw | ConvertFrom-Json
$featCount = $meta.features
$featHash  = $meta.feature_hash

Write-Host "  Version:     $($meta.version)"
Write-Host "  Features:    $featCount"
Write-Host "  Hash:        $featHash"
Write-Host "  AUC:         $($meta.test_auc)"
Write-Host "  Training:    $($meta.training_date)"

if ($featCount -ne 48) {
    Write-Host "[WARN] Expected 48 features, got $featCount" -ForegroundColor Red
} else {
    Write-Host "[OK] Feature count = 48 (matches runtime)" -ForegroundColor Green
}

# 验证 hash 与运行时 FEAT_NAMES 一致
Push-Location $backend
try {
    $runtimeHash = python -c @"
import hashlib
from app.services.alphaflow_features import FEAT_NAMES
print(hashlib.md5(','.join(FEAT_NAMES).encode()).hexdigest()[:8])
"@
    if ($runtimeHash -eq $featHash) {
        Write-Host "[OK] Feature hash matches runtime ($runtimeHash)" -ForegroundColor Green
    } else {
        Write-Host "[FAIL] Hash mismatch: meta=$featHash, runtime=$runtimeHash" -ForegroundColor Red
        exit 1
    }
} finally {
    Pop-Location
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  Retrain complete!" -ForegroundColor Cyan
Write-Host "  Restart backend to load new model." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
