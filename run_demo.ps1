# URM - testovyy zapusk na Windows.
#
#   run_demo.bat   (dvoynoy klik)   ili   .\run_demo.ps1
#
# Bez vhoda i uchetnyh zapisey, rabochaya baza.
# Parol sprashivaetsya pri zapuske i v fayly ne popadaet.

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
Set-Location $PSScriptRoot

$env:PGHOST     = "pg.data.ddirection.kz"
$env:PGPORT     = "55432"
$env:PGDATABASE = "URM"
$env:PGSSLMODE  = "prefer"

$login = Read-Host "Логин PostgreSQL (Enter = URM)"
if ([string]::IsNullOrWhiteSpace($login)) { $login = "URM" }
$login = $login.Trim()

$ok = $false
for ($try = 1; $try -le 3 -and -not $ok; $try++) {
    $secret = Read-Host "Пароль PostgreSQL (попытка $try из 3)" -AsSecureString
    $plain  = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)).Trim()
    if ($plain.Length -eq 0) {
        Write-Host "Пароль пустой - введите ещё раз." -ForegroundColor Yellow
        continue
    }
    $env:URM_RO_USER     = $login
    $env:URM_RO_PASSWORD = $plain
    Write-Host "Проверяю подключение..."
    python tools/list_labs.py
    $ok = ($LASTEXITCODE -eq 0)
}
if (-not $ok) {
    Write-Host "Подключиться не удалось." -ForegroundColor Red
    exit 1
}

$write = Read-Host "Разрешить сохранять маппинг в базу этой учёткой? (y/N)"
if ($write -match '^[yYдД]') {
    $env:URM_RW_USER     = $login
    $env:URM_RW_PASSWORD = $plain
} else {
    $env:URM_RW_USER     = ""
    $env:URM_RW_PASSWORD = ""
}
$env:URM_NO_AUTH      = "1"
$env:URM_SNAPSHOT_DIR = "./snapshots"

Write-Host ""
Write-Host "Готовлю снимок данных (первый раз - несколько минут, дальше - только при обновлении витрин)..."
python tools/build_snapshot.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "Снимок не собрался - текст ошибки выше." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Открываю интерфейс: http://localhost:8501" -ForegroundColor Green
Write-Host "Остановить - Ctrl+C в этом окне." -ForegroundColor DarkGray
Write-Host ""
streamlit run app.py --server.address=127.0.0.1 --server.port=8501
