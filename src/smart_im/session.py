"""Small, platform-independent composition state machine.

Only explicit composition and committed text are retained. Platform adapters own
cursor positions and must call ``reset_context`` when the target changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from .types import Candidate, SuggestionResult


@dataclass(frozen=True)
class KeyAction:
    consumed: bool = False
    changed: bool = False
    text: str = ""
    pinyin: str = ""
    context: str = ""


class InputSession:
    MAX_COMPOSITION = 64
    MAX_CONTEXT = 512
    PUNCTUATION = {",": "，", ".": "。", ";": "；", ":": "：", "?": "？", "!": "！"}

    def __init__(self) -> None:
        self.enabled = True
        self.pinyin = ""
        self.context = ""
        self.candidates: list[Candidate] = []
        self.predictions: list[Candidate] = []
        self.revision = 0
        self._pending_selection: int | None = None
        self._queued_keys: list[str] = []
        self._ready_actions: list[KeyAction] = []

    def reset_context(self, context: str = "") -> None:
        self.pinyin = ""
        self._pending_selection = None
        self._queued_keys = []
        self._ready_actions = []
        self.context = context[-self.MAX_CONTEXT :]
        self.candidates = []
        self.predictions = []
        self.revision += 1

    def set_context(self, context: str) -> None:
        context = context[-self.MAX_CONTEXT :]
        if context != self.context:
            self.context = context
            self.candidates = []
            self.predictions = []
            self.revision += 1

    def apply_result(self, revision: int, result: SuggestionResult) -> bool:
        if revision != self.revision:
            return False
        self.candidates = list(result.candidates)
        self.predictions = list(result.predictions)
        if self._pending_selection is not None:
            index, self._pending_selection = self._pending_selection, None
            queued, self._queued_keys = self._queued_keys, []
            if self.candidates:
                action = self.select(index)
            else:
                action = self.commit_literal()
            actions = [action]
            for key in queued:
                if self._pending_selection is not None:
                    self._queued_keys.append(key)
                else:
                    replayed = self.handle(key)
                    if not replayed.consumed and len(key) == 1:
                        replayed = self._commit(key)
                    if not replayed.consumed and key in {"Space", "Enter", "Return", "Tab"}:
                        replayed = self._commit(
                            {"Space": " ", "Enter": "\n", "Return": "\n", "Tab": "\t"}[key]
                        )
                    actions.append(replayed)
            self._ready_actions.extend(actions)
        return True

    def drain_actions(self) -> list[KeyAction]:
        actions, self._ready_actions = self._ready_actions, []
        return actions

    def _changed(self) -> KeyAction:
        self.candidates = []
        self.predictions = []
        self.revision += 1
        return KeyAction(consumed=True, changed=True)

    def select(self, index: int = 0, *, prediction: bool = False) -> KeyAction:
        values = self.predictions if prediction else self.candidates
        if not 0 <= index < len(values):
            if not prediction and self.pinyin and not self.candidates:
                self._pending_selection = index
                return KeyAction(consumed=True, changed=True)
            return KeyAction(consumed=bool(self.pinyin))
        return self._commit(values[index].text, "" if prediction else self.pinyin)

    def _commit(self, text: str, pinyin: str = "") -> KeyAction:
        action = KeyAction(True, True, text, pinyin, self.context)
        self.context = (self.context + text)[-self.MAX_CONTEXT :]
        self.pinyin = ""
        self._changed()
        return action

    def commit_literal(self) -> KeyAction:
        return self._commit(self.pinyin)

    def handle(self, key: str, *, modified: bool = False) -> KeyAction:
        """Handle a text character or canonical key name; never inspect external text."""
        if not self.enabled:
            return KeyAction()
        if modified:
            self.reset_context()
            return KeyAction(changed=True)
        if self._pending_selection is not None:
            if key == "Backspace" and self._queued_keys:
                self._queued_keys.pop()
                return KeyAction(consumed=True)
            if key in {
                "Escape",
                "Left",
                "Right",
                "Up",
                "Down",
                "Home",
                "End",
                "PageUp",
                "PageDown",
                "Delete",
                "Backspace",
            }:
                self._pending_selection = None
                self._queued_keys = []
            else:
                self._queued_keys.append(key)
                return KeyAction(consumed=True)
        if len(key) == 1 and ("a" <= key.lower() <= "z"):
            if len(self.pinyin) < self.MAX_COMPOSITION:
                self.pinyin += key.lower()
                return self._changed()
            return KeyAction(consumed=True)
        if key == "'" and self.pinyin:
            if len(self.pinyin) < self.MAX_COMPOSITION:
                self.pinyin += key
                return self._changed()
            return KeyAction(consumed=True)
        if key == "Backspace" and self.pinyin:
            self.pinyin = self.pinyin[:-1]
            return self._changed()
        if key == "Escape" and self.pinyin:
            self.pinyin = ""
            return self._changed()
        if key in ("Space", " ") and self.pinyin:
            return self.select()
        if key in ("Enter", "Return") and self.pinyin:
            return self.commit_literal()
        if key in "123456789" and len(key) == 1 and self.pinyin:
            return self.select(int(key) - 1)
        if key == "Tab" and not self.pinyin and self.predictions:
            return self.select(prediction=True)
        if key in self.PUNCTUATION:
            if self.pinyin and not self.candidates:
                self._pending_selection = 0
                self._queued_keys.append(key)
                return KeyAction(consumed=True, changed=True)
            prefix = self.select() if self.pinyin else KeyAction(context=self.context)
            punctuation = self.PUNCTUATION[key]
            if prefix.text:
                self.context = (self.context + punctuation)[-self.MAX_CONTEXT :]
                return KeyAction(
                    True, True, prefix.text + punctuation, prefix.pinyin, prefix.context
                )
            return self._commit(punctuation)
        if key in {
            "Left",
            "Right",
            "Up",
            "Down",
            "Home",
            "End",
            "PageUp",
            "PageDown",
            "Delete",
            "Backspace",
            "Enter",
            "Return",
            "Tab",
        }:
            self.reset_context()
            return KeyAction(changed=True)
        # Raw digits, whitespace and unsupported keys make external context unknown.
        if len(key) == 1 or key == "Space":
            self.reset_context()
            return KeyAction(changed=True)
        return KeyAction()


def prefix_at_utf16(text: str, position: int) -> str:
    """Qt positions count UTF-16 code units, including astral emoji as two units."""
    return text.encode("utf-16-le", errors="surrogatepass")[: position * 2].decode(
        "utf-16-le", errors="ignore"
    )
