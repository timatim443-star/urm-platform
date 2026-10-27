"""Собрать снимок данных вручную или по расписанию.

    python tools/build_snapshot.py          # только если витрины обновились
    python tools/build_snapshot.py --force  # пересобрать в любом случае

Удобно для прогрева после старта контейнера и для cron после ночного расчёта.
Приложение и само пересобирает снимок, когда видит новую публикацию, но
первый пользователь в этом случае увидит вчерашние данные до конца сборки.
"""
from __future__ import annotations

import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _admin_env  # noqa: E402,F401 — путь к проекту и тихий streamlit

from urm import snapshot  # noqa: E402
from urm.loader import publication_key  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    key = publication_key()
    if "--force" not in sys.argv and snapshot.current_meta().get("publication_key") == key:
        print("Снимок актуален:", snapshot.current_dir())
        return 0
    path = snapshot.build(key)
    meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
