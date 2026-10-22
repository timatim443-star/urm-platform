#!/usr/bin/env bash
# УРМ — тестовый запуск: без входа и учётных записей, рабочая база.
set -euo pipefail
cd "$(dirname "$0")"

export PGHOST="${PGHOST:-pg.data.ddirection.kz}"
export PGPORT="${PGPORT:-55432}"
export PGDATABASE="${PGDATABASE:-URM}"
export PGSSLMODE="${PGSSLMODE:-prefer}"

read -rp "Логин PostgreSQL [URM]: " LOGIN; LOGIN="${LOGIN:-URM}"
export URM_NO_AUTH=1
export URM_SNAPSHOT_DIR="${URM_SNAPSHOT_DIR:-./snapshots}"
OK=0
for TRY in 1 2 3; do
  read -rsp "Пароль PostgreSQL (попытка $TRY из 3): " SECRET; echo
  SECRET="$(printf '%s' "$SECRET" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  [[ -z "$SECRET" ]] && { echo "Пароль пустой — введите ещё раз."; continue; }
  export URM_RO_USER="$LOGIN" URM_RO_PASSWORD="$SECRET"
  echo "Проверяю подключение…"
  if python3 tools/list_labs.py; then OK=1; break; fi
done
[[ $OK == 1 ]] || { echo "Подключиться не удалось."; exit 1; }
read -rp "Разрешить сохранять маппинг в базу этой учёткой? [y/N]: " WRITE
if [[ "${WRITE,,}" == y* ]]; then export URM_RW_USER="$LOGIN" URM_RW_PASSWORD="$SECRET"
else export URM_RW_USER="" URM_RW_PASSWORD=""; fi
echo; echo "Готовлю снимок данных (первый раз — несколько минут, дальше — только при обновлении витрин)…"
python3 tools/build_snapshot.py
echo; echo "Интерфейс: http://<адрес сервера>:8501   (Ctrl+C — остановить)"; echo
exec streamlit run app.py --server.address="${URM_BIND:-0.0.0.0}" --server.port=8501
