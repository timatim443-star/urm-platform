"""Общее для служебных команд: подключение под учётной записью администратора.

Если заданы URM_ADMIN_USER / URM_ADMIN_PASSWORD — команды tools/migrate.py и
tools/users.py работают под ними (нужно для создания схемы и первого
администратора). Иначе — под учётной записью на запись URM_RW_USER.
Импортировать до любых модулей urm.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if os.environ.get("URM_ADMIN_USER"):
    os.environ["URM_RW_USER"] = os.environ["URM_ADMIN_USER"]
    os.environ["URM_RW_PASSWORD"] = os.environ.get("URM_ADMIN_PASSWORD", "")

# Вне веб-интерфейса streamlit пишет «No runtime found» — для служебных команд это шум.
from streamlit import logger as _st_logger  # noqa: E402

_st_logger.set_log_level("error")
