import time

import pytest

from smart_im.decoder import PinyinDecoder


@pytest.fixture(scope="module")
def decoder():
    return PinyinDecoder()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("nihao", "你好"),
        ("zhongguo", "中国"),
        ("jintian", "今天"),
        ("women", "我们"),
        ("shijie", "世界"),
        ("kaishi", "开始"),
        ("nv", "女"),
        ("lve", "略"),
    ],
)
def test_common_full_pinyin(decoder, raw, expected):
    assert decoder.decode(raw)[0].text == expected


def test_exact_matches_win_over_longer_frequent_prefixes(decoder):
    assert decoder.decode("wo")[0].text == "我"
    assert decoder.decode("zhongguo")[0].text == "中国"


def test_homophones_remain_available(decoder):
    texts = {candidate.text for candidate in decoder.decode("shishi")}
    assert {"试试", "事实", "实施", "实时"} <= texts


def test_apostrophe_constrains_syllable_boundary(decoder):
    assert decoder.decode("xian")[0].text == "先"
    assert decoder.decode("xi'an")[0].text == "西安"
    assert "先" not in {candidate.text for candidate in decoder.decode("xi'an")}
    assert decoder.decode("xi an")[0].text == "西安"
    assert decoder.decode("xi'a")[0].text == "西安"


@pytest.mark.parametrize(
    "raw,normalized",
    [
        ("Nǐ Hǎo", "ni'hao"),
        ("nǚ", "nv"),
        ("NU:", "nv"),
        ("lüè", "lve"),
        ("ni3 hao3", "ni'hao"),
        ("  ni’hao  ", "ni'hao"),
    ],
)
def test_normalization(decoder, raw, normalized):
    assert decoder.normalize(raw) == normalized


def test_partial_and_initials(decoder):
    partials = decoder.decode("nih")
    assert partials[0].text == "你好"
    assert partials[0].source == "partial"
    assert "你好" in {candidate.text for candidate in decoder.decode("nh")}
    assert "中国" in {candidate.text for candidate in decoder.decode("zg")}
    assert "你好" in {candidate.text for candidate in decoder.decode("n'h")}


def test_compose_unlisted_sentence(decoder):
    candidates = decoder.decode("womenjintianqushangban")
    assert candidates[0].text == "我们今天去上班"
    assert candidates[0].source == "composed"
    assert candidates[0].pinyin == "womenjintianqushangban"


def test_spelling_transposition_is_labeled(decoder):
    candidate = next(item for item in decoder.decode("nihoa") if item.text == "你好")
    assert candidate.source == "spelling"
    assert "nihoa → nihao" in candidate.annotation


def test_spelling_insertion_and_deletion(decoder):
    for raw in ["nihaao", "nhao", "zhonguo"]:
        expected = "中国" if raw == "zhonguo" else "你好"
        assert any(
            item.text == expected and item.source == "spelling" for item in decoder.decode(raw)
        )


def test_spelling_does_not_move_distant_letters(decoder):
    assert not any(item.text == "你好" for item in decoder.decode("nhaoi"))


def test_valid_prefix_does_not_show_spelling_corrections(decoder):
    assert all(item.source != "spelling" for item in decoder.decode("niha"))


def test_reverse_lookup_and_unknown_chinese(decoder):
    assert decoder.lookup("你好") == "nihao"
    assert decoder.lookup("西安") == "xian"
    assert decoder.lookup("铷") == "ru"
    assert decoder.lookup("English") == ""
    assert decoder.lookup("") == ""


@pytest.mark.parametrize(
    "text,raw",
    [
        ("你好", "nihao"),
        ("你好", "ni'hao"),
        ("西安", "xi'an"),
        ("重", "chong"),
        ("重", "zhong"),
        ("布鲁墨", "bu'lu'mo"),
        ("布鲁墨", "bulumo"),
        ("女", "nǚ"),
        ("略", "lve"),
    ],
)
def test_complete_reading_matches_known_and_custom_phrases(decoder, text, raw):
    assert decoder.matches(text, raw)


@pytest.mark.parametrize(
    "text,raw",
    [
        ("先", "xi'an"),
        ("你好", "ni'h'ao"),
        ("你好", "nh"),
        ("你好", "nih"),
        ("你好", "nihoa"),
        ("布鲁墨", "bu'lumo'a"),
        ("重", "zhon"),
        ("你好", "ni!hao"),
        ("你好！", "nihao"),
        ("", "nihao"),
        ("你好", ""),
        ("重" * 65, "chong" * 65),
    ],
)
def test_complete_reading_rejects_partial_typo_and_invalid_boundaries(decoder, text, raw):
    assert not decoder.matches(text, raw)


def test_curated_lexicon_coverage(decoder):
    assert len(decoder.entries) >= 1000
    assert sum(len(candidate.text) > 1 for candidate in decoder.entries) >= 500


def test_limits_and_invalid_input(decoder):
    assert decoder.decode("") == []
    assert decoder.decode("你好") == []
    assert decoder.decode("ni!hao") == []
    assert decoder.decode("a" * 65) == []
    assert decoder.decode("nihao", limit=0) == []
    assert len(decoder.decode("s", limit=3)) == 3
    candidates = decoder.decode("shi", limit=9999)
    assert len(candidates) <= decoder.MAX_RESULTS
    assert len({item.text for item in candidates}) == len(candidates)


def test_long_ambiguous_buffer_has_bounded_work(decoder):
    # A broad guard against exponential segmentation, not a machine-specific
    # latency benchmark. Real p50/p95 timing is provided by the project benchmark.
    start = time.perf_counter()
    decoder.decode("shi" * 21)
    assert time.perf_counter() - start < 2.0
