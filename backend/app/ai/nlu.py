"""Feature extraction for the offline assistant.

Turns one user message into an `Utterance`: folded text, money amounts, quantities with units,
time window and a product reference with Azerbaijani case suffixes removed ("Taxta qapının" → "Taxta qapı"). The rule table in `rules.py` only reads
these features, so rules stay short and declarative. Pure functions, no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from functools import cached_property
from typing import Any

from app.services.lexicon import _SURFACE, UNIT_ALIASES, analyze, fold, stem, unit_phrase_replace

NUM = r"\d+(?:[.,]\d+)?"
UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)
_CURRENCY_AFTER = re.compile(r"^\s*(?:azn|manat\w*|man\b)")
_PRICE_BEFORE = re.compile(r"(?:qiymet\w*|price|fiyat\w*|deyer\w*|costs?|each|her biri|biri|alis|satis|maya)\W{0,3}$")
_TIME_AFTER = re.compile(r"^\s*(gun|gün|hefte|ay|il|day|week|month|year)\w*")
_PERCENT_AFTER = re.compile(r"^\s*(?:faiz|percent)")
_TIME_FACTOR = {"gun": 1, "gün": 1, "day": 1, "hefte": 7, "week": 7, "ay": 30, "month": 30, "il": 365, "year": 365}

PRONOUNS = {"onun", "onu", "ona", "ondan", "bunun", "bunu", "buna", "bundan", "it", "its", "that", "this", "them", "onlarin"}

# Words that never belong to a product reference ("Taxta qapının qiymətini 90 et" → "Taxta qapı").
REF_DROP_EXACT = {
    "et", "edin", "elə", "ele", "olsun", "var", "yox", "ve", "ile", "de", "da", "ki", "bu", "o", "bir", "ne", "nə", "nece",
    "qeder", "cox", "az", "en", "hem", "men", "mene", "menim", "bizim", "the", "of", "to", "by", "for", "a", "an", "my",
    "our", "is", "are", "set", "make", "please", "zehmet", "olmasa", "indi", "artiq", "yeni", "new", "and", "in", "on",
    "eded", "dene", "unit", "units", "pieces", "piece", "vahid", "azn", "manat", "man", "now", "dir", "dur", "dır", "how",
    "many", "much", "do", "we", "have", "left", "is", "qalib", "qaldi", "faiz", "percent", "teze", "today", "bugun", "gun",
    "hefte", "ay", "il", "son", "bele", "ele", "bes", "deye", "uste", "ustune", "hazirda", "hal", "halda", "hazirki",
    "cemi", "umumiyyetle", "sold", "sell", "for", "each", "dunen", "yesterday", "hele",
}
REF_DROP_PREFIX = (
    "qiymet", "price", "fiyat", "stok", "stock", "miqdar", "quantity", "sayi", "sayin", "anbar", "inventar", "inventory",
    "mehsul", "product", "azalt", "artir", "deyis", "yenile", "update", "change", "reduce", "decrease", "increase",
    "qaldir", "endir", "dusur", "satdim", "satildi", "satdiq", "sold", "cixart", "cixdi", "elave", "goster", "show",
    "tap", "find", "axtar", "list", "minimum", "hedd", "threshold", "xeberdar", "alis", "satis", "purchase", "selling",
    "maya", "manat", "teyin", "qoy", "yaz", "olsun", "dene", "eded", "edek", "isteyir", "lazim", "gelib", "geldi",
    "gotur", "xarab", "remove", "istifade", "neçe", "nece", "satil", "satmis", "sattim", "heresi", "hereye",
    "musteri", "tanesi", "denesi", "itdi", "sindi", "azn", "usd", "eur", "dollar", "avro",
)


@dataclass
class Context:
    """What earlier assistant turns showed, parsed from `[context: …]` lines the orchestrator adds to history."""

    products: list[str] = field(default_factory=list)  # product names mentioned, most recent last

    _PRODUCT = re.compile(r"product=([^;\]]+)")

    @classmethod
    def from_messages(cls, messages: list[dict[str, Any]]) -> "Context":
        ctx = cls()
        for m in messages:
            if m.get("role") != "assistant" or not m.get("content"):
                continue
            text = m["content"]
            ctx.products += [p.strip() for p in cls._PRODUCT.findall(text)]
        return ctx


@dataclass
class Number:
    value: Decimal
    kind: str  # "money" | "quantity" | "time" | "percent"
    unit: str | None = None  # for quantities
    time_unit: str | None = None


def _dec(s: str) -> Decimal:
    return Decimal(s.replace(",", "."))


def plain(d: Decimal | None) -> str | None:
    """Decimal → canonical string for tool arguments ('85', '12.5')."""
    if d is None:
        return None
    s = format(d.normalize(), "f")
    return s


class Utterance:
    def __init__(self, text: str, context: Context | None = None, mode: str = "inventory"):
        self.raw = text.strip()
        self.mode = mode
        self.context = context or Context()
        uuid_m = UUID_RE.search(self.raw)
        self.uuid = uuid_m.group(0).lower() if uuid_m else None
        t = UUID_RE.sub(" ", self.raw).replace("₼", " azn ").replace("%", " faiz ")
        self.f = unit_phrase_replace(fold(t))
        self.tokens = [w.strip(".,;:!?") for w in self.f.split() if w.strip(".,;:!?")]

    # ------------------------------------------------------------------ matching helpers
    def has(self, *words: str) -> bool:
        """True if any word/phrase occurs at a word start (so 'qiymet' matches 'qiymətini')."""
        return any(re.search(rf"(?<![a-z0-9]){re.escape(w)}", self.f) for w in words)

    def only(self, vocabulary: set[str]) -> bool:
        """True if every token belongs to the vocabulary (used for pure small talk like 'Salam!')."""
        return bool(self.tokens) and all(t in vocabulary for t in self.tokens)

    # ------------------------------------------------------------------ numbers
    @cached_property
    def numbers(self) -> list[Number]:
        out: list[Number] = []
        for m in re.finditer(NUM, self.f):
            if m.start() > 0 and (self.f[m.start() - 1].isalpha() or self.f[m.start() - 1] == "-" and m.start() > 1
                                  and self.f[m.start() - 2].isalnum()):
                continue  # part of a code like "MDF-18" or "x2"
            value, after, before = _dec(m.group(0)), self.f[m.end(): m.end() + 14], self.f[max(0, m.start() - 22): m.start()]
            tm = _TIME_AFTER.match(after)
            if re.match(r"^-?(?:ci|cu)(?![a-z])", after):
                continue  # ordinal: "1-ci elan"
            if _PERCENT_AFTER.match(after):
                out.append(Number(value, "percent"))
            elif tm and not re.match(r"^\s*(?:ed|den|kq|kv|m\b)", after):
                out.append(Number(value, "time", time_unit=tm.group(1)))
            elif _CURRENCY_AFTER.match(after) or _PRICE_BEFORE.search(before):
                out.append(Number(value, "money"))
            else:
                um = re.match(r"^\s*([a-z0-9.]+)", after)
                out.append(Number(value, "quantity", unit=UNIT_ALIASES.get(um.group(1).rstrip(".")) if um else None))
        return out

    @property
    def money(self) -> list[Decimal]:
        return [n.value for n in self.numbers if n.kind == "money"]

    @property
    def quantities(self) -> list[Number]:
        return [n for n in self.numbers if n.kind == "quantity"]

    @property
    def percent(self) -> bool:
        return any(n.kind == "percent" for n in self.numbers)

    @cached_property
    def days(self) -> int:
        """Reporting window: 'son 3 ayda' → 90, 'bu həftə' → 7, default 30."""
        for n in self.numbers:
            if n.kind == "time":
                return max(1, min(365, int(n.value * _TIME_FACTOR.get(n.time_unit or "gun", 1))))
        if self.has("bu gun", "bugun", "today"):
            return 1
        if self.has("hefte", "week"):
            return 7
        if self.has("bu il", "this year", "il erzinde", "year"):
            return 365
        return 30

    # ------------------------------------------------------------------ entities
    @cached_property
    def concepts(self) -> set[str]:
        return analyze(self.raw)[0]

    @cached_property
    def rest(self) -> list[str]:
        return analyze(self.raw)[1]

    @property
    def pronoun(self) -> bool:
        return any(t in PRONOUNS for t in self.tokens)

    def ref(self, extra_drop: tuple[str, ...] = (), use_context: bool = True) -> str | None:
        """Product reference in the user's own words, case suffixes removed. When the message names
        no product ("onun qiymətini 95 et", "stokunu 20 azalt") it falls back to the product discussed
        last, unless use_context is False."""
        if self.uuid:
            return self.uuid
        words: list[str] = []
        for w in UUID_RE.sub(" ", self.raw).replace("₼", " ").split():
            w = w.strip(".,;:!?«»\"'()")
            f = fold(w)
            if not f or f in {"oldu", "olub"} or re.search(r"\d", f) or f in REF_DROP_EXACT or f in PRONOUNS or f in UNIT_ALIASES:
                continue
            if any(f.startswith(p) for p in REF_DROP_PREFIX + extra_drop):
                continue
            words.append(_strip_case(w, f))
        if words:
            return " ".join(words)
        if self.context.products and (use_context or self.pronoun):
            return self.context.products[-1]
        return None


def _strip_case(word: str, folded: str) -> str:
    """'qapının' → 'qapı', 'lövhəni' → 'lövhə', 'Ceresitdən' → 'Ceresit'; keeps the original spelling."""
    s = stem(folded)
    if s not in _SURFACE:
        s = folded
        for suf in ("nin", "nun", "dan", "den", "ni", "nu", "in", "un", "a", "e", "i", "u"):
            if folded.endswith(suf) and len(folded) - len(suf) >= 4 and (suf not in ("a", "e", "i", "u") or folded[-2] in "nsy"):
                s = folded[: -len(suf)]
                if s.endswith(("n", "s", "y")) and s[:-1] in _SURFACE:
                    s = s[:-1]
                break
    if s == folded:
        return word
    return word[: len(s)] if len(fold(word)) == len(word) and fold(word).startswith(s) else s
