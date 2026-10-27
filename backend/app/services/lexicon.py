"""Azerbaijani-first text normalisation, light stemming and a small trade lexicon.

Used by product search/matching and the offline (no-LLM) parser.
Everything here is deterministic and data-only; no I/O.
"""

from __future__ import annotations

import re
import unicodedata

_FOLD = str.maketrans({"ə": "e", "ı": "i", "ö": "o", "ü": "u", "ş": "s", "ç": "c", "ğ": "g", "â": "a", "î": "i", "û": "u"})


def az_lower(text: str) -> str:
    """Lowercase with Azerbaijani/Turkish dotted/dotless I rules."""
    return text.replace("I", "ı").replace("İ", "i").lower()


def fold(text: str) -> str:
    """Lowercase, strip Azerbaijani diacritics and punctuation: 'Ağ QAPILAR!' -> 'ag qapilar'."""
    t = az_lower(text).translate(_FOLD)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = t.replace("²", "2").replace("³", "3")
    t = re.sub(r"[^\w\s.,/-]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# concept -> surface forms (already folded). Concepts are language-neutral ids.
CONCEPTS: dict[str, set[str]] = {
    # products
    "door": {"qapi", "door", "kapi", "dver"},
    "window": {"pencere", "window", "okno"},
    "board": {"lovhe", "board", "plank", "levha", "taxta lovhe", "doska"},
    "panel": {"panel", "panelli"},
    "laminate": {"laminat", "laminate", "laminant"},
    "parquet": {"parket", "parquet"},
    "tile": {"kafel", "tile", "plite", "seramik", "fayans", "metlax"},
    "paint": {"boya", "paint", "kraska", "emulsiya"},
    "cement": {"sement", "cement", "cimento", "semento"},
    "concrete": {"beton", "concrete"},
    "brick": {"kerpic", "brick", "tugla", "blok"},
    "gypsum": {"alci", "gips", "gypsum", "plaster", "suvaq", "shpaklyovka", "spaklyovka"},
    "drywall": {"gipskarton", "alcipan", "drywall", "qipskarton"},
    "plywood": {"fanera", "plywood", "kontrplak"},
    "profile": {"profil", "profile"},
    "pipe": {"boru", "pipe", "truba"},
    "cable": {"kabel", "cable", "naqil"},
    "screw": {"vint", "surup", "screw", "samorez", "vida"},
    "nail": {"mismar", "nail", "civi"},
    "insulation": {"izolyasiya", "insulation", "yalitim", "penoplast", "minvata"},
    "handle": {"destek", "qulp", "handle", "kulp"},
    "hinge": {"petle", "hinge", "mentese", "rezde"},
    "lock": {"qifil", "lock", "kilid", "kilit"},
    "baseboard": {"plintus", "baseboard", "supurgelik"},
    "glue": {"yapisqan", "glue", "kley", "yapistirici", "adhesive"},
    "sand": {"qum", "sand", "kum"},
    "rebar": {"armatur", "rebar"},
    "sheet": {"vereq", "sheet"},
    "faucet": {"kran", "faucet", "smesitel"},
    "sink": {"lavabo", "rakovina", "sink"},
    "lamp": {"lampa", "lamp", "cilciraq"},
    "tool": {"alet", "tool", "instrument"},
    # materials
    "wood": {"taxta", "agac", "wood", "wooden", "ahsap", "tahta", "derevo", "odun", "massiv"},
    "mdf": {"mdf"},
    "chipboard": {"dsp", "lddsp", "chipboard", "yonqar", "sunta", "osb"},
    "metal": {"metal", "demir", "iron", "metallik"},
    "steel": {"polad", "steel", "celik", "paslanmaz"},
    "aluminium": {"aluminium", "aluminum", "aluminyum", "alumin"},
    "plastic": {"plastik", "plastic", "pvc", "pvx"},
    "glass": {"suse", "glass", "cam", "steklo"},
    "ceramic": {"keramika", "ceramic", "keramik"},
    "oak": {"palid", "oak", "mese"},
    "pine": {"sam", "pine", "cam agaci"},
    "interior": {"otaqarasi", "interior", "ic", "daxili", "mezhkomnatnaya"},
    "exterior": {"giris", "exterior", "xarici", "dis", "entrance"},
    "waterproof": {"suya davamli", "waterproof", "nemedavamli", "su gecirmez"},
    # colours
    "white": {"ag", "white", "beyaz", "belyy"},
    "black": {"qara", "black", "siyah"},
    "gray": {"boz", "gray", "grey", "gri", "seri"},
    "brown": {"qehveyi", "brown", "kahverengi", "qehveyi rengli"},
    "red": {"qirmizi", "red", "kirmizi"},
    "blue": {"goy", "blue", "mavi"},
    "green": {"yasil", "green", "yesil"},
    "yellow": {"sari", "yellow"},
    "beige": {"bej", "beige"},
    "walnut": {"qoz", "walnut", "ceviz"},
    # general goods
    "table": {"masa", "table", "stol"},
    "chair": {"stul", "chair", "sandalye", "kreslo"},
    "shelf": {"ref", "shelf", "polka"},
    "cabinet": {"skaf", "dolab", "cabinet", "wardrobe"},
}

COLOR_CONCEPTS = {"white", "black", "gray", "brown", "red", "blue", "green", "yellow", "beige", "walnut"}
MATERIAL_CONCEPTS = {"wood", "mdf", "chipboard", "metal", "steel", "aluminium", "plastic", "glass", "ceramic", "oak", "pine"}

# Display names (az) for concept ids, used when the offline parser names a product.
CONCEPT_AZ: dict[str, str] = {
    "door": "qapı", "window": "pəncərə", "board": "lövhə", "panel": "panel", "laminate": "laminat",
    "parquet": "parket", "tile": "kafel", "paint": "boya", "cement": "sement", "concrete": "beton",
    "brick": "kərpic", "gypsum": "alçı", "drywall": "gipskarton", "plywood": "fanera", "profile": "profil",
    "pipe": "boru", "cable": "kabel", "screw": "vint", "nail": "mismar", "insulation": "izolyasiya",
    "handle": "dəstək", "hinge": "petlə", "lock": "qıfıl", "baseboard": "plintus", "glue": "yapışqan",
    "sand": "qum", "rebar": "armatur", "sheet": "vərəq", "faucet": "kran", "sink": "lavabo", "lamp": "lampa",
    "tool": "alət", "wood": "taxta", "mdf": "MDF", "chipboard": "DSP", "metal": "metal", "steel": "polad",
    "aluminium": "alüminium", "plastic": "plastik", "glass": "şüşə", "ceramic": "keramika", "oak": "palıd",
    "pine": "şam", "interior": "otaqarası", "exterior": "giriş", "waterproof": "suya davamlı",
    "white": "ağ", "black": "qara", "gray": "boz", "brown": "qəhvəyi", "red": "qırmızı", "blue": "göy",
    "green": "yaşıl", "yellow": "sarı", "beige": "bej", "walnut": "qoz", "table": "masa", "chair": "stul",
    "shelf": "rəf", "cabinet": "şkaf",
}

_SURFACE: dict[str, str] = {}
_MULTIWORD: list[tuple[str, str]] = []
for _concept, _forms in CONCEPTS.items():
    for _f in _forms:
        if " " in _f:
            _MULTIWORD.append((_f, _concept))
        else:
            _SURFACE.setdefault(_f, _concept)
_MULTIWORD.sort(key=lambda x: -len(x[0]))

# Azerbaijani/Turkish suffixes (folded), longest first.
_SUFFIXES = sorted(
    {
        "larinin", "lerinin", "larin", "lerin", "larim", "lerim", "imda", "imde", "imiz", "larini", "lerini", "larina", "lerine", "larinda", "lerinde", "lardan", "lerden",
        "larda", "lerde", "lari", "leri", "lar", "ler", "nin", "nun", "nın", "dan", "den", "tan", "ten",
        "da", "de", "ta", "te", "ni", "nu", "ya", "ye", "yi", "yu", "in", "un", "i", "u", "a", "e",
        "li", "lu", "lik", "luk", "s", "es",
    },
    key=len,
    reverse=True,
)

STOPWORDS = {
    # az
    "ve", "ile", "bir", "bu", "o", "mene", "men", "menim", "lazimdir", "lazim", "var", "ucun", "hem", "de", "da",
    "goster", "gosterin", "tap", "tapin", "axtar", "istiyirem", "isteyirem", "olan", "ki", "cox", "az", "en",
    "edin", "et", "zehmet", "olmasa", "zehmetolmasa", "yaxin", "yaxinliginda", "ucuz", "serfeli", "qiymete",
    "eded", "ededi", "dene", "anbarda", "anbarimda", "anbarimizda", "elave", "hansi", "neçe", "nece",
    "topdan", "topdansatici", "topdansaticilar", "satici", "saticilar", "teklif", "teklifler", "secim", "secimler",
    "i", "need", "want", "show", "me", "find", "the", "a", "an", "of", "for", "in", "near", "from", "with",
    "and", "or", "please", "some", "options", "affordable", "cheap", "suppliers", "supplier", "wholesalers",
    "preferably", "bulk", "have", "my", "warehouse", "pieces", "units", "ihtiyacim", "lazim", "bana", "goster",
}

UNIT_ALIASES: dict[str, str] = {
    "eded": "piece", "ed": "piece", "dene": "piece", "denə": "piece", "pcs": "piece", "pc": "piece",
    "piece": "piece", "pieces": "piece", "adet": "piece", "shtuk": "piece", "unit": "piece", "units": "piece",
    "m2": "m2", "kvm": "m2", "kv.m": "m2", "kv": "m2", "sqm": "m2", "metrekare": "m2", "kvadratmetr": "m2",
    "m": "m", "metr": "m", "meter": "m", "meters": "m", "metre": "m", "metres": "m",
    "m3": "m3", "kub": "m3", "kubmetr": "m3",
    "kg": "kg", "kq": "kg", "kilo": "kg", "kiloqram": "kg", "kilogram": "kg",
    "ton": "ton", "tons": "ton", "tonne": "ton",
    "l": "l", "litr": "l", "liter": "l", "litre": "l", "liters": "l",
    "paket": "pack", "qutu": "pack", "pack": "pack", "packs": "pack", "box": "pack", "boxes": "pack", "koli": "pack",
    "dest": "set", "set": "set", "komplekt": "set", "sets": "set",
}
_UNIT_PHRASES = [
    ("kvadrat metr", "m2"), ("kv metr", "m2"), ("kv. m", "m2"), ("square meters", "m2"), ("square meter", "m2"),
    ("square metres", "m2"), ("sq m", "m2"), ("sq. m", "m2"), ("metre kare", "m2"), ("kub metr", "m3"),
    ("cubic meters", "m3"), ("cubic meter", "m3"),
]

UNIT_LABEL_AZ = {"piece": "ədəd", "m2": "m²", "m": "metr", "m3": "m³", "kg": "kq", "ton": "ton", "l": "litr", "pack": "paket", "set": "dəst"}

CITIES: dict[str, str] = {
    "baki": "Bakı", "baku": "Bakı", "bakida": "Bakı", "bakidan": "Bakı", "bakiya": "Bakı", "bakinin": "Bakı",
    "sumqayit": "Sumqayıt", "sumgait": "Sumqayıt", "sumqayitda": "Sumqayıt",
    "gence": "Gəncə", "ganja": "Gəncə", "gencede": "Gəncə",
    "mingecevir": "Mingəçevir", "lenkeran": "Lənkəran", "lankaran": "Lənkəran", "seki": "Şəki", "shaki": "Şəki",
    "sirvan": "Şirvan", "naxcivan": "Naxçıvan", "nakhchivan": "Naxçıvan", "xirdalan": "Xırdalan",
    "quba": "Quba", "qebele": "Qəbələ", "gabala": "Qəbələ", "yevlax": "Yevlax", "berde": "Bərdə",
    "salyan": "Salyan", "qusar": "Qusar", "zaqatala": "Zaqatala", "masalli": "Masallı", "abseron": "Abşeron",
}


def _split_tokens(folded: str) -> list[str]:
    return [t for t in re.split(r"[\s,./]+", folded) if t]


def stem(token: str) -> str:
    """Return a lexicon surface form if the token reduces to one, else a conservative stem."""
    if token in _SURFACE or token.isdigit():
        return token
    for suf in _SUFFIXES:
        if token.endswith(suf) and len(token) - len(suf) >= 2:
            cand = token[: -len(suf)]
            if cand in _SURFACE:
                return cand
            # buffer consonant variants: qapisi -> qapi, lovhesi -> lovhe
            if cand.endswith("s") and cand[:-1] in _SURFACE:
                return cand[:-1]
    # two stacked suffixes: laminatindan -> laminat (+in +dan), qapilarindan -> qapi
    for suf in _SUFFIXES:
        if token.endswith(suf) and len(token) - len(suf) >= 3:
            cand = token[: -len(suf)]
            for suf2 in _SUFFIXES:
                if cand.endswith(suf2) and cand[: -len(suf2)] in _SURFACE:
                    return cand[: -len(suf2)]
    # last resort, also used for words outside the lexicon ("masanı", "masam" → "masa"); index and query
    # are stemmed the same way, so a rough stem still matches
    for suf in ("lari", "leri", "lar", "ler", "dan", "den", "da", "de", "ni", "nu", "im", "um", "m", "s"):
        if token.endswith(suf) and len(token) - len(suf) >= 4:
            return token[: -len(suf)]
    return token


def analyze(text: str) -> tuple[set[str], list[str]]:
    """Return (concept ids, remaining meaningful stems) for free text."""
    folded = fold(text)
    folded = unit_phrase_replace(folded)
    concepts: set[str] = set()
    for phrase, concept in _MULTIWORD:
        if phrase in folded:
            concepts.add(concept)
            folded = folded.replace(phrase, " ")
    rest: list[str] = []
    for tok in _split_tokens(folded):
        s = stem(tok)
        if s in _SURFACE:
            concepts.add(_SURFACE[s])
        elif tok in CITIES or s in CITIES or tok in UNIT_ALIASES or s in UNIT_ALIASES:
            continue
        elif s not in STOPWORDS and tok not in STOPWORDS and not s.isdigit() and len(s) > 1:
            rest.append(s)
    return concepts, rest


def build_search_text(*parts: str | None) -> str:
    """Normalised, de-duplicated index string: folded stems + concept ids (prefixed with '#')."""
    text = " ".join(p for p in parts if p)
    concepts, rest = analyze(text)
    folded_tokens = [stem(t) for t in _split_tokens(fold(text))]
    words = list(dict.fromkeys(folded_tokens + rest))
    return " ".join(words + [f"#{c}" for c in sorted(concepts)])


def detect_city(text: str) -> str | None:
    for tok in _split_tokens(fold(text)):
        if tok in CITIES:
            return CITIES[tok]
        s = stem(tok)
        if s in CITIES:
            return CITIES[s]
        for suf in ("daki", "deki", "dakilar", "dekiler", "dan", "den", "da", "de", "a", "e", "ya", "ye", "nin", "nun"):
            if tok.endswith(suf) and tok[: -len(suf)] in CITIES:  # Bakıdakı, Gəncədəki, Bakıya
                return CITIES[tok[: -len(suf)]]
    return None


def parse_unit(token: str) -> str | None:
    return UNIT_ALIASES.get(fold(token).rstrip("."))


def unit_phrase_replace(folded: str) -> str:
    """Collapse multi-word units ('kvadrat metr') into canonical single tokens ('m2')."""
    for phrase, unit in _UNIT_PHRASES:
        folded = folded.replace(phrase, unit)
    return folded


def concepts_to_name(concepts: list[str]) -> str:
    """Build a readable Azerbaijani product name from concept ids, colours/materials first."""
    order = sorted(concepts, key=lambda c: (0 if c in COLOR_CONCEPTS else 1 if c in MATERIAL_CONCEPTS else 2))
    words = [CONCEPT_AZ.get(c, c) for c in order]
    if not words:
        return ""
    name = " ".join(words)
    return name[0].upper() + name[1:]
