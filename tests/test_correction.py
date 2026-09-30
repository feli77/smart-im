from smart_im.correction import CorrectionEngine


def test_spelling_suggestions_have_exact_offsets():
    original = "😀请按装程序，然后登陆账号。"
    suggestions = CorrectionEngine().suggest(original)
    assert [item.replacement for item in suggestions] == ["安装", "登录账号"]
    assert original == "😀请按装程序，然后登陆账号。"  # Suggestions do not mutate input.
    for item in suggestions:
        assert original[item.start : item.end] == item.original
        assert 0 < item.confidence <= 1


def test_semantic_collocation_is_a_low_confidence_suggestion():
    suggestion = CorrectionEngine().suggest("我们希望提高效率和成本。")[0]
    assert suggestion.replacement == "提高效率并降低成本"
    assert suggestion.confidence < 0.9


def test_valid_ambiguous_text_is_not_aggressively_rewritten():
    assert CorrectionEngine().suggest("他在这里再次说明，错误操作会降低工作效率。") == []


def test_repeated_errors_and_long_context_preserve_offsets():
    original = "开头" * 400 + "按装和按装"
    suggestions = CorrectionEngine().suggest(original)
    assert len(suggestions) == 2
    assert [item.start for item in suggestions] == [800, 803]
    assert all(original[item.start : item.end] == "按装" for item in suggestions)
