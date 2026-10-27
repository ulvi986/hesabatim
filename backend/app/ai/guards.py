"""Guards: assertions applied to every tool call before it runs, in both assistant modes.

Each guard is «ƏGƏR alət çağırışı belədirsə → ONDA dayandır / düzəlt / xəbərdar et». They run after
Pydantic validation and before the read/preview step, so neither a weak offline parse nor a confused
LLM gets past them. Their `rule` sentences are also shown to the LLM in its system prompt.

* block — the call is refused and the message goes back to the model and the user.
* fix   — the arguments are corrected (patch) and the card says why.
* warn  — the call goes ahead; the warning is shown on the confirmation card (or passed to the model).

`ungrounded_amounts` is the matching assertion for the assistant's *text*: every money amount it
states must appear in tool results or the conversation, otherwise the UI shows a warning.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from app.ai.nlu import REF_DROP_EXACT
from app.ai.tools import ToolContext, resolve_product
from app.core.errors import AppError
from app.services import products as products_svc
from app.services.common import qty_str
from app.services.lexicon import UNIT_LABEL_AZ, fold

BIG_QUANTITY = Decimal(100_000)
PRICE_JUMP = Decimal("0.5")  # ±50 %


@dataclass(frozen=True)
class Verdict:
    level: Literal["block", "fix", "warn"]
    message: str
    patch: dict[str, Any] | None = None


@dataclass(frozen=True)
class Guard:
    id: str
    tools: frozenset[str]
    rule: str  # human-readable assertion, also rendered into the LLM prompt
    check: Callable[[ToolContext, Any], Verdict | None]


def _money(ctx: ToolContext, d: Decimal | None) -> str:
    return f"{Decimal(d).quantize(Decimal('0.01'))} {ctx.sign}" if d is not None else "—"


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
_JUNK = REF_DROP_EXACT | {"mehsul", "product", "mal", "sey", "item", "items", "things", "bu", "gun", "geldi", "var"}


def _name_meaningful(ctx: ToolContext, a: Any) -> Verdict | None:
    words = [w for w in re.split(r"[\s,./-]+", fold(a.name)) if w]
    meaningful = [w for w in words if len(w) >= 2 and not w.isdigit() and w not in _JUNK]
    if not meaningful:
        return Verdict("block", f"Məhsulun adı aydın deyil («{a.name}»). Adı yazın, məsələn: «Taxta qapı».")
    if len(words) > 8:
        return Verdict("warn", "Məhsulun adı çox uzundur — cümlənin bir hissəsi ada düşmüş ola bilər. Kartda adı yoxlayın.")
    return None


def _exact_duplicate(ctx: ToolContext, a: Any) -> Verdict | None:
    if a.merge_into_product_id:
        return None
    for p, _score in products_svc.find_matching_products(ctx.db, ctx.business_id, a.name, limit=5):
        if fold(p.name) == fold(a.name):
            before = Decimal(p.inventory.quantity if p.inventory else 0)
            unit = UNIT_LABEL_AZ.get(p.unit, p.unit)
            return Verdict("fix", f"«{p.name}» artıq inventarınızda var ({qty_str(before)} {unit}). Yeni məhsul yaradılmayacaq — "
                                  f"miqdar mövcud məhsula əlavə olunacaq: {qty_str(before)} → {qty_str(before + a.quantity)}. "
                                  f"Ümumi miqdarı dəyişmək istəyirsinizsə, «{p.name} sayı {qty_str(a.quantity)} olsun» yazın.",
                           patch={"merge_into_product_id": str(p.id)})
    return None


def _quantity_plausible(ctx: ToolContext, a: Any) -> Verdict | None:
    values = [getattr(a, f, None) for f in ("quantity", "change", "set_quantity")]
    big = [abs(Decimal(v)) for v in values if v is not None and abs(Decimal(v)) > BIG_QUANTITY]
    if big:
        return Verdict("warn", f"Miqdar çox böyükdür ({qty_str(big[0])}). Rəqəmi yoxlayın — artıq sıfır yazılmış ola bilər.")
    return None


def _price_zero(ctx: ToolContext, a: Any) -> Verdict | None:
    if any(getattr(a, f, None) == 0 for f in ("selling_price", "purchase_price", "unit_price")):
        return Verdict("warn", f"Qiymət 0 {ctx.sign} göstərilib. Pulsuz deyilsə, qiyməti düzəldin.")
    return None


def _price_jump(ctx: ToolContext, a: Any) -> Verdict | None:
    try:
        p = resolve_product(ctx, a.product)
    except AppError:
        return None  # the preview reports "not found" / "ambiguous" itself
    notes = []
    for field, label in (("selling_price", "Satış"), ("purchase_price", "Alış")):
        new, old = getattr(a, field), getattr(p, field)
        if new is None or not old:
            continue
        change = (Decimal(new) - Decimal(old)) / Decimal(old)
        if abs(change) > PRICE_JUMP:
            notes.append(f"{label} qiyməti {_money(ctx, old)} → {_money(ctx, new)} ({change * 100:+.0f}%)")
    if notes:
        return Verdict("warn", "Böyük qiymət dəyişikliyi: " + "; ".join(notes) + ". Rəqəmi yoxlayın.")
    return None


def _stock_jump(ctx: ToolContext, a: Any) -> Verdict | None:
    if a.set_quantity is None:
        return None
    try:
        p = resolve_product(ctx, a.product)
    except AppError:
        return None
    current = Decimal(p.inventory.quantity if p.inventory else 0)
    if current > 0 and a.set_quantity > current * 10 and a.set_quantity > 100:
        return Verdict("warn", f"Yeni miqdar hazırkından 10 dəfədən çoxdur ({qty_str(current)} → {qty_str(a.set_quantity)}). "
                               "Əlavə etmək nəzərdə tutulubsa, «… 20 ədəd artır» yazın.")
    return None


def _sale_price_vs_list(ctx: ToolContext, a: Any) -> Verdict | None:
    if a.unit_price is None:
        return None
    try:
        p = resolve_product(ctx, a.product)
    except AppError:
        return None
    if p.selling_price:
        change = (Decimal(a.unit_price) - Decimal(p.selling_price)) / Decimal(p.selling_price)
        if abs(change) > PRICE_JUMP:
            return Verdict("warn", f"Satış qiyməti ({_money(ctx, a.unit_price)}) məhsulun qeydə alınmış qiymətindən "
                                   f"({_money(ctx, p.selling_price)}) {change * 100:+.0f}% fərqlənir. Rəqəmi yoxlayın.")
    if p.purchase_price is not None and Decimal(a.unit_price) < Decimal(p.purchase_price):
        return Verdict("warn", f"Satış qiyməti alış qiymətindən ({_money(ctx, p.purchase_price)}) aşağıdır: bu satış zərərlidir.")
    return None


GUARDS: list[Guard] = [
    Guard("product.name_meaningful", frozenset({"create_product"}),
          "məhsulun adı real məhsul adı olmalıdır (yalnız «var», «bu gün», rəqəm və s. deyil); əks halda dayandırılır",
          _name_meaningful),
    Guard("product.exact_duplicate", frozenset({"create_product"}),
          "eyni adlı məhsul artıq varsa, yeni məhsul yaradılmır: miqdar mövcud məhsula əlavə olunur və istifadəçiyə deyilir",
          _exact_duplicate),
    Guard("quantity.plausible", frozenset({"create_product", "update_inventory", "record_sale"}),
          "100 000-dən böyük miqdar xəbərdarlıqla göstərilir", _quantity_plausible),
    Guard("price.zero", frozenset({"create_product", "update_price", "record_sale"}),
          "0 qiymət xəbərdarlıqla göstərilir", _price_zero),
    Guard("price.jump", frozenset({"update_price"}),
          "qiymət hazırkından 50%-dən çox dəyişirsə, xəbərdarlıq göstərilir", _price_jump),
    Guard("stock.jump", frozenset({"update_inventory"}),
          "miqdar hazırkından 10 dəfədən çox artırılırsa, xəbərdarlıq göstərilir", _stock_jump),
    Guard("sale.price_check", frozenset({"record_sale"}),
          "satış qiyməti məhsulun qiymətindən 50%-dən çox fərqlənirsə və ya alış qiymətindən aşağıdırsa, xəbərdarlıq göstərilir",
          _sale_price_vs_list),
]


def apply_guards(ctx: ToolContext, tool: str, args: Any, revalidate: Callable[[dict[str, Any]], Any]
                 ) -> tuple[Any, list[str], str | None]:
    """Run the guards for one call. Returns (possibly fixed args, warnings, block message or None)."""
    warnings: list[str] = []
    for guard in GUARDS:
        if tool not in guard.tools:
            continue
        verdict = guard.check(ctx, args)
        if verdict is None:
            continue
        if verdict.level == "block":
            return args, warnings, verdict.message
        if verdict.level == "fix" and verdict.patch:
            args = revalidate({**args.model_dump(exclude_none=True), **verdict.patch})
        warnings.append(verdict.message)
    return args, warnings, None


# ---------------------------------------------------------------------------
# Text assertion: no invented money amounts
# ---------------------------------------------------------------------------
_AMOUNT = re.compile(r"(\d{1,3}(?:[  ,]\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?)\s*(?:₼|AZN|manat|USD|\$|EUR|€|TRY|₺|RUB|₽|GBP|£)",
                     re.IGNORECASE)
_ANY_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _to_decimal(raw: str) -> Decimal | None:
    s = raw.replace(" ", " ")
    if re.fullmatch(r"\d{1,3}(?:[ ,]\d{3})+(?:\.\d+)?", s):
        s = s.replace(" ", "").replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def ungrounded_amounts(text: str, evidence: list[str]) -> list[str]:
    """Money amounts stated in `text` that appear nowhere in `evidence` (tool results, user messages)."""
    known: set[Decimal] = set()
    for chunk in evidence:
        for m in _ANY_NUMBER.findall(chunk or ""):
            known.add(Decimal(m))
        for m in _AMOUNT.finditer(chunk or ""):
            d = _to_decimal(m.group(1))
            if d is not None:
                known.add(d)
    out = []
    for m in _AMOUNT.finditer(text or ""):
        d = _to_decimal(m.group(1))
        if d is not None and d not in known:
            out.append(m.group(0).strip())
    return out
