from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


os.environ.setdefault("MPLCONFIGDIR", str(Path.cwd() / ".matplotlib-cache"))


KOREAN_ALIASES = {
    "삼성전자": ("005930.KS", "삼성전자"),
    "두산에너빌리티": ("034020.KS", "두산에너빌리티"),
    "구영테크": ("053270.KQ", "구영테크"),
}


@dataclass(frozen=True)
class ResolvedSymbol:
    input_text: str
    yahoo_symbol: str
    display_name: str


def resolve_symbol(symbol: str) -> ResolvedSymbol:
    cleaned = symbol.strip()
    if not cleaned:
        raise ValueError("종목명 또는 종목 코드를 입력해 주세요.")

    alias = KOREAN_ALIASES.get(cleaned)
    if alias:
        return ResolvedSymbol(cleaned, alias[0], alias[1])

    upper = cleaned.upper()
    if upper.endswith((".KS", ".KQ")):
        code = upper[:6]
        return ResolvedSymbol(cleaned, upper, _name_for_code(code) or cleaned)

    if upper.isdigit() and len(upper) == 6:
        alias = _alias_symbol_by_code(upper)
        if alias:
            return ResolvedSymbol(cleaned, alias[0], alias[1])
        krx = _krx_symbol_by_code(upper)
        if krx:
            return ResolvedSymbol(cleaned, krx.yahoo_symbol, krx.display_name)
        return ResolvedSymbol(cleaned, f"{upper}.KS", _name_for_code(upper) or cleaned)

    if _looks_korean_name(cleaned):
        krx = _krx_symbol_by_name(cleaned)
        if krx:
            return ResolvedSymbol(cleaned, krx.yahoo_symbol, krx.display_name)
        raise ValueError(f"'{cleaned}' 종목명을 찾지 못했습니다. 종목명을 정확히 입력하거나 6자리 코드를 입력해 주세요.")

    return ResolvedSymbol(cleaned, upper, upper)


def normalize_symbol(symbol: str) -> str:
    return resolve_symbol(symbol).yahoo_symbol


def display_name_for_symbol(symbol: str) -> str:
    return resolve_symbol(symbol).display_name


def _looks_korean_name(value: str) -> bool:
    return any("가" <= char <= "힣" for char in value)


def _name_for_code(code: str) -> str | None:
    krx = _krx_symbol_by_code(code)
    return krx.display_name if krx else _alias_name_by_code(code)


def _alias_symbol_by_code(code: str) -> tuple[str, str] | None:
    for yahoo_symbol, display_name in KOREAN_ALIASES.values():
        if yahoo_symbol[:6] == code:
            return yahoo_symbol, display_name
    return None


def _alias_name_by_code(code: str) -> str | None:
    alias = _alias_symbol_by_code(code)
    return alias[1] if alias else None


def _krx_symbol_by_code(code: str) -> ResolvedSymbol | None:
    return _krx_symbols_by_code().get(code)


def _krx_symbol_by_name(name: str) -> ResolvedSymbol | None:
    exact = _krx_symbols_by_name().get(name)
    if exact:
        return exact

    matches = [
        item for item_name, item in _krx_symbols_by_name().items()
        if name in item_name
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        sample = ", ".join(item.display_name for item in matches[:5])
        raise ValueError(f"'{name}'와 비슷한 종목이 여러 개입니다: {sample}. 더 정확히 입력해 주세요.")
    return None


@lru_cache(maxsize=1)
def _krx_symbols_by_code() -> dict[str, ResolvedSymbol]:
    return _load_with_finance_datareader()


def _load_with_finance_datareader() -> dict[str, ResolvedSymbol]:
    by_code: dict[str, ResolvedSymbol] = {}
    try:
        import FinanceDataReader as fdr
    except Exception:
        return by_code

    try:
        listing = fdr.StockListing("KRX")
    except Exception:
        return by_code

    for _, row in listing.iterrows():
        code = str(row.get("Code", "")).zfill(6)
        name = str(row.get("Name", "")).strip()
        market = str(row.get("Market", "")).upper()
        if not code or not name:
            continue
        suffix = "KQ" if "KOSDAQ" in market else "KS"
        by_code[code] = ResolvedSymbol(name, f"{code}.{suffix}", name)

    return by_code

@lru_cache(maxsize=1)
def _krx_symbols_by_name() -> dict[str, ResolvedSymbol]:
    return {item.display_name: item for item in _krx_symbols_by_code().values()}
