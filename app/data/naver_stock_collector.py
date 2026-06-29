"""
naver_stock_collector.py — 네이버 금융 국내주식 수집.

SK하이닉스(000660) 현재가·일별시세 스크래핑.
수집 우선순위 상 KIS 다음, yfinance 이전 2순위로 사용.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_HYNIX_CODE = "000660"
_HYNIX_PRICE_MIN = 50_000
_HYNIX_PRICE_MAX = 1_000_000
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
    "Accept-Language": "ko-KR,ko;q=0.9",
}


def _is_valid_hynix_price(price: float) -> bool:
    return _HYNIX_PRICE_MIN <= price <= _HYNIX_PRICE_MAX


def fetch_naver_current_price(code: str = _HYNIX_CODE) -> dict:
    """
    네이버 금융에서 종목 현재가 수집.

    Returns
    -------
    dict: {current_price, source, status, error}
    """
    result: dict = {
        "current_price": None,
        "source": "naver",
        "status": "failed",
        "error": None,
    }
    try:
        url = f"https://finance.naver.com/item/sise.naver?code={code}"
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        resp.encoding = "euc-kr"
        text = resp.text

        price = None
        for pattern in [
            r'id="current_value"[^>]*>\s*([\d,]+)',
            r'class="no_today"[^>]*>\s*<em[^>]*>([^<]+)</em>',
            r'"currentPrice"\s*:\s*"?([\d,]+)',
            r'<strong[^>]*class="[^"]*num[^"]*"[^>]*>\s*([\d,]+)',
        ]:
            m = re.search(pattern, text)
            if m:
                try:
                    price = float(m.group(1).replace(",", ""))
                    if _is_valid_hynix_price(price):
                        break
                    price = None
                except ValueError:
                    price = None

        if price is None:
            daily = fetch_naver_daily_ohlcv(code, pages=1)
            if daily is not None and not daily.empty:
                price = float(daily.iloc[-1]["close"])
                if not _is_valid_hynix_price(price):
                    price = None

        if price is not None:
            result["current_price"] = price
            result["status"] = "success"
        else:
            result["error"] = "가격 파싱 실패 (범위 밖 또는 패턴 불일치)"
    except Exception as exc:
        result["error"] = f"네이버 현재가 수집 오류: {exc}"
        logger.warning("naver_current_price 수집 실패: %s", exc)

    return result


def fetch_naver_daily_ohlcv(code: str = _HYNIX_CODE, pages: int = 3) -> Optional[pd.DataFrame]:
    """
    네이버 금융 일별시세 수집.

    Parameters
    ----------
    code  : 종목 코드
    pages : 수집할 페이지 수 (1페이지 ≈ 10~15거래일)

    Returns
    -------
    pd.DataFrame with columns [datetime, open, high, low, close, volume]
    or None on failure.
    """
    records = []
    for page in range(1, pages + 1):
        url = f"https://finance.naver.com/item/sise_day.naver?code={code}&page={page}"
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=10)
            resp.raise_for_status()
            resp.encoding = "euc-kr"

            tables = pd.read_html(resp.text, header=0)
            if not tables:
                continue

            df = None
            for t in tables:
                t.columns = [str(c).strip() for c in t.columns]
                if any("날짜" in c or "일자" in c for c in t.columns):
                    df = t
                    break
            if df is None:
                continue

            col_map: dict = {}
            for c in df.columns:
                lc = c.strip()
                if "날짜" in lc or "일자" in lc:
                    col_map[c] = "date"
                elif "종가" in lc:
                    col_map[c] = "close"
                elif "시가" in lc:
                    col_map[c] = "open"
                elif "고가" in lc:
                    col_map[c] = "high"
                elif "저가" in lc:
                    col_map[c] = "low"
                elif "거래량" in lc:
                    col_map[c] = "volume"
            df = df.rename(columns=col_map)

            needed = [c for c in ["date", "open", "high", "low", "close", "volume"] if c in df.columns]
            if "date" not in needed or "close" not in needed:
                continue
            df = df[needed].dropna(subset=["date", "close"])

            for _, row in df.iterrows():
                try:
                    date_str = str(row["date"]).strip()
                    if not re.match(r"\d{4}\.\d{2}\.\d{2}", date_str):
                        continue
                    close_raw = str(row["close"]).replace(",", "").strip()
                    close = float(close_raw)
                    if not _is_valid_hynix_price(close):
                        continue
                    open_raw = str(row.get("open", close)).replace(",", "").strip()
                    high_raw = str(row.get("high", close)).replace(",", "").strip()
                    low_raw  = str(row.get("low", close)).replace(",", "").strip()
                    vol_raw  = str(row.get("volume", 0)).replace(",", "").strip()
                    records.append({
                        "datetime": date_str,
                        "open":   float(open_raw or close),
                        "high":   float(high_raw or close),
                        "low":    float(low_raw or close),
                        "close":  close,
                        "volume": int(float(vol_raw or 0)),
                    })
                except (ValueError, TypeError):
                    continue
        except Exception as exc:
            logger.debug("네이버 일봉 page=%d 수집 실패: %s", page, exc)
            continue

    if not records:
        return None

    result = pd.DataFrame(records)
    result["datetime"] = pd.to_datetime(result["datetime"], format="%Y.%m.%d", errors="coerce")
    result = result.dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)
    return result if not result.empty else None
