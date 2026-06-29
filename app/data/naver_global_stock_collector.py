"""
naver_global_stock_collector.py — 해외 종목 시세 수집.

수집 순서: Naver 글로벌 주식 페이지 → yfinance
해외 데이터는 네이버를 보조 소스로만 사용, 실패 시 yfinance를 반드시 사용.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

_NAVER_SYMBOL_MAP = {
    "MU":   "MU.O",
    "NVDA": "NVDA.O",
    "QQQ":  "QQQ.O",
    "SOXX": "SOXX.O",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
}


def _fetch_from_naver_world(symbol: str) -> Optional[dict]:
    """네이버 해외주식 페이지에서 시세 수집 (보조)."""
    naver_sym = _NAVER_SYMBOL_MAP.get(symbol.upper())
    if not naver_sym:
        return None
    try:
        import requests
        url = f"https://finance.naver.com/world/sise.naver?symbol={naver_sym}"
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        resp.encoding = "euc-kr"
        text = resp.text

        price = None
        for pattern in [
            r'"now"\s*:\s*"?([\d,.]+)',
            r'class="rate_info"[^>]*>.*?<em[^>]*>([\d,.]+)',
            r'<span[^>]*class="[^"]*price[^"]*"[^>]*>([\d,.]+)',
        ]:
            m = re.search(pattern, text, re.DOTALL)
            if m:
                try:
                    price = float(m.group(1).replace(",", ""))
                    if price > 0:
                        break
                    price = None
                except ValueError:
                    price = None

        return_pct = None
        for pattern in [
            r'"rate"\s*:\s*"?([-\d.]+)',
            r'class="[^"]*change[^"]*"[^>]*>([-+\d.]+)%',
        ]:
            m = re.search(pattern, text)
            if m:
                try:
                    return_pct = float(m.group(1))
                    break
                except ValueError:
                    pass

        if price and price > 0:
            return {"price": price, "return_pct": return_pct}
    except Exception as exc:
        logger.debug("Naver global %s 실패: %s", symbol, exc)
    return None


def _fetch_from_yfinance(symbol: str) -> Optional[dict]:
    """yfinance로 해외 종목 시세 수집."""
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        info = t.fast_info
        price = float(info.last_price or 0) or None
        prev  = float(info.previous_close or 0) or None
        if price and prev:
            return_pct = round((price / prev - 1) * 100, 2)
        else:
            return_pct = None
        if price:
            return {"price": price, "return_pct": return_pct}
    except Exception as exc:
        logger.debug("yfinance %s 실패: %s", symbol, exc)
    return None


def fetch_naver_global_quote(symbol: str) -> dict:
    """
    해외 종목 시세 수집 (Naver → yfinance fallback).

    Returns
    -------
    dict:
        symbol     : 종목 심볼
        price      : 현재가 (float | None)
        return_pct : 등락률 (float | None)
        source     : "naver_global" | "yfinance" | "failed"
        status     : "success" | "failed"
        error      : 오류 메시지 | None
    """
    result: dict = {
        "symbol": symbol,
        "price": None,
        "return_pct": None,
        "source": "failed",
        "status": "failed",
        "error": None,
    }
    errors = []

    naver_data = _fetch_from_naver_world(symbol)
    if naver_data and naver_data.get("price"):
        result.update(**naver_data, source="naver_global", status="success")
        return result
    errors.append(f"Naver global {symbol}: 데이터 없음")

    yf_data = _fetch_from_yfinance(symbol)
    if yf_data and yf_data.get("price"):
        result.update(**yf_data, source="yfinance", status="success")
        return result
    errors.append(f"yfinance {symbol}: 데이터 없음")

    result["error"] = " | ".join(errors)
    return result


def search_naver_finance_keyword(keyword: str) -> list:
    """
    네이버 금융 키워드 검색.

    Returns
    -------
    list[dict]: [{name, code}, ...]
    """
    try:
        import requests
        url = f"https://ac.finance.naver.com/ac?q={keyword}&q_enc=UTF-8&st=111&sug_num=6"
        resp = requests.get(url, headers=_HEADERS, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        results = []
        for group in items:
            for item in group:
                if isinstance(item, list) and len(item) >= 2:
                    results.append({"name": item[0], "code": item[1] if len(item) > 1 else ""})
        return results
    except Exception as exc:
        logger.debug("네이버 금융 검색 실패: %s", exc)
        return []
