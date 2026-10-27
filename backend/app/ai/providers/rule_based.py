"""Deterministic offline assistant used when no LLM API key is configured.

It runs the rule table in app/ai/rules.py and emits the *same* structured tool calls an LLM
would, so the whole validate → preview → confirm → execute pipeline is exercised identically.
This module keeps the inventory-list parser and the result summaries.
It is clearly labelled as "offline mode" in the UI; it is not a language model.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

from app.ai.providers.base import ProviderEvent, ToolCall
from app.services.lexicon import UNIT_ALIASES, UNIT_LABEL_AZ, fold, unit_phrase_replace

_NUM = r"\d+(?:[.,]\d+)?"
_CURRENCY = r"(?:azn|manat|man|₼|m\b)"


def _num(s: str) -> str:
    return str(Decimal(s.replace(",", ".")))


# Filler words removed when extracting a product name (folded forms)
_FILLER = {
    "var", "anbarimda", "anbarimizda", "anbarda", "anbara", "anbarima", "elave", "et", "edin", "edek", "inventara", "inventar",
    "stoka", "bizde", "mende", "menim", "i", "have", "we", "add", "to", "inventory", "the", "my", "in", "warehouse",
    "stock", "of", "has", "got", "received", "gelib", "geldi", "teze", "yeni", "eded", "dene", "pieces", "units", "unit",
    "her", "biri", "birinin", "each", "costs", "cost", "qiymeti", "qiymet", "price", "at", "for", "with", "per", "is",
    "are", "there", "hemcinin", "da", "de", "also", "elave-et", "qeyd", "yaz", "please", "zehmet", "olmasa", "ve", "and",
    "ile", "satis", "alis", "maya", "deyeri", "purchase", "selling", "sale", "bir", "adet", "tane", "var.", "olsun",
    "bu", "gun", "bugun", "today", "dunen", "yesterday", "indi", "now", "teze", "aldim", "aldiq", "gelen", "gelib",
    "mende", "bizde", "hazirda", "elimde", "elimizde",
}


def _clean_name(original_segment: str) -> str:
    words = []
    for w in re.split(r"\s+", original_segment.strip()):
        f = fold(w).strip(".,;:!?")
        if not f or f in _FILLER or re.fullmatch(_NUM, f) or f in UNIT_ALIASES or re.fullmatch(_CURRENCY, f):
            continue
        if re.fullmatch(rf"{_NUM}(?:azn|manat|₼)", f):
            continue
        words.append(w.strip(".,;:!?"))
    if not words:
        return ""
    # English plural on the head noun: "wooden doors" -> "wooden door"
    last = words[-1]
    if re.fullmatch(r"[A-Za-z]{4,}s", last) and not last.lower().endswith("ss"):
        words[-1] = last[:-1]
    name = " ".join(words)
    return name[0].upper() + name[1:]


def _extract_prices(folded: str) -> tuple[str | None, str | None]:
    """Return (selling_price, purchase_price) found in a folded segment."""
    selling = purchase = None
    m = re.search(rf"(?:alis|purchase|maya deyeri|maya)\D{{0,25}}?({_NUM})", folded)
    if m:
        purchase = _num(m.group(1))
        folded = folded[: m.start()] + " " + folded[m.end():]
    m = (re.search(rf"(?:satis qiymeti|selling price|satis)\D{{0,20}}?({_NUM})", folded)
         or re.search(rf"({_NUM})\s*{_CURRENCY}", folded)
         or re.search(rf"(?:qiymeti|price|costs?|her biri|each|fiyati)\D{{0,20}}?({_NUM})", folded))
    if m:
        selling = _num(m.group(1))
    return selling, purchase


def split_items(text: str) -> list[str]:
    parts = re.split(r",(?!\d)|;|\n|[.!?](?=\s|$)|\s+(?:və|ve|and|həmçinin|hemçinin|also)\s+", text, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p and p.strip()]


def parse_inventory_items(text: str) -> list[dict[str, Any]]:
    """'Anbarımda 500 taxta qapı, 300 taxta lövhə və 120 laminat panel var. Hər biri 85 AZN'"""
    global_sell, global_buy = _extract_prices(unit_phrase_replace(fold(text)))
    items: list[dict[str, Any]] = []
    price_only_segments = 0
    for seg in split_items(text):
        f = unit_phrase_replace(fold(seg))
        seg_sell, seg_buy = _extract_prices(f)
        # quantity = first number that is not part of a price expression
        f_wo_prices = re.sub(rf"(?:alis|purchase|maya|satis|qiymeti|price|costs?|her biri|each)\D{{0,25}}?{_NUM}", " ", f)
        f_wo_prices = re.sub(rf"{_NUM}\s*{_CURRENCY}", " ", f_wo_prices)
        qm = re.search(rf"({_NUM})\s*([a-z0-9.]+)?", f_wo_prices)
        if not qm:
            if seg_sell or seg_buy:
                price_only_segments += 1
            continue
        unit = UNIT_ALIASES.get((qm.group(2) or "").rstrip("."))
        orig_wo_numbers = re.sub(rf"\b{_NUM}\s*(?:azn|manat|₼)?", " ", seg, flags=re.IGNORECASE)
        # remove unit words from the original segment
        name = _clean_name(re.sub(r"(?i)\b(kvadrat metr|kv\.? ?m|m²|m2|m³|m3|square meters?|sq\.? m)\b", " ", orig_wo_numbers))
        if not name:
            continue
        items.append({"name": name, "quantity": _num(qm.group(1)), "unit": unit or "piece",
                      "selling_price": seg_sell, "purchase_price": seg_buy})
    # a price stated once for everything ("hər biri 85 AZN") applies to items without their own price
    if items and (global_sell or global_buy):
        explicit = [i for i in items if i["selling_price"] or i["purchase_price"]]
        if not explicit or price_only_segments:
            for i in items:
                i["selling_price"] = i["selling_price"] or global_sell
                i["purchase_price"] = i["purchase_price"] or global_buy
    return items


class RuleBasedProvider:
    """Executes the declarative rule table in app/ai/rules.py («ƏGƏR … → ONDA …»)."""

    name = "offline_parser"
    model = "hesabatim-offline-rules-v1"

    def stream(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Iterator[ProviderEvent]:
        from app.ai.nlu import Context
        from app.ai.rules import Reply, plan

        tool_names = {t["function"]["name"] for t in tools}
        last = messages[-1]
        if last["role"] == "tool":
            yield ProviderEvent(type="text", text=self._summarize(messages))
            return
        result, _rule = plan(last.get("content") or "", "inventory", Context.from_messages(messages))
        if isinstance(result, Reply):
            yield ProviderEvent(type="text", text=result.text)
            return
        f = fold(last.get("content") or "")
        for call in result:
            if call.tool == "get_sales_analytics":
                for phrase, period in (("bu gun", "today"), ("bugun", "today"), ("bu hefte", "this_week"),
                                       ("bu ay", "this_month"), ("kecen ay", "last_month")):
                    if phrase in f:
                        call.args["period"] = period
                        break
                if "muqayise" in f:
                    call.args.update(period="this_month", compare=True)
        yield ProviderEvent(type="tool_calls", tool_calls=[
            ToolCall(id=f"call_{i}", name=c.tool, arguments=json.dumps(c.args, ensure_ascii=False))
            for i, c in enumerate(result) if c.tool in tool_names
        ])

    # ------------------------------------------------------------------ summaries
    def _summarize(self, messages: list[dict[str, Any]]) -> str:
        results: list[tuple[str, dict]] = []
        i = len(messages) - 1
        while i >= 0 and messages[i]["role"] == "tool":
            try:
                results.insert(0, (messages[i].get("name", ""), json.loads(messages[i]["content"])))
            except (json.JSONDecodeError, TypeError):
                pass
            i -= 1
        lines: list[str] = []
        pending = [r for n, r in results if r.get("status") == "pending_confirmation"]
        for name, r in results:
            if r.get("error") or r.get("status") == "pending_confirmation":
                continue
            lines.append(_summary_for(name, r))
        if pending:
            n = len(pending)
            missing = any(p.get("missing") for p in pending)
            warned = any(p.get("warnings") for p in pending)
            lines.append(
                (f"{n} əməliyyat hazırladım." if n > 1 else "Əməliyyatı hazırladım.")
                + " Zəhmət olmasa, aşağıdakı kartda məlumatları yoxlayın və **Təsdiqlə** düyməsini basın. "
                + "Təsdiqlənənə qədər heç bir dəyişiklik edilmir."
                + (" Bəzi məlumatlar çatışmır: kartda əlavə edin." if missing else "")
                + (" Kartdakı xəbərdarlıqlara diqqət edin." if warned else "")
            )
        for name, r in results:
            if r.get("error"):
                hint = ""
                if r.get("code") == "product_not_found" and name in ("update_price", "update_inventory", "update_product",
                                                                     "record_sale"):
                    hint = (" Adı inventardakı kimi yazın (məs. «Qapı») və ya əvvəlcə məhsulu əlavə edin: "
                            "«Məndə 50 … var».")
                lines.append(f"⚠️ {r['error']}{hint}")
        return "\n\n".join(l for l in lines if l) or "Hazırdır."


def _cur(r: dict) -> str:
    return {"AZN": "₼", "USD": "$", "EUR": "€", "TRY": "₺", "RUB": "₽", "GBP": "£"}.get(r.get("currency") or "AZN", "₼")


def _summary_for(name: str, r: dict) -> str:
    if name == "search_inventory":
        if not r.get("total"):
            return "Bu meyarlara uyğun məhsul inventarınızda tapılmadı."
        items = r.get("items") or []
        if 0 < len(items) <= 3:
            rows = [f"- **{i['name']}**: {i['quantity']} {UNIT_LABEL_AZ.get(i['unit'], i['unit'])}" for i in items]
            return "Hazırda inventarınızda:\n" + "\n".join(rows)
        return f"İnventarınızda {r['total']} uyğun məhsul tapdım."
    if name == "get_inventory_summary":
        c = _cur(r)
        value = f"Alış qiyməti ilə inventar dəyəri: {r['inventory_value']} {c}."
        if r.get("products_without_cost"):
            value += f" ({r['products_without_cost']} məhsulun alış qiyməti qeyd olunmayıb, dəyərə daxil deyil.)"
        return (f"İnventarınızda {r['total_products']} məhsul, cəmi {r['total_units']} vahid var. {value} "
                f"Stoku az olan məhsul sayı: {r['low_stock_count']}.")
    if name == "get_sales_analytics":
        items = r.get("items") or []
        if r.get("metric") == "unsold":
            return (f"Son {r['days']} gündə satılmayan {len(items)} məhsul tapdım." if items
                    else f"Son {r['days']} gündə stokda olan bütün məhsullar ən azı bir dəfə satılıb.")
        t = r.get("totals") or {}
        c = _cur(r)
        if not t.get("sales_count") and not t.get("returns_count"):
            return f"Son {r['days']} gündə qeydə alınmış satış yoxdur, ona görə də statistika boşdur."
        head = (f"Son {r['days']} gündə {t['sales_count']} satış, xalis gəlir {t['revenue']} {c}, "
                f"satılan miqdar {t['units_sold']} vahid.")
        if not t.get("cost_complete", True):
            profit = (f" Ümumi mənfəət məlum deyil. Alış qiyməti məlum olan satışlar üzrə mənfəət {t['known_cost_gross_profit']} {c}; "
                      f"{t['revenue_without_cost']} {c} gəlirin maya dəyəri qeyd olunmayıb, mənfəətə daxil edilmədi.")
        else:
            profit = f" Maya dəyəri {t['cost_of_goods']} {c}, ümumi mənfəət {t['gross_profit']} {c}."
        if r.get("metric") == "summary" or not items:
            previous = r.get("previous_period")
            comparison = f" Keçən ayın xalis gəliri: {previous['revenue']} {c}." if previous else ""
            return head + profit + comparison
        top = items[0]
        label = "ən çox satılan" if r.get("metric") == "top_units" else "ən çox gəlir gətirən"
        return (head + f" Siyahıda {label} məhsul: **{top['name']}** ({top['units']} "
                f"{UNIT_LABEL_AZ.get(top['unit'], top['unit'])}, {top['revenue']} {c}).")
    if name == "get_reorder_suggestions":
        items = r.get("items") or []
        b = r.get("basis") or {}
        if not items:
            return (f"Hazırda yenidən sifariş tələb edən məhsul yoxdur: bütün məhsullar minimum həddən yuxarıdır və "
                    f"son {b.get('days', 30)} günün satış sürətinə görə stok ən azı {b.get('cover_days', 14)} günə çatır.")
        lines = []
        for i in items[:5]:
            unit = UNIT_LABEL_AZ.get(i["unit"], i["unit"])
            why = []
            if "out_of_stock" in i["reasons"]:
                why.append("stok bitib")
            if "below_threshold" in i["reasons"]:
                why.append(f"minimum hədd {i['threshold']}")
            if i.get("days_of_cover"):
                why.append(f"satış sürəti ilə ~{i['days_of_cover']} günə çatır")
            lines.append(f"- **{i['name']}**: qalıq {i['quantity']} {unit}, son {b.get('days', 30)} gündə "
                         f"{i['sold_units']} satılıb ({'; '.join(why)}). Təklif: **{i['suggested_quantity']} {unit}**")
        return "Yenidən sifariş üçün tövsiyələr:\n" + "\n".join(lines)
    return ""
