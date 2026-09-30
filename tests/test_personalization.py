import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from smart_im.personalization import PersonalStore


def test_frequency_survives_reopen_and_pinyin_normalizes(tmp_path):
    path = tmp_path / "private" / "learning.sqlite3"
    with PersonalStore(path) as store:
        store.record("xi'an", "西安", "我准备去")
        store.record("xi an", "西安", "我准备去")
        assert store.frequency("xian", "西安") == 2
    with PersonalStore(path) as store:
        assert store.frequency("XI AN", "西安") == 2
        assert store.candidates("xian")[0].text == "西安"
        assert store.predict("我准备去")[0].text == "西安"
        assert store.stats() == {"phrases": 1, "selections": 2, "contexts": 1}
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_context_is_bounded_and_long_pasted_documents_are_not_learned(tmp_path):
    path = tmp_path / "learning.sqlite3"
    context = "这是一段不应完整保留的敏感历史，仅最后八字进入统计"
    with PersonalStore(path) as store:
        store.record("nihao", "你好", context)
        store.record("a", "长" * 65, context)
    db = sqlite3.connect(path)
    assert db.execute("SELECT context FROM transitions").fetchall() == [(context[-8:],)]
    assert db.execute("SELECT text FROM words").fetchall() == [("你好",)]
    db.close()
    assert context.encode("utf-8") not in path.read_bytes()


def test_clear_removes_all_learning_and_file_contents(tmp_path):
    path = tmp_path / "learning.sqlite3"
    with PersonalStore(path) as store:
        store.record("mimi", "这是保密的测试短语", "保密上下文")
        store.clear()
        assert store.stats() == {"phrases": 0, "selections": 0, "contexts": 0}
        assert store.predict("保密上下文") == []
        assert store.frequency("mimi", "这是保密的测试短语") == 0
    assert "这是保密的测试短语".encode("utf-8") not in path.read_bytes()


def test_concurrent_commits_are_not_lost():
    with PersonalStore(":memory:") as store:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: store.record("nihao", "你好", "大家"), range(100)))
        assert store.frequency("nihao", "你好") == 100
        assert store.predict("大家")[0].frequency == 100


def test_literal_sql_characters_and_limits():
    with PersonalStore(":memory:") as store:
        store.record("x", "hello'", "abc_%")
        store.record("x", "other", "xyz12")
        assert store.predict("other_%")[0].text == "hello'"
        assert store.candidates("x", 0) == []
        assert store.predict("abc_%", 0) == []
        assert store.predict("") == []
        assert store.frequency("x' OR 1=1 --", "other") == 0


def test_store_evicts_old_entries():
    with PersonalStore(":memory:") as store:
        store.MAX_ROWS = 3
        for index in range(5):
            store.record(f"x{index}", f"测试{index}", f"上下文{index}")
        assert store.stats()["phrases"] == 3
        assert store.stats()["contexts"] == 3
        assert store.frequency("x0", "测试0") == 0
        assert store.frequency("x4", "测试4") == 1
