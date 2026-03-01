"""Compiled regex keyword matcher."""

import re


class KeywordMatcher:
    def __init__(self, keywords: list[str] | None = None):
        self._keywords: list[str] = []
        self._pattern: re.Pattern | None = None
        if keywords:
            self.update(keywords)

    def update(self, keywords: list[str]):
        self._keywords = [k.strip().lower() for k in keywords if k.strip()]
        if self._keywords:
            escaped = [re.escape(k) for k in self._keywords]
            self._pattern = re.compile(
                r"(?:" + "|".join(escaped) + r")",
                re.IGNORECASE,
            )
        else:
            self._pattern = None

    def match(self, text: str) -> str | None:
        """Return first matched keyword or None."""
        if not self._pattern or not text:
            return None
        m = self._pattern.search(text)
        return m.group(0).lower() if m else None

    @property
    def keywords(self) -> list[str]:
        return list(self._keywords)
