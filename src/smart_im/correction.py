"""Conservative, explainable correction suggestions; never auto-replace."""

from smart_im.types import Correction

# Authored common confusions. Short ambiguous characters (的/地/得, 在/再)
# are deliberately not rewritten without a sufficiently specific collocation.
_RULES = (
    ("按装", "安装", "常见同音错字：软件或设备通常用“安装”", 0.97),
    ("登陆账号", "登录账号", "账号访问通常写作“登录”", 0.94),
    ("登陆帐号", "登录帐号", "账号访问通常写作“登录”", 0.94),
    ("帐户", "账户", "推荐使用规范词形“账户”", 0.90),
    ("在接再厉", "再接再厉", "成语应为“再接再厉”", 0.99),
    ("再接再励", "再接再厉", "成语应为“再接再厉”", 0.99),
    ("迫不急待", "迫不及待", "成语应为“迫不及待”", 0.99),
    ("一如继往", "一如既往", "成语应为“一如既往”", 0.99),
    ("既使", "即使", "表示让步时通常用“即使”", 0.95),
    ("因该", "应该", "常见近音错字“应该”", 0.97),
    ("以经", "已经", "常见同音错字“已经”", 0.97),
    ("尽管如此但是", "尽管如此", "转折表达可能重复，请结合上下文确认", 0.78),
    ("提高效率和成本", "提高效率并降低成本", "效率与成本通常使用不同的动词搭配", 0.84),
    ("提高成本和效率", "降低成本并提高效率", "成本与效率通常使用不同的动词搭配", 0.84),
)


class CorrectionEngine:
    """A finite MVP rule set, not a general semantic understanding system."""

    def suggest(self, text: str) -> list[Correction]:
        suggestions: list[Correction] = []
        # Bound per-keystroke work. Offsets always refer to the original text.
        start_offset = max(0, len(text) - 512)
        tail = text[start_offset:]
        for original, replacement, reason, confidence in _RULES:
            cursor = 0
            while (index := tail.find(original, cursor)) >= 0:
                start = start_offset + index
                suggestions.append(
                    Correction(
                        original, replacement, start, start + len(original), reason, confidence
                    )
                )
                cursor = index + len(original)
        return sorted(suggestions, key=lambda item: (item.start, -item.confidence))[:20]
