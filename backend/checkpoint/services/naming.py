"""Session names suggested from their tasks, and the Git-safe branch names derived from them."""

from __future__ import annotations

import re
import unicodedata
from typing import Callable, Iterable

BRANCH_PREFIX = "checkpoint/"
MAX_SLUG = 48

# Words that make a title longer without saying what it's about.
_FILLER = {
    "a", "an", "the", "is", "are", "was", "be", "it", "its", "this", "that", "these", "those", "there", "should", "would",
    "could", "can", "please", "just", "really", "very", "some", "when", "then", "so", "of", "to", "for", "on", "in", "at",
    "i", "we", "you", "me", "my", "our", "maybe", "need", "needs", "make", "made", "get", "gets", "got", "seems", "kind",
}
_LEAD_VERBS = {"fix", "fixes", "fixed", "add", "adds", "make", "update", "improve", "change", "remove", "check", "investigate"}
_DEFAULT_TITLE = re.compile(r"^Session \w{3} \d{2} \w{3} \d{4}, \d{2}:\d{2}$")


def is_default_title(title: str) -> bool:
    return bool(_DEFAULT_TITLE.match(title or ""))


def short_phrase(title: str, max_words: int = 5) -> str:
    """'The purple geyser should not launch players so high' -> 'Purple geyser launch players high'."""
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'+-]*", title or "")
    while words and words[0].lower() in _LEAD_VERBS:
        words = words[1:]
    kept = [w for w in words if w.lower() not in _FILLER] or words
    out = " ".join(kept[:max_words])
    return out[:1].upper() + out[1:] if out else ""


def suggest_name(titles: Iterable[str]) -> str:
    """A short name for a session from its task titles (first ones first)."""
    phrases: list[str] = []
    for t in titles:
        p = short_phrase(t, 4 if phrases else 5)
        if p and p.lower() not in (x.lower() for x in phrases):
            phrases.append(p)
    if not phrases:
        return ""
    if len(phrases) == 1:
        return phrases[0]
    name = f"{phrases[0]} and {phrases[1][:1].lower() + phrases[1][1:]}"
    if len(phrases) > 2:
        name += f" (+{len(phrases) - 2})"
    return name[:120]


def slug(name: str) -> str:
    """Git-safe: lowercase ASCII letters, digits and single hyphens."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    if len(s) > MAX_SLUG:
        s = s[:MAX_SLUG].rsplit("-", 1)[0] if "-" in s[:MAX_SLUG] else s[:MAX_SLUG]
    return s or "session"


def branch_for(name: str, exists: Callable[[str], bool]) -> str:
    """checkpoint/<slug>, with -2, -3... only when that branch is already taken."""
    base = BRANCH_PREFIX + slug(name)
    if not exists(base):
        return base
    n = 2
    while exists(f"{base}-{n}"):
        n += 1
    return f"{base}-{n}"
