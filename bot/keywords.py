"""Compiled regex keyword matcher with basic Russian morphology support."""

import re

_CYR = re.compile(r'[\u0400-\u04ff]')
_TRAILING_VOWEL = re.compile(r'[аеёиоуыьэюя]$', re.IGNORECASE)


def _stem(keyword: str) -> str:
    """Strip trailing Russian vowel for basic inflection matching.

    "колонка" → "колонк"  matches колонку/колонки/колонкой etc.
    Non-Cyrillic and short words are returned unchanged.
    """
    if len(keyword) > 4 and _CYR.search(keyword):
        return _TRAILING_VOWEL.sub('', keyword)
    return keyword


class KeywordMatcher:
    def __init__(self, keywords: list[str] | None = None,
                 keyword_map: dict[str, list[str]] | None = None):
        self._keywords: list[str] = []
        self._pattern: re.Pattern | None = None
        if keywords or keyword_map:
            self.update(keywords or [], keyword_map or {})

    def update(self, keywords: list[str], keyword_map: dict | None = None):
        """Build pattern from keywords + synonym groups in keyword_map.

        keyword_map values can be either str (legacy type mapping) or list[str]
        (synonym group). Synonyms are stem-matched the same way as keywords.
        """
        terms = [k.strip().lower() for k in keywords if k.strip()]

        # Expand synonym groups from keyword_map
        if keyword_map:
            for _key, value in keyword_map.items():
                if isinstance(value, list):
                    terms.extend(v.strip().lower() for v in value if v.strip())
                # str values are type-labels, not synonyms — skip them

        self._keywords = list(dict.fromkeys(terms))  # deduplicate, preserve order
        if self._keywords:
            escaped = [re.escape(_stem(k)) for k in self._keywords]
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
