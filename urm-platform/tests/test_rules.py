"""Роли, надписи, цвета тепловой карты, пароли — без базы."""
from __future__ import annotations

import numpy as np
import pytest

from urm import accounts, notices
from urm.auth import User, hash_password, verify_password
from urm.charts import HEATMAP_LEVELS, heatmap_level
from urm.roles import ADMIN_PAGES, PAGES, ROLES


@pytest.mark.parametrize("role", ["city", "network"])
def test_report_roles_have_no_admin_pages(role):
    user = User("u", "U", role, ("lab0",))
    assert all(user.can_open(p) for p in PAGES if p not in ADMIN_PAGES)
    assert not any(user.can_open(p) for p in ADMIN_PAGES)


def test_admin_opens_everything():
    user = User("a", "A", "admin")
    assert all(user.can_open(p) for p in PAGES)


def test_unknown_role_falls_back_to_most_restricted():
    assert User("x", "X", "superuser").policy is ROLES["city"]


def test_roles_have_all_fields():
    fields = {"title", "all_labs", "pages", "network_quality", "mapping_edit", "users_manage",
              "single_session", "own_password"}
    for role in ROLES.values():
        assert fields <= set(role)


def test_password_hash_roundtrip():
    stored = hash_password("Секрет-123", iterations=1000)
    assert verify_password("Секрет-123", stored)
    assert not verify_password("секрет-123", stored)
    assert not verify_password("x", "garbage")


@pytest.mark.parametrize("bad", ["", "пароль", "Pass word", " lead", "Pass\x16word", "abcЖ1"])
def test_bad_passwords_rejected(bad):
    """Пароль: только латиница, цифры и знаки ASCII, без пробелов. Длина не ограничена."""
    with pytest.raises(accounts.AccountError):
        accounts.check_password(bad)


@pytest.mark.parametrize("good", ["a", "1", "abc", "12345", "Astana2026", "p@ss!_-#"])
def test_simple_latin_passwords_accepted(good):
    accounts.check_password(good)


def test_login_format():
    assert accounts.LOGIN_RE.match("ivanov.a")
    assert not accounts.LOGIN_RE.match("Иванов")
    assert not accounts.LOGIN_RE.match("ab")


def test_heatmap_levels():
    issued = np.array([100, 100, 100, 100, 100, 100, 100, 0, 0])
    consumed = np.array([0, 20, 40, 60, 98, 100, 130, 5, 0])
    levels = heatmap_level(issued, consumed)
    assert levels[:7].tolist() == [3, 3, 2, 1, 0, 0, 4]  # >100 % — не красный
    assert levels[7] == 5 and np.isnan(levels[8])
    colors = {code: color for code, color, _ in HEATMAP_LEVELS}
    assert colors[4] != colors[3]


def test_notices_switch(monkeypatch):
    monkeypatch.delenv("URM_UI_SHOW", raising=False)
    assert notices.enabled("delta_warning") and notices.enabled("footer_timing")
    monkeypatch.setenv("URM_UI_SHOW", "notice")
    assert notices.enabled("delta_warning") and not notices.enabled("footer_timing")
    assert notices.text("hint_card_pick") is None
    monkeypatch.setenv("URM_UI_SHOW", "")
    assert not notices.enabled("delta_warning")
    assert notices.enabled("test_mode")  # предупреждение тестового режима не отключается
    monkeypatch.setenv("URM_UI_SHOW", "notice,hint")
    monkeypatch.setenv("URM_UI_HIDE", "risk_priority")
    assert not notices.enabled("risk_priority") and notices.enabled("watchlist_intro")


def test_canonical_names_one_name_per_bin():
    import pandas as pd

    from tests.conftest import make_fact
    from urm import fast
    from urm import report_core as rc

    raw = make_fact(seed=3)
    # у части БИН в разные месяцы разные названия — как в документах 1С/ЛИС
    first = raw["month"] == raw["month"].min()
    raw.loc[first, "partner_name"] = raw.loc[first, "partner_name"] + " (старое)"
    before = rc.summaries(rc.enrich_fact(raw))["customers"]
    assert before.duplicated(["lab_key", "bin"]).any()  # без исправления — дубликаты заказчиков

    fixed = fast.canonical_names(raw)
    assert fixed.groupby("bin")["partner_name"].nunique().max() == 1
    after = rc.summaries(rc.enrich_fact(fixed))["customers"]
    assert not after.duplicated(["lab_key", "bin"]).any()
    assert after["issued"].sum() == pd.Series(before["issued"]).sum()  # суммы не меняются


