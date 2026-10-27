"""Assistant behaviour rules: «ƏGƏR belədirsə → ONDA belə et».

One declarative table drives both assistant modes:

* **Offline mode** executes it. Rules are tried from the highest priority down; the first rule whose
  `match` is true and whose `act` returns a plan wins (`act` may return None to let lower rules try).
* **LLM mode** receives the same `when` / `then` sentences and examples in its system prompt
  (see prompts.py), so both modes are told the same thing.
* Every example is an **assertion**: tests/test_rules.py runs each one and checks that this exact rule
  fires and produces exactly these tool calls (or a reply containing the given text).

Adding a behaviour = adding a Rule with a priority, a `when`/`then` sentence, `match`, `act` and at
least one example. Safety does not depend on these rules: tool arguments are validated, tenant
scoping is enforced in code, writes need confirmation, and guards.py re-checks every tool call.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.ai.nlu import NUM, Context, Utterance, plain

Mode = str  # "inventory" | "any"


@dataclass(frozen=True)
class Call:
    tool: str
    args: dict[str, Any]


@dataclass(frozen=True)
class Reply:
    text: str


Plan = list[Call] | Reply


@dataclass(frozen=True)
class Example:
    say: str
    calls: tuple[tuple[str, dict[str, Any]], ...] = ()
    reply: str | None = None  # expected substring of the reply when no tool is called
    context: str = ""  # an earlier assistant message, including its "[context: …]" line
    prompt: bool = True  # also show this example to the LLM


def ex(say: str, *calls: tuple[str, dict[str, Any]], reply: str | None = None, context: str = "",
       prompt: bool = True) -> Example:
    return Example(say, tuple(calls), reply, context, prompt)


@dataclass(frozen=True)
class Rule:
    id: str
    mode: Mode
    priority: int
    when: str
    then: str
    match: Callable[[Utterance], bool]
    act: Callable[[Utterance], Plan | None]
    examples: tuple[Example, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Vocabulary (folded: ə→e, ı→i, ş→s, ç→c, ğ→g, ö→o, ü→u). Matching is by word start.
# ---------------------------------------------------------------------------
FILLER = {"ve", "men", "mene", "sen", "siz", "bir", "cox", "da", "de", "hem", "ki", "e", "ya", "dostum", "qardas",
          "hormetli", "xanim", "bey", "muellim", "bele", "ele", "hele", "bes"}
GREET = {"salam", "slm", "salamlar", "hello", "hi", "hey", "merhaba", "selam", "sabahiniz", "sabah", "xeyir", "axsaminiz",
         "gunortaniz", "privet", "aleykum", "eleykum", "necesen", "necesiz", "nasilsin", "nasilsiniz", "how", "are", "you",
         "yaxsisan", "yaxsisiniz", "good", "morning", "evening", "neynirsen", "isler", "nece", "gedir"}
THANKS = {"sag", "ol", "olun", "sagol", "sagolun", "tesekkur", "tesekkurler", "edirem", "edirik", "thanks", "thank",
          "you", "mersi", "eyvallah", "super", "ela", "yaxsi", "oldu", "ok", "okay", "oke", "tamam", "anladim",
          "aydindir", "aydin", "cox", "gozel", "great", "nice", "perfect", "minnetdaram"}
CAPABILITY = ("ne ede bilirsen", "ne edirsen", "ne bacarirsan", "nece komek", "komek et", "komek ede", "help", "yardim",
              "kimsen", "sen kimsen", "what can you do", "who are you", "nece isleyir", "nece istifade", "ne yaza bilerem")

PRICE = ("qiymet", "price", "fiyat", "deyeri", "deyerini")
ADD = ("elave", "anbarimda", "anbarimiza", "anbarimizda", "anbara", "anbarda", "var", "geldi", "gelib", "aldim", "aldiq",
       "add", "received", "have", "got", "teze mal", "yeni mal", "mehsul gel", "inventara", "stoka", "mende", "bizde")
SALE = ("satdim", "satdiq", "satildi", "satmisam", "satmisiq", "sattim", "sold", "sell ", "satis etdim", "satis oldu")
DEC = ("azalt", "cixart", "cixdi", "reduce", "decrease", "remove", "minus", "istifade", "xarab", "itdi", "sindi", "dus",
       "silin")
INC = ("artir", "increase", "add to stock", "plus")
STOCK = ("sayi", "sayin", "miqdar", "stok", "qaliq", "quantity", "stock", "count")
SET = ("olsun", "et", "edin", "ele", "to", "set", "yaz", "teyin", "deyis", "olmalidir", "edek")
THRESHOLD = ("minimum", "min stok", "min. stok", "hedd", "threshold", "xeberdarliq", "alert", "bildiris")
RELATIVE = ("artir", "azalt", "endir", "qaldir", "increase", "decrease", "reduce", "raise", "lower", "by")
SHOW = ("goster", "show", "axtar", "tap", "find", "list", "hansi", "which", "search", "bax", "sadala")
HOW_MANY = ("ne qeder", "nece ", "neçe", "how many", "how much", "qalib", "qalir", "var mi", "varmi", "movcud", "stokda",
            "anbarda")
TOP = ("en cox satilan", "en cox satan", "en cox gelir", "en cok satan", "most sold", "best sell", "top sell", "top ",
       "gelir", "revenue", "qazan", "menfeet", "xeyir etdim", "profit", "ne satil", "neler satil", "what sold",
       "what did we sell", "satislar", "satis analitika", "satis statistika", "sales", "dovriyye", "satis hesabat")
RANKING = ("en cox", "en cok", "most", "best", "top", "hansi", "which", "siyahi")
UNSOLD = ("satilmayan", "satilmir", "satilmamis", "satilmadi", "not sold", "haven't sold", "havent sold", "unsold",
          "hasn't sold", "slow", "durğun", "durgun", "satilmayib")
REORDER = ("yeniden sifaris", "sifaris vermeli", "sifaris etmeli", "sifaris verim", "sifaris edim", "reorder", "restock",
           "almaliyam", "almaliyiq", "yeniden almali", "ne almali", "tedaruk", "yenilemeliyem", "bitmemis almali")
SUMMARY = ("xulase", "summary", "umumi", "overview", "veziyyet", "vezziyyet", "nece mehsul", "how many products",
           "inventory value", "inventar deyeri", "inventarin deyeri", "inventarimin", "ne qeder mehsul")
LOW = ("az qalan", "azalan", "low stock", "bitmek uzre", "bitmek", "biten", "bitib", "stokda yox", "out of stock",
       "az olan", "tukenen", "running out", "qurtar")
DELETE = ("sil", "delete", "arxivle", "arxive", "arxivə")
CANCEL = ("legv", "cancel", "geri qaytar", "geri al", "iptal")
SALE_NUMBER = re.compile(r"(?<![a-z0-9])s-?(\d{1,6})(?![0-9])")

HELP = (
    "Mən inventarınızı və satışlarınızı idarə etməyə kömək edirəm. Məsələn yaza bilərsiniz:\n\n"
    "- «Məndə 500 qapı, 200 masa və 350 taxta var»\n"
    "- «100 yeni qapı gəldi»\n"
    "- «20 qapını hərəsini 150 AZN-ə satdım»\n"
    "- «Hazırda neçə qapım qalıb?»\n"
    "- «Son 30 gündə ən çox satılan məhsullar hansılardır?»\n"
    "- «Hansı məhsullardan yenidən sifariş verməliyəm?»"
)


def _money_fmt(d: Decimal) -> str:
    return plain(d) or ""


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
def _price_amounts(u: Utterance) -> list[Decimal]:
    if u.money:
        return u.money
    if u.has(*PRICE):
        return [q.value for q in u.quantities if q.unit is None]
    return []


def _is_price_change(u: Utterance) -> bool:
    has_amount = bool(_price_amounts(u))
    stock_qty = [q for q in u.quantities if q.unit] or (u.money and u.quantities)
    return has_amount and not stock_qty and not u.has(*ADD) and not u.has(*STOCK)


def act_price(u: Utterance) -> Plan | None:
    amounts = _price_amounts(u)
    ref = u.ref()
    if not ref:
        return Reply(f"Hansı məhsulun qiymətini {_money_fmt(amounts[0])} edim? Məhsulun adını da yazın, məsələn: "
                     f"«Qapının qiymətini {_money_fmt(amounts[0])} AZN et».")
    args: dict[str, Any] = {"product": ref}
    m_buy = re.search(rf"(?:alis|maya|purchase|cost price)\D{{0,25}}?({NUM})", u.f)
    m_sell = re.search(rf"(?:satis|selling|sale price)\D{{0,25}}?({NUM})", u.f)
    if m_buy:
        args["purchase_price"] = plain(Decimal(m_buy.group(1).replace(",", ".")))
    if m_sell:
        args["selling_price"] = plain(Decimal(m_sell.group(1).replace(",", ".")))
    if not m_buy and not m_sell:
        args["selling_price"] = plain(amounts[0])
    return [Call("update_price", args)]


def _is_relative_price(u: Utterance) -> bool:
    if not u.has(*PRICE):
        return False
    if u.percent:
        return True
    return bool(_price_amounts(u)) and u.has(*RELATIVE) and not re.search(r"manata|azn-?[ea]\b|\bto\b", u.f)


def act_relative_price(u: Utterance) -> Plan:
    ref = u.ref(extra_drop=RELATIVE) or "məhsulun"
    return Reply(f"Qiymətləri faizlə və ya «… manat artır» kimi nisbi dəyişmirəm, çünki səhv hesablama riski var. "
                 f"Yeni qiyməti dəqiq yazın, məsələn: «{ref} qiymətini 95 AZN et».")


def act_price_ask(u: Utterance) -> Plan:
    ref = u.ref()
    who = f"«{ref}» üçün yeni" if ref else "Yeni"
    return Reply(f"{who} qiyməti yazın, məsələn: «Qapının qiymətini 90 AZN et». "
                 "Alış qiymətini dəyişmək üçün «alış qiyməti» deyin.")


def act_threshold(u: Utterance) -> Plan:
    ref = u.ref()
    n = u.quantities[0].value
    if not ref:
        return Reply(f"Hansı məhsul üçün minimum stok həddini {plain(n)} edim?")
    return [Call("update_product", {"product": ref, "low_stock_threshold": int(n)})]


def act_stock_set(u: Utterance) -> Plan:
    ref = u.ref()
    n = u.quantities[0].value
    if not ref:
        return Reply(f"Hansı məhsulun miqdarını {plain(n)} edim? Məhsulun adını yazın.")
    return [Call("update_inventory", {"product": ref, "set_quantity": plain(n)})]


def act_stock_change(u: Utterance) -> Plan:
    ref = u.ref()
    n = u.quantities[0].value
    sign = "-" if u.has(*DEC) else ""
    if not ref:
        return Reply(f"Hansı məhsulun stokunu {plain(n)} {'azaldım' if sign else 'artırım'}? Məhsulun adını yazın.")
    return [Call("update_inventory", {"product": ref, "change": f"{sign}{plain(n)}", "reason": "AI köməkçi: " + u.raw[:120]})]


SALE_DROP = ("heresi", "hereye", "her biri", "her birin", "tanesi", "denesi", "edesi", "eded", "musteri")


def act_sale(u: Utterance) -> Plan:
    """«20 qapını hərəsini 150 AZN-ə satdım», «20 qapı və 5 masa satdım» → one record_sale per product.
    A unit price is passed only when the user stated it; otherwise the card proposes the recorded price."""
    from app.ai.providers.rule_based import split_items

    parts = [Utterance(seg, u.context, u.mode) for seg in split_items(u.raw)]
    parts = [p for p in parts if p.quantities] or [u]
    shared_price = u.money[0] if len(u.money) == 1 and len(parts) > 1 and u.has("her biri", "heresi", "each") else None
    calls: list[Call] = []
    for p in parts:
        if not p.quantities:
            continue
        ref = p.ref(extra_drop=SALE_DROP, use_context=len(parts) == 1)
        if not ref:
            return Reply(f"Hansı məhsuldan {plain(p.quantities[0].value)} ədəd satdınız? Məhsulun adını yazın, "
                         "məsələn: «20 qapı satdım, hərəsi 150 AZN».")
        args: dict[str, Any] = {"product": ref, "quantity": plain(p.quantities[0].value)}
        price = p.money[0] if p.money else shared_price
        if price is not None:
            args["unit_price"] = plain(price)
        calls.append(Call("record_sale", args))
    if not calls:
        return Reply("Neçə ədəd və hansı məhsulu satdınız? Məsələn: «20 qapı satdım, hərəsi 150 AZN».")
    return calls


def act_sale_noqty(u: Utterance) -> Plan:
    ref = u.ref(extra_drop=SALE_DROP, use_context=False)
    what = f"«{ref}» məhsulundan" if ref else "Hansı məhsuldan"
    return Reply(f"{what} neçə ədəd satdınız? Miqdarı və mümkünsə qiyməti yazın, məsələn: «20 qapı satdım, hərəsi 150 AZN».")


def act_cancel_sale(u: Utterance) -> Plan:
    m = SALE_NUMBER.search(u.f)
    return [Call("cancel_sale", {"sale_number": f"S-{int(m.group(1)):06d}"})]


def act_analytics(u: Utterance) -> Plan:
    if u.has("en cox gelir", "gelir getiren", "en cox qazanc", "top revenue", "most revenue"):
        metric = "top_revenue"
    elif u.has(*RANKING) or u.has("ne satil", "neler satil", "what sold", "what did we sell"):
        metric = "top_units"
    else:
        metric = "summary"
    return [Call("get_sales_analytics", {"metric": metric, "days": u.days})]


def act_low_threshold(u: Utterance) -> Plan | None:
    m = re.search(rf"({NUM})\s*(?:-?(?:den|dan)\s*az|ededden az|vahidden az|deneden az|m2-?den az)", u.f) or \
        re.search(rf"(?:fewer|less|below|under)\s*(?:than)?\s*({NUM})", u.f)
    if not m:
        return None
    return [Call("search_inventory", {"max_quantity": plain(Decimal(m.group(1).replace(",", "."))), "limit": 50})]


def act_add(u: Utterance) -> Plan | None:
    from app.ai.providers.rule_based import parse_inventory_items

    items = parse_inventory_items(u.raw)
    if not items:
        return None
    return [Call("create_product", {k: v for k, v in i.items() if v is not None}) for i in items[:20]]


def act_add_without_quantity(u: Utterance) -> Plan:
    ref = u.ref(use_context=False) or ""
    return Reply(f"«{ref}» məhsulundan neçə ədəd əlavə edim? Miqdarı və mümkünsə qiyməti yazın, məsələn: "
                 f"«Məndə 50 {ref.lower()} var, hər biri 85 AZN».")


def act_stock_query(u: Utterance) -> Plan:
    ref = u.ref(use_context=False)
    if not ref:
        return [Call("get_inventory_summary", {})]
    return [Call("search_inventory", {"query": ref, "limit": 10})]


def act_search_inventory(u: Utterance) -> Plan:
    ref = u.ref(extra_drop=("butun", "hamisi", "all", "hamisini", "list", "hansi", "hans", "mal", "mallar", "mallarim",
                                 "mallarimi", "mallarimiz", "mallari"), use_context=False)
    return [Call("search_inventory", {"query": ref, "limit": 30} if ref else {"limit": 30})]


def act_lookup(u: Utterance) -> Plan | None:
    ref = u.ref(use_context=False)
    return [Call("search_inventory", {"query": ref, "limit": 10})] if ref else None


# ---------------------------------------------------------------------------
# The rule table
# ---------------------------------------------------------------------------
PRODUCT_CTX = "İnventarınızda 1 uyğun məhsul tapdım.\n[context: product=Qapı]"

RULES: list[Rule] = [
    # ---- small talk
    Rule("common.greeting", "any", 100,
         "mesaj yalnız salamlaşmadır (salam, necəsən, hello) və heç bir sorğu yoxdur",
         "alət çağırma; qısa salamlaş və nə edə biləcəyini nümunələrlə göstər",
         lambda u: u.only(GREET | FILLER) and any(t in GREET for t in u.tokens),
         lambda u: Reply("Salam! " + HELP),
         (ex("Salam", reply="Salam!"), ex("Necəsən?", reply="Salam!"))),
    Rule("common.thanks", "any", 100,
         "istifadəçi təşəkkür edir və ya «oldu/tamam» deyir",
         "alət çağırma; qısa cavab ver: «Buyurun! Başqa nə ilə kömək edim?»",
         lambda u: u.only(THANKS | GREET | FILLER) and any(t in THANKS for t in u.tokens),
         lambda u: Reply("Buyurun! Başqa nə ilə kömək edim?"),
         (ex("Sağ ol", reply="Buyurun"), ex("Çox təşəkkür edirəm, oldu", reply="Buyurun"))),
    Rule("common.capabilities", "any", 99,
         "istifadəçi nə edə bildiyini, necə işlədiyini soruşur",
         "alət çağırma; bacarıqlarını konkret nümunələrlə sadala",
         lambda u: u.has(*CAPABILITY) and not u.numbers and not u.concepts,
         lambda u: Reply(HELP),
         (ex("Nə edə bilirsən?", reply="Məsələn"),)),

    # ---- inventory & sales
    Rule("inventory.delete", "inventory", 95,
         "məhsulu silmək/arxivləmək istəyir",
         "alət çağırma; bunu köməkçinin etmədiyini və İnventar səhifəsindəki «Arxivlə» əmrini göstər",
         lambda u: u.has(*DELETE) and not u.numbers and not u.has(*CANCEL),
         lambda u: Reply("Məhsulları köməkçi silmir: geri qaytarılmaz səhvlərin qarşısını almaq üçün. **İnventar** "
                         "səhifəsində məhsulun sətirindəki menyudan **Arxivlə** seçin (arxivdən geri qaytarmaq olur)."),
         (ex("Qapını sil", reply="Arxivlə"),)),
    Rule("sales.cancel", "inventory", 93,
         "satış nömrəsini (S-…) göstərərək satışı ləğv etmək / geri qaytarmaq istəyir",
         "cancel_sale çağır: sale_number = «S-000012» formatında nömrə. Nömrə yoxdursa, nömrəni soruş",
         lambda u: u.has(*CANCEL) and bool(SALE_NUMBER.search(u.f)),
         act_cancel_sale,
         (ex("S-000012 satışını ləğv et", ("cancel_sale", {"sale_number": "S-000012"})),
          ex("S-12 satışını geri qaytar", ("cancel_sale", {"sale_number": "S-000012"}), prompt=False))),
    Rule("sales.cancel_ask", "inventory", 92,
         "satışı ləğv etmək istəyir, amma satış nömrəsi yoxdur",
         "alət çağırma; «Satışlar» səhifəsində satışın nömrəsini (S-…) tapıb yazmasını xahiş et",
         lambda u: u.has(*CANCEL) and u.has("satis", "sale", "satdig"),
         lambda u: Reply("Hansı satışı ləğv edim? **Satışlar** səhifəsində satışın nömrəsini tapın (məsələn **S-000012**) "
                         "və yazın: «S-000012 satışını ləğv et». Ləğv tarixçəni silmir: ayrıca qaytarma qeydi yaradılır."),
         (ex("Dünənki satışı ləğv et", reply="nömrəsini"),)),
    Rule("inventory.threshold", "inventory", 90,
         "minimum stok həddini / xəbərdarlıq həddini dəyişmək istəyir və rəqəm var",
         "update_product çağır: product = məhsulun adı, low_stock_threshold = rəqəm",
         lambda u: u.has(*THRESHOLD) and bool(u.quantities),
         act_threshold,
         (ex("Qapının minimum stokunu 30 et", ("update_product", {"product": "Qapı", "low_stock_threshold": 30})),)),
    Rule("inventory.price.relative", "inventory", 88,
         "qiyməti faizlə və ya «10 manat artır» kimi nisbi dəyişmək istəyir",
         "alət çağırma; yeni qiyməti dəqiq rəqəmlə yazmağı xahiş et (cari qiyməti təxmin etmə)",
         _is_relative_price,
         act_relative_price,
         (ex("Qapının qiymətini 10% artır", reply="dəqiq"),
          ex("Laminatın qiymətini 5 manat endir", reply="dəqiq", prompt=False))),
    Rule("sales.record", "inventory", 87,
         "məhsul satdığını deyir (satdım, satıldı, sold) və miqdar var",
         "hər məhsul üçün ayrıca record_sale çağır: product = məhsulun adı, quantity = satılan miqdar, unit_price YALNIZ "
         "istifadəçi qiyməti deyibsə (deyilməyibsə buraxın: kartda məhsulun qeydə alınmış qiyməti təklif olunur). "
         "Satış üçün update_inventory çağırma",
         lambda u: u.has(*SALE) and bool(u.quantities) and not u.has(*UNSOLD),
         act_sale,
         (ex("20 qapını hərəsini 150 AZN-ə satdım", ("record_sale", {"product": "qapı", "quantity": "20", "unit_price": "150"})),
          ex("Bu gün 5 masa satdım", ("record_sale", {"product": "masa", "quantity": "5"})),
          ex("20 qapı və 5 masa satdım", ("record_sale", {"product": "qapı", "quantity": "20"}),
             ("record_sale", {"product": "masa", "quantity": "5"})),
          ex("Sold 3 tables for 200 AZN each", ("record_sale", {"product": "table", "quantity": "3", "unit_price": "200"}),
             prompt=False))),
    Rule("sales.record_noqty", "inventory", 86,
         "satdığını deyir, amma miqdar yazılmayıb",
         "alət çağırma; neçə ədəd satıldığını soruş, miqdar uydurma",
         lambda u: u.has(*SALE) and not u.quantities and not u.has(*UNSOLD) and not u.has(*RANKING),
         act_sale_noqty,
         (ex("Qapı satdım", reply="neçə ədəd"),)),
    Rule("analytics.reorder", "inventory", 85,
         "hansı məhsullardan yenidən sifariş verməli / almalı olduğunu soruşur",
         "get_reorder_suggestions çağır; cavabda yalnız nəticədəki rəqəmləri və səbəbləri de",
         lambda u: u.has(*REORDER) or (u.has("sifaris") and u.has("hansi", "ne", "lazim", "vermeli", "etmeli")),
         lambda u: [Call("get_reorder_suggestions", {"days": u.days})],
         (ex("Hansı məhsullardan yenidən sifariş verməliyəm?", ("get_reorder_suggestions", {"days": 30})),
          ex("Nə almalıyam?", ("get_reorder_suggestions", {"days": 30}), prompt=False))),
    Rule("analytics.unsold", "inventory", 84,
         "satılmayan / durğun məhsulları soruşur",
         "get_sales_analytics çağır: metric=\"unsold\", days = istədiyi dövr (susmaya görə 30)",
         lambda u: u.has(*UNSOLD),
         lambda u: [Call("get_sales_analytics", {"metric": "unsold", "days": u.days})],
         (ex("Hansı məhsullar son 2 həftədə satılmayıb?", ("get_sales_analytics", {"metric": "unsold", "days": 14})),)),
    Rule("analytics.sales", "inventory", 83,
         "satış, gəlir, mənfəət, ən çox satılan məhsullar haqqında soruşur",
         "get_sales_analytics çağır: ümumi gəlir/mənfəət soruşursa metric=\"summary\", ən çox satılanlar — \"top_units\", "
         "ən çox gəlir gətirənlər — \"top_revenue\"; days: «bu həftə»=7, «bu ay»=30, «son 3 ay»=90, «bu il»=365. "
         "Rəqəmləri yalnız nəticədən götür; gəliri mənfəətlə qarışdırma",
         lambda u: (u.has(*TOP) or (u.has("satis", "satmisam") and u.has("ne qeder", "nece", "muqayise"))) and not (u.has(*DEC) and u.quantities),
         act_analytics,
         (ex("Son 30 gündə ən çox satılan məhsullar hansılardır?", ("get_sales_analytics", {"metric": "top_units", "days": 30})),
          ex("Bu ay ən çox gəlir gətirən məhsullar", ("get_sales_analytics", {"metric": "top_revenue", "days": 30})),
          ex("Bu həftə nə qədər qazanc əldə etmişəm?", ("get_sales_analytics", {"metric": "summary", "days": 7})),
          ex("Son 3 ayda mənfəətim nə qədərdir?", ("get_sales_analytics", {"metric": "summary", "days": 90}), prompt=False))),
    Rule("inventory.summary", "inventory", 80,
         "inventarın ümumi vəziyyətini, dəyərini, məhsul sayını soruşur",
         "get_inventory_summary çağır",
         lambda u: u.has(*SUMMARY) and not u.quantities,
         lambda u: [Call("get_inventory_summary", {})],
         (ex("İnventarımın ümumi dəyəri nə qədərdir?", ("get_inventory_summary", {})),
          ex("İnventar xülasəsi", ("get_inventory_summary", {}), prompt=False))),
    Rule("inventory.lowstock.threshold", "inventory", 75,
         "«N ədəddən az qalan» məhsulları soruşur",
         "search_inventory çağır: max_quantity = N",
         lambda u: bool(u.quantities) and u.has("az", "fewer", "less", "below", "under"),
         act_low_threshold,
         (ex("20 ədəddən az qalan məhsulları göstər", ("search_inventory", {"max_quantity": "20", "limit": 50})),)),
    Rule("inventory.lowstock", "inventory", 74,
         "bitmək üzrə olan / stoku az məhsulları soruşur",
         "search_inventory çağır: stock_status=\"attention\"",
         lambda u: u.has(*LOW),
         lambda u: [Call("search_inventory", {"stock_status": "attention", "limit": 50})],
         (ex("Hansı məhsullar bitmək üzrədir?", ("search_inventory", {"stock_status": "attention", "limit": 50})),)),
    Rule("inventory.stock.change", "inventory", 70,
         "satış OLMAYAN stok düzəlişi: artırmaq/azaltmaq (xarab oldu, itdi, azalt, artır) və miqdar var",
         "update_inventory çağır: product = məhsulun adı, change = işarəli miqdar (azalanda mənfi), reason = istifadəçinin sözləri",
         lambda u: (u.has(*DEC) or u.has(*INC)) and bool(u.quantities) and not u.has(*PRICE),
         act_stock_change,
         (ex("Qapıdan 3 dənə xarab oldu", ("update_inventory", {"product": "Qapı", "change": "-3",
                                                               "reason": "AI köməkçi: Qapıdan 3 dənə xarab oldu"})),
          ex("Masanı 15 ədəd artır", ("update_inventory", {"product": "Masa", "change": "15",
                                                          "reason": "AI köməkçi: Masanı 15 ədəd artır"})),
          ex("Reduce the stock of white MDF door by 40 units",
             ("update_inventory", {"product": "white MDF door", "change": "-40",
                                   "reason": "AI köməkçi: Reduce the stock of white MDF door by 40 units"}), prompt=False))),
    Rule("inventory.stock.set", "inventory", 68,
         "mövcud məhsulun miqdarını konkret rəqəmə bərabər etmək istəyir («sayı 250 olsun»)",
         "update_inventory çağır: product = məhsulun adı, set_quantity = rəqəm",
         lambda u: bool(u.quantities) and not u.money and u.has(*SET) and (u.has(*STOCK) or u.has("olsun", "olmalidir"))
         and not u.has(*ADD),
         act_stock_set,
         (ex("Taxtanın sayı 250 olsun", ("update_inventory", {"product": "Taxta", "set_quantity": "250"})),
          ex("Onun stokunu 120 et", ("update_inventory", {"product": "Qapı", "set_quantity": "120"}),
             context=PRODUCT_CTX))),
    Rule("inventory.price.set", "inventory", 65,
         "mövcud məhsulun qiymətini dəyişir: məbləğ (manat/AZN) və ya «qiymət» sözü + rəqəm var, miqdar yoxdur",
         "update_price çağır: product = məhsulun adı, selling_price = məbləğ; «alış»/«maya» deyilibsə purchase_price",
         _is_price_change,
         act_price,
         (ex("Qapı 90 manat olsun", ("update_price", {"product": "Qapı", "selling_price": "90"})),
          ex("Qapının qiymətini 90 manata qaldır", ("update_price", {"product": "Qapı", "selling_price": "90"})),
          ex("Masanın alış qiyməti 60 manatdır", ("update_price", {"product": "Masa", "purchase_price": "60"})),
          ex("Onun qiymətini 95 AZN et", ("update_price", {"product": "Qapı", "selling_price": "95"}),
             context=PRODUCT_CTX),
          ex("Qiyməti 85 manat et", reply="Hansı məhsulun", prompt=False))),
    Rule("inventory.price.ask", "inventory", 60,
         "qiyməti dəyişmək istəyir, amma yeni qiymət yazılmayıb",
         "alət çağırma; yeni qiyməti soruş",
         lambda u: u.has(*PRICE) and u.has("deyis", "yenile", "change", "update", "set", "teyin", "et", "qaldir", "endir")
         and not u.numbers,
         act_price_ask,
         (ex("Qiyməti dəyiş", reply="qiyməti yazın"),)),
    Rule("inventory.stock.query", "inventory", 55,
         "konkret məhsulun stokda nə qədər olduğunu soruşur",
         "search_inventory çağır: query = məhsulun adı; cavabda yalnız nəticədəki miqdarı de",
         lambda u: u.has(*HOW_MANY) and not u.quantities and not u.money and not u.has(*ADD[:4]),
         act_stock_query,
         (ex("Hazırda neçə qapım qalıb?", ("search_inventory", {"query": "qapı", "limit": 10})),
          ex("Stokda nə qədər taxta var?", ("search_inventory", {"query": "taxta", "limit": 10})),
          ex("Masa neçə dənədir?", ("search_inventory", {"query": "Masa", "limit": 10})))),
    Rule("inventory.add", "inventory", 50,
         "anbara mal əlavə edir / əlində nə olduğunu sadalayır (miqdar var): «məndə 500 qapı var», «100 yeni qapı gəldi»",
         "hər məhsul üçün ayrıca create_product çağır: name, quantity, unit, və deyilibsə selling_price/purchase_price. "
         "«Hər biri 85 AZN» bütün məhsullara aiddir. Qiymət deyilməyibsə, qiymət uydurma. Eyni adlı məhsul varsa, "
         "sistem miqdarı avtomatik ona əlavə edir",
         lambda u: bool(u.quantities),
         act_add,
         (ex("Məndə 500 qapı, 200 masa və 350 taxta var",
             ("create_product", {"name": "Qapı", "quantity": "500", "unit": "piece"}),
             ("create_product", {"name": "Masa", "quantity": "200", "unit": "piece"}),
             ("create_product", {"name": "Taxta", "quantity": "350", "unit": "piece"})),
          ex("100 yeni qapı gəldi", ("create_product", {"name": "Qapı", "quantity": "100", "unit": "piece"})),
          ex("Anbarımda 120 laminat panel var, hər biri 85 AZN",
             ("create_product", {"name": "Laminat panel", "quantity": "120", "unit": "piece", "selling_price": "85"})),
          ex("200 kv.m laminat 12 manat", ("create_product", {"name": "Laminat", "quantity": "200", "unit": "m2",
                                                              "selling_price": "12"}), prompt=False))),
    Rule("inventory.add_noqty", "inventory", 48,
         "məhsul əlavə etmək istəyir, amma miqdar yazılmayıb",
         "alət çağırma; miqdarı (və qiyməti) soruş, miqdar uydurma",
         lambda u: u.has("elave", "add") and not u.numbers and bool(u.ref(use_context=False)),
         act_add_without_quantity,
         (ex("Qapı əlavə et", reply="neçə ədəd"),)),
    Rule("inventory.search", "inventory", 40,
         "inventarda məhsul axtarır və ya siyahı istəyir",
         "search_inventory çağır: query = axtarılan söz (hamısı istənirsə query vermə)",
         lambda u: u.has(*SHOW),
         act_search_inventory,
         (ex("Bütün məhsulları göstər", ("search_inventory", {"limit": 30})),
          ex("Qapıları göstər", ("search_inventory", {"query": "Qapı", "limit": 30})))),
    Rule("inventory.lookup", "inventory", 20,
         "yalnız məhsul adı yazılıb (məs. «qapı»)",
         "search_inventory çağır: query = həmin ad",
         lambda u: not u.numbers and bool(u.concepts or u.rest),
         act_lookup,
         (ex("qapı", ("search_inventory", {"query": "qapı", "limit": 10})),)),
]
RULES.sort(key=lambda r: -r.priority)

FALLBACK = "Bunu tam başa düşmədim. "
_CLAUSES = re.compile(r"\s+(?:və|ve|and|sonra|həm də|hem de)\s+|;|,(?!\d)", re.IGNORECASE)
_CHAINABLE = {"update_price", "update_inventory", "update_product"}


def rules_for(mode: str = "inventory") -> list[Rule]:
    return [r for r in RULES if r.mode in (mode, "any")]


def _first(u: Utterance) -> tuple[Plan, Rule | None]:
    for rule in rules_for(u.mode):
        if rule.match(u):
            result = rule.act(u)
            if result is not None:
                return result, rule
    return Reply(FALLBACK + HELP), None


def _chained(text: str, mode: str, context: Context) -> tuple[list[Call], Rule | None]:
    """Several edits in one message ("Qapının qiymətini 90 et və stokunu 20 azalt"): plan each clause;
    a clause without a product name refers to the product of the previous clause."""
    clauses = [c for c in _CLAUSES.split(text) if c and c.strip()]
    if len(clauses) < 2:
        return [], None
    ctx = Context(products=list(context.products))
    calls: list[Call] = []
    first_rule = None
    for clause in clauses:
        result, rule = _first(Utterance(clause, ctx, mode))
        if not isinstance(result, list) or any(c.tool not in _CHAINABLE for c in result):
            return [], None
        first_rule = first_rule or rule
        calls += result
        ctx.products.append(result[-1].args["product"])
    return calls, first_rule


def plan(text: str, mode: str = "inventory", context: Context | None = None) -> tuple[Plan, Rule | None]:
    """Return the plan for one message and the rule that produced it (None = fallback help)."""
    context = context or Context()
    calls, rule = _chained(text, mode, context)
    if calls:
        return calls, rule
    return _first(Utterance(text, context, mode))


# extra multi-command example (asserted in tests, shown to the LLM)
MULTI_EXAMPLE = ex("Qapının qiymətini 90 manat et və stokunu 20 azalt",
                   ("update_price", {"product": "Qapı", "selling_price": "90"}),
                   ("update_inventory", {"product": "Qapı", "change": "-20",
                                         "reason": "AI köməkçi: stokunu 20 azalt"}))