def test_signals_grouped_by_customer(fact):
    from urm import fast
    from urm.tables import signals_by_customer, signals_display

    anomalies = fast.build_anomalies(fact)
    grouped = signals_by_customer(anomalies)
    assert not grouped.duplicated(["lab_key", "БИН"]).any()
    assert len(grouped) == anomalies.groupby(["lab_key", "БИН"]).ngroups
    assert abs(grouped["unaccounted"].sum() - anomalies["Неотражено_ед"].sum()) < 1e-6
    assert (grouped["materials"] >= 1).all()
    assert list(signals_display(grouped).columns)[:3] == ["Критичность", "БИН", "Заказчик"]


def test_customer_labels_keep_bins_apart():
    import pandas as pd

    from urm.tables import customer_labels

    frame = pd.DataFrame({"partner_name": ["ТОО А", "ТОО А", "ТОО Б"], "bin": ["1", "2", "3"]})
    labels = customer_labels(frame).tolist()
    assert labels[0] != labels[1] and labels[2] == "ТОО Б"


def test_xlsx_roundtrip():
    import io

    import pandas as pd

    from urm.tables import xlsx

    data = xlsx({"Лист с очень длинным названием больше тридцати одного": pd.DataFrame({"БИН": ["000123"], "x": [1.5]})})
    back = pd.read_excel(io.BytesIO(data), sheet_name=None, dtype={"БИН": str})
    sheet = next(iter(back.values()))
    assert sheet.loc[0, "БИН"] == "000123" and sheet.loc[0, "x"] == 1.5


def test_materials_list_is_complete():
    """В сводке по заказчику перечислены все материалы, без «и ещё N»."""
    import pandas as pd

    from urm.tables import _join_unique

    values = pd.Series([f"Материал {i}" for i in range(20)] + ["Материал 1", None, " "])
    joined = _join_unique(values)
    assert "и ещё" not in joined
    assert joined.count("; ") == 19
    assert _join_unique(values, limit=3).endswith("и ещё 17")


def test_donut_small_share_and_centered_title():
    from urm.charts import donut

    fig = donut(["А", "Б"], [1375, 3], ["#000", "#111"], "1 378")
    labels = list(fig.data[0].labels)
    assert "< 1 %" in labels[1] and "0 %" not in labels[1]
    assert fig.data[0].title.position == "middle center"  # число в центре кольца, а не аннотацией сбоку
    assert not fig.layout.annotations


def test_guid_cells_not_split():
    from urm.style import _numeric_cells

    out = _numeric_cells("<td>334ffc71-232e-11e7-87b5-0050569a1c2b</td><td>5 620</td>")
    assert '<td class="guid">334ffc71' in out and '<td class="num">5 620</td>' in out


def test_admin_single_session_others_shared():
    assert ROLES["admin"]["single_session"] is True
    assert ROLES["city"]["single_session"] is False and ROLES["network"]["single_session"] is False
    assert ROLES["city"]["own_password"] is False  # общая учётка: пароль меняет только админ


def test_sessions_shared_account_and_admin_single(tmp_path):
    from urm.sessions import SessionStore

    store = SessionStore(tmp_path / "s.json")
    # общая учётка города: 10 человек одновременно — все сессии живы
    tokens = [store.create("astana", 240)[0] for _ in range(10)]
    assert all(store.login_for(t) == "astana" for t in tokens)
    store.revoke(tokens[0])  # один вышел — остальные работают
    assert store.login_for(tokens[0]) is None and store.login_for(tokens[1]) == "astana"
    assert store.count("astana") == 9

    # админ: новый вход закрывает прежнее устройство
    first, _ = store.create("admin", 240, single=True)
    second, closed = store.create("admin", 240, single=True)
    assert closed == 1 and store.login_for(first) is None and store.login_for(second) == "admin"
    assert store.closed_reason(first) == "other_device"

    # переживает перезапуск сервера: новое хранилище читает файл; токенов в файле нет
    again = SessionStore(tmp_path / "s.json")
    assert again.login_for(second) == "admin" and again.login_for(tokens[1]) == "astana"
    assert second not in (tmp_path / "s.json").read_text()


def test_sessions_expire_and_disable(tmp_path):
    from urm.sessions import SessionStore

    store = SessionStore(tmp_path / "s.json")
    token, _ = store.create("kim", 0)  # срок 0 минут — уже истекла
    assert store.login_for(token) is None and store.closed_reason(token) == "expired"
    live, _ = store.create("kim", 240)
    assert store.revoke_login("kim") == 1 and store.login_for(live) is None
    assert store.closed_reason(live) == "disabled"
