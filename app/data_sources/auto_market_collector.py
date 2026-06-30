"""Automatic market data collector for the SK Hynix forecast tab."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
load_dotenv()

try:
    from app.data.safe_api import safe_json as _safe_json
except ImportError:
    def _safe_json(response):  # type: ignore[misc]
        try:
            return response.json() if response else None
        except Exception:
            return None

try:
    from app.data.naver_stock_collector import (
        fetch_naver_current_price as _naver_current_price,
        fetch_naver_daily_ohlcv as _naver_daily_ohlcv,
    )
except ImportError:
    def _naver_current_price(code="000660"):  # type: ignore[misc]
        return {"current_price": None, "status": "failed", "source": "naver", "error": "import_failed"}

    def _naver_daily_ohlcv(code="000660", pages=3):  # type: ignore[misc]
        return None

try:
    from app.data.naver_global_stock_collector import fetch_naver_global_quote as _naver_global_quote
except ImportError:
    def _naver_global_quote(symbol):  # type: ignore[misc]
        return {"symbol": symbol, "price": None, "return_pct": None, "source": "failed", "status": "failed", "error": "import_failed"}

try:
    from app.data.market_data_validator import (
        validate_hynix_current_sources,
        validate_hynix_dataframe,
        validate_hynix_price,
        validate_stock_identity,
    )
except ImportError:
    def validate_hynix_dataframe(df):  # type: ignore[misc]
        if df is None or df.empty or "close" not in df.columns:
            return False, "daily data missing", df
        ok = df[df["close"].apply(lambda x: 50_000 <= float(x) <= 1_000_000)].reset_index(drop=True)
        return len(ok) >= 20, f"valid rows={len(ok)}", ok

    def validate_hynix_price(price):  # type: ignore[misc]
        return price is not None and 50_000 <= float(price) <= 5_000_000, "ok"

    def validate_hynix_current_sources(source_prices, tolerance_pct=1.0):  # type: ignore[misc]
        return False, "validator unavailable", {"source_prices": source_prices}

    def validate_stock_identity(code, name):  # type: ignore[misc]
        return code == "000660" and name == "SK하이닉스", "ok"

ROOT = Path(__file__).resolve().parent.parent.parent
MICRON_DIR = ROOT / "data" / "micron"
CACHE_DIR = ROOT / "data" / "cache"
LEGACY_HYNIX_DIR = ROOT / "data" / "hynix"

_HYNIX_DAILY_CSV = CACHE_DIR / "hynix_daily.csv"
_HYNIX_CURRENT_JSON = CACHE_DIR / "hynix_current.json"
_MU_1MIN_CSV = CACHE_DIR / "mu_1min.csv"
_GLOBAL_QUOTES_JSON = CACHE_DIR / "global_quotes.json"


def _configure_yfinance_cache() -> None:
    try:
        import yfinance as yf

        cache_dir = CACHE_DIR / "yfinance"
        cache_dir.mkdir(parents=True, exist_ok=True)
        if hasattr(yf, "set_tz_cache_location"):
            yf.set_tz_cache_location(str(cache_dir))
    except Exception:
        pass


def _has_kis_real_keys() -> bool:
    return bool(os.environ.get("KIS_REAL_APP_KEY")) and bool(os.environ.get("KIS_REAL_APP_SECRET"))


def _has_kis_mock_keys() -> bool:
    return bool(os.environ.get("KIS_MOCK_APP_KEY")) and bool(os.environ.get("KIS_MOCK_APP_SECRET"))


def _kis_mode() -> Optional[str]:
    if _has_kis_real_keys():
        return "real"
    if _has_kis_mock_keys():
        return "mock"
    return None


def _cache_age_hours(path: Path) -> Optional[float]:
    if not path.exists():
        return None
    return (time.time() - path.stat().st_mtime) / 3600


def _fresh_cache(path: Path, max_hours: float = 24.0) -> bool:
    age = _cache_age_hours(path)
    return age is not None and age <= max_hours


def _read_json_cache(path: Path) -> Optional[dict]:
    if not _fresh_cache(path):
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json_cache(path: Path, payload: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = dict(payload)
        data["cached_at"] = datetime.now().isoformat()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("json cache write failed for %s: %s", path, exc)


def collect_mu_data(mode: Optional[str] = None) -> dict:
    """Collect MU one-minute and three-minute bars.

    Priority: KIS overseas minute, yfinance 1m, Naver/yfinance quote, cache.
    """
    mode = mode or _kis_mode()
    result = {
        "df_1min": None,
        "df_3min": None,
        "current_price": None,
        "source": None,
        "error": None,
        "fallback_chain": [],
        "current_price_status": "failed",
        "minute_1m_status": "unavailable",
        "minute_3m_status": "unavailable",
        "daily_status": "unavailable",
        "minute_error": None,
        "df_daily": None,
    }

    if mode:
        try:
            from app.data_sources.kis_overseas_minute import collect_and_save_mu, fetch_mu_current_price

            collect_and_save_mu(mode=mode)
            current_price = fetch_mu_current_price(mode=mode)
            df_1min = pd.read_csv(MICRON_DIR / "MU_1min.csv") if (MICRON_DIR / "MU_1min.csv").exists() else None
            df_3min = pd.read_csv(MICRON_DIR / "MU_3min.csv") if (MICRON_DIR / "MU_3min.csv").exists() else None
            ok_1m, reason_1m, df_1min = _validate_real_candles(df_1min, "kis")
            ok_3m, reason_3m, df_3min = _validate_real_candles(df_3min, "kis")
            if ok_1m:
                df_daily = _fetch_yfinance_daily("MU", period="90d")
                ok_daily, reason_daily, df_daily = _validate_real_daily_candles(df_daily, "yfinance")
                _save_mu_1min(df_1min)
                result.update(
                    df_1min=df_1min,
                    df_3min=df_3min if ok_3m else _resample_3min(df_1min),
                    df_daily=df_daily if ok_daily else None,
                    current_price=current_price,
                    source="kis",
                    current_price_status="success" if current_price else "failed",
                    minute_1m_status="real candle success",
                    minute_3m_status="real candle success",
                    daily_status="real candle success" if ok_daily else "unavailable",
                    minute_error=None if ok_daily else reason_daily,
                )
                result["fallback_chain"].append("KIS: success")
                return result
            result["minute_error"] = reason_1m
            result["fallback_chain"].append(f"KIS: minute rejected ({reason_1m})")
        except Exception as exc:
            result["error"] = f"KIS MU failed: {exc}"
            result["fallback_chain"].append(f"KIS: failed ({exc})")
    else:
        result["fallback_chain"].append("KIS: skipped (credentials missing)")

    try:
        df_1min = _fetch_yfinance_intraday("MU", period="5d", interval="1m")
        ok_1m, reason_1m, df_1min = _validate_real_candles(df_1min, "yfinance")
        if ok_1m:
            df_3min = _resample_3min(df_1min)
            ok_3m, reason_3m, df_3min = _validate_real_candles(df_3min, "yfinance")
            df_daily = _fetch_yfinance_daily("MU", period="90d")
            ok_daily, reason_daily, df_daily = _validate_real_daily_candles(df_daily, "yfinance")
            last = df_1min.iloc[-1]
            current_price = {
                "price": float(last["close"]),
                "open": float(df_1min.iloc[0]["open"]),
                "high": float(df_1min["high"].max()),
                "low": float(df_1min["low"].min()),
            }
            _save_mu_1min(df_1min)
            result.update(
                df_1min=df_1min,
                df_3min=df_3min if ok_3m else None,
                df_daily=df_daily if ok_daily else None,
                current_price=current_price,
                source="yfinance",
                current_price_status="success",
                minute_1m_status="real candle success",
                minute_3m_status="real candle success" if ok_3m else "unavailable",
                daily_status="real candle success" if ok_daily else "unavailable",
                minute_error=None if (ok_3m and ok_daily) else "; ".join(x for x in [None if ok_3m else reason_3m, None if ok_daily else reason_daily] if x),
            )
            result["fallback_chain"].append("yfinance_1m: success")
            return result
        result["minute_1m_status"] = "synthetic rejected" if reason_1m and "constant" in reason_1m else "unavailable"
        result["minute_error"] = reason_1m
        result["fallback_chain"].append(f"yfinance_1m: rejected ({reason_1m})")
    except Exception as exc:
        result["error"] = (result.get("error") or "") + f" | yfinance MU minute failed: {exc}"
        result["fallback_chain"].append(f"yfinance_1m: failed ({exc})")

    quote = _quote_with_naver_then_yfinance("MU")
    if quote.get("price") is not None:
        result.update(
            current_price={"price": quote["price"], "open": None, "high": None, "low": None},
            source=quote["source"],
            current_price_status="success",
            minute_1m_status="unavailable",
            minute_3m_status="unavailable",
        )
        result["fallback_chain"].append(f"{quote['source']}: quote success; minute unavailable")
        return result

    cached = _load_mu_1min_cache()
    if cached is not None:
        ok_cache, reason_cache, cached = _validate_real_candles(cached, "cache")
        if not ok_cache:
            result["minute_1m_status"] = "synthetic rejected" if "constant" in reason_cache else "unavailable"
            result["minute_error"] = reason_cache
            result["fallback_chain"].append(f"cache: rejected ({reason_cache})")
            return result
        df_3min = _resample_3min(cached)
        current_price = {"price": float(cached.iloc[-1]["close"]), "open": None, "high": None, "low": None}
        result["fallback_chain"].append("cache: present but rejected for live prediction")
        result["error"] = (result.get("error") or "") + " | MU live minute collection failed; cache not allowed"
    elif _MU_1MIN_CSV.exists():
        result["fallback_chain"].append(f"cache: stale ({_cache_age_hours(_MU_1MIN_CSV):.1f}h)")
        result["error"] = (result.get("error") or "") + " | MU cache stale"
    return result


def collect_nvda_data(mode: Optional[str] = None) -> dict:
    """Collect NVDA quote. Priority: KIS, Naver global, yfinance, cache."""
    mode = mode or _kis_mode()
    result = {"current_price": None, "premarket_return": None, "regular_return": None, "source": None, "error": None}

    if mode:
        try:
            from app.data_sources.kis_overseas_minute import BASE_URL_MOCK, BASE_URL_REAL, _get_access_token, _load_credentials
            import requests as rq

            base_url = BASE_URL_REAL if mode == "real" else BASE_URL_MOCK
            creds = _load_credentials(mode)
            token = _get_access_token(mode)
            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": creds["app_key"],
                "appsecret": creds["app_secret"],
                "tr_id": "HHDFS00000300",
            }
            params = {"AUTH": "", "EXCD": "NAS", "SYMB": "NVDA"}
            response = rq.get(f"{base_url}/uapi/overseas-stock/v1/quotations/price", headers=headers, params=params, timeout=10)
            body = _safe_json(response)
            if body is None:
                raise ValueError("KIS response is not JSON")
            out = body.get("output", {})
            price = _float_or_none(out.get("last") or out.get("zdiv"))
            if price:
                ret = _float_or_none(out.get("rate") or out.get("diff_rate"))
                result.update(current_price=price, regular_return=ret, source="kis")
                _save_global_quote("NVDA", price, ret, "kis")
                return result
        except Exception as exc:
            result["error"] = f"KIS NVDA failed: {exc}"

    quote = _quote_with_naver_then_yfinance("NVDA")
    if quote.get("price") is not None:
        result.update(current_price=quote["price"], regular_return=quote.get("return_pct"), source=quote["source"], error=None)
        return result

    cached = _load_global_quote("NVDA")
    if cached:
        result.update(current_price=cached.get("price"), regular_return=cached.get("return_pct"), source="cache")
    return result


def collect_index_data() -> dict:
    """Collect Nasdaq futures, SOXX/SOX, and USD/KRW. Priority: Naver global, yfinance."""
    result = {
        "qqq_return": None,
        "sox_return": None,
        "usdkrw_change": None,
        "source": None,
        "error": None,
        "fallback_detail": {},
        "source_detail": {},
    }

    qqq = _quote_with_naver_then_yfinance("NQ=F")
    nasdaq_proxy = None
    if qqq.get("return_pct") is None:
        nasdaq_proxy = _quote_with_naver_then_yfinance("QQQ")
        if nasdaq_proxy.get("return_pct") is not None:
            qqq = dict(nasdaq_proxy)
            qqq["source"] = f"{qqq.get('source')}_qqq_proxy"
    soxx = _quote_with_naver_then_yfinance("SOXX")
    if soxx.get("return_pct") is None:
        soxx = _quote_with_naver_then_yfinance("SOX")
    usdkrw = _quote_with_naver_then_yfinance("USDKRW")

    values = {
        "NASDAQ_FUTURES": ("qqq_return", qqq),
        "SOXX": ("sox_return", soxx),
        "USDKRW": ("usdkrw_change", usdkrw),
    }
    sources = []
    for symbol, (field, quote) in values.items():
        value = quote.get("return_pct")
        result[field] = value
        ok = value is not None
        if symbol == "NASDAQ_FUTURES" and nasdaq_proxy and ok:
            result["fallback_detail"][symbol] = "QQQ proxy success"
        else:
            result["fallback_detail"][symbol] = "success" if ok else "failed"
        result["source_detail"][symbol] = quote.get("source") if ok else "failed"
        if ok:
            sources.append(quote.get("source"))

    if sources:
        result["source"] = "mixed" if len(set(sources)) > 1 else sources[0]
    else:
        result["error"] = "Nasdaq futures/SOXX/USDKRW collection failed"
    return result


def collect_hynix_daily(mode: Optional[str] = None, n_days: int = 70) -> dict:
    """Collect SK Hynix daily candles and current price.

    Priority: KIS, Naver Finance, yfinance, fresh cache.
    """
    mode = mode or _kis_mode()
    result = {
        "df_daily": None,
        "prev_close": None,
        "current_price": None,
        "source": None,
        "error": None,
        "fallback_chain": [],
        "source_detail": {"current_price": None, "daily_ohlcv": None},
        "stock_identity": {"code": "000660", "name": "SK하이닉스", "ok": False, "message": None},
        "price_validation": {"ok": False, "message": "not collected", "source_prices": {}},
        "current_price_sources": {},
        "collected_at": None,
        "cache_stale": False,
    }
    identity_ok, identity_msg = validate_stock_identity("000660", "SK하이닉스")
    result["stock_identity"] = {"code": "000660", "name": "SK하이닉스", "ok": identity_ok, "message": identity_msg}
    if not identity_ok:
        result["error"] = f"Hynix stock identity validation failed: {identity_msg}"
        return result

    def accept(df: Optional[pd.DataFrame], source: str, current_price: Optional[float] = None, current_source: Optional[str] = None) -> bool:
        identity_ok, identity_msg = validate_stock_identity("000660", "SK하이닉스")
        result["stock_identity"] = {"code": "000660", "name": "SK하이닉스", "ok": identity_ok, "message": identity_msg}
        if not identity_ok:
            result["fallback_chain"].append(f"{source}: identity failed ({identity_msg})")
            return False
        valid, msg, df_ok = validate_hynix_dataframe(df)
        if not valid:
            result["fallback_chain"].append(f"{source}: validation failed ({msg})")
            return False
        last_close = float(df_ok.iloc[-1]["close"])
        if current_price is None:
            result["fallback_chain"].append(f"{source}: current price missing")
            return False
        price = current_price
        price_ok, price_msg = validate_hynix_price(price)
        if not price_ok:
            result["fallback_chain"].append(f"{source}: invalid current price ({price_msg})")
            return False

        _save_hynix_daily(df_ok)
        _save_hynix_current(price, current_source or source)
        result.update(df_daily=df_ok, prev_close=last_close, current_price=float(price), source=source)
        result["source_detail"] = {"current_price": current_source or source, "daily_ohlcv": source}
        result["collected_at"] = datetime.now().isoformat()
        result["fallback_chain"].append(f"{source}: success")
        logger.warning("[HYNIX_PRICE] current_price source=%s value=%s", current_source or source, price)
        logger.warning("[HYNIX_DAILY] last_close=%s prev_close=%s date=%s", last_close, last_close, df_ok.iloc[-1].get("datetime"))
        return True

    kis_current_price = None
    if mode:
        kis_current_price = _fetch_hynix_current_from_kis(mode)
        if kis_current_price is not None:
            result["fallback_chain"].append("KIS current: success")
            logger.warning("[HYNIX_PRICE] current_price source=KIS value=%s", kis_current_price)
        else:
            result["fallback_chain"].append("KIS current: failed")

    df_kis = None
    if mode:
        try:
            df_kis = _fetch_hynix_daily_from_kis(mode, n_days)
            result["fallback_chain"].append("KIS daily: collected")
        except Exception as exc:
            result["error"] = f"KIS Hynix daily failed: {exc}"
            result["fallback_chain"].append(f"KIS: failed ({exc})")
    else:
        result["fallback_chain"].append("KIS: skipped (credentials missing)")

    naver_current_price = None
    try:
        current = _naver_current_price("000660")
        if current.get("status") == "success" and current.get("current_price") is not None:
            naver_current_price = float(current["current_price"])
            result["fallback_chain"].append("Naver current: success")
            logger.warning("[HYNIX_PRICE] current_price source=naver value=%s", naver_current_price)
        else:
            result["fallback_chain"].append(f"Naver current: failed ({current.get('error')})")
    except Exception as exc:
        result["fallback_chain"].append(f"Naver current: failed ({exc})")

    yahoo_current_price = None
    try:
        yahoo_current_price = _fetch_hynix_current_from_yfinance()
        if yahoo_current_price is not None:
            result["fallback_chain"].append("Yahoo current: success")
            logger.warning("[HYNIX_PRICE] current_price source=yfinance value=%s", yahoo_current_price)
        else:
            result["fallback_chain"].append("Yahoo current: failed")
    except Exception as exc:
        result["fallback_chain"].append(f"Yahoo current: failed ({exc})")

    current_sources = {"KIS": kis_current_price, "naver": naver_current_price, "yfinance": yahoo_current_price}
    price_ok, price_msg, price_detail = validate_hynix_current_sources(current_sources)
    result["current_price_sources"] = current_sources
    result["price_validation"] = {"ok": price_ok, "message": price_msg, **price_detail}
    if not price_ok:
        result["error"] = (result.get("error") or "") + f" | Hynix current price validation failed: {price_msg}"
        result["fallback_chain"].append(f"current validation: failed ({price_msg})")
        return result
    validated_current_price = price_detail["selected_price"]
    validated_current_source = price_detail["selected_source"]

    if df_kis is not None and accept(df_kis, "KIS", validated_current_price, validated_current_source):
        return result

    try:
        df_naver = _naver_daily_ohlcv("000660", pages=4)
        anchor_price = validated_current_price
        anchor_source = validated_current_source
        if accept(df_naver, "naver", anchor_price, anchor_source):
            return result
    except Exception as exc:
        result["error"] = (result.get("error") or "") + f" | Naver Hynix daily failed: {exc}"
        result["fallback_chain"].append(f"Naver daily: failed ({exc})")

    try:
        import yfinance as yf

        hist = yf.Ticker("000660.KS").history(period=f"{n_days + 30}d", interval="1d", auto_adjust=True)
        if hist is not None and not hist.empty:
            df_yf = _normalize_yf_ohlcv(hist)
            anchor_price = validated_current_price
            anchor_source = validated_current_source
            if accept(df_yf, "yfinance", anchor_price, anchor_source):
                return result
        else:
            result["fallback_chain"].append("yfinance: no data")
    except Exception as exc:
        result["error"] = (result.get("error") or "") + f" | yfinance Hynix failed: {exc}"
        result["fallback_chain"].append(f"yfinance: failed ({exc})")

    if _HYNIX_DAILY_CSV.exists():
        result["cache_stale"] = True
        result["fallback_chain"].append("cache: present but rejected for live prediction")
        result["error"] = (result.get("error") or "") + " | Hynix live daily collection failed; cache not allowed"

    return result


def collect_kospilab_data(force_refresh: bool = False) -> dict:
    try:
        from app.data_sources.kospilab_scraper import fetch_kospilab_data

        return fetch_kospilab_data(force_refresh=force_refresh)
    except Exception as exc:
        return {
            "hynix_reference_price": None,
            "hynix_reference_return": None,
            "samsung_reference_return": None,
            "hyundai_reference_return": None,
            "source_status": "failed",
            "error_message": str(exc),
        }


def collect_all(mode: Optional[str] = None) -> dict:
    mu = collect_mu_data(mode=mode)
    nvda = collect_nvda_data(mode=mode)
    index = collect_index_data()
    hynix = collect_hynix_daily(mode=mode)
    kospilab = collect_kospilab_data()

    errors = [
        f"MU: {mu['error']}" if mu.get("error") else None,
        f"NVDA: {nvda['error']}" if nvda.get("error") else None,
        f"Index: {index['error']}" if index.get("error") else None,
        f"Hynix: {hynix['error']}" if hynix.get("error") else None,
        f"Kospilab: {kospilab.get('error_message')}" if kospilab.get("source_status") == "failed" else None,
    ]

    return {
        "mu": mu,
        "nvda": nvda,
        "index": index,
        "hynix": hynix,
        "kospilab": kospilab,
        "collected_at": datetime.now().isoformat(),
        "errors": [err for err in errors if err],
    }


def _quote_with_naver_then_yfinance(symbol: str) -> dict:
    quote = _naver_global_quote(symbol)
    if quote.get("status") == "success" and quote.get("price") is not None:
        _save_global_quote(symbol, quote.get("price"), quote.get("return_pct"), quote.get("source"))
        return quote
    yf_quote = _fetch_global_quote_from_yfinance(symbol)
    if yf_quote.get("status") == "success" and yf_quote.get("price") is not None:
        _save_global_quote(symbol, yf_quote.get("price"), yf_quote.get("return_pct"), yf_quote.get("source"))
        return yf_quote
    return quote


def _fetch_global_quote_from_yfinance(symbol: str) -> dict:
    yf_symbol = {
        "SOX": "^SOX",
        "USDKRW": "KRW=X",
    }.get(symbol.upper(), symbol)
    result = {"symbol": symbol, "price": None, "return_pct": None, "source": "yfinance", "status": "failed", "error": None}
    try:
        _configure_yfinance_cache()
        import yfinance as yf

        hist = yf.Ticker(yf_symbol).history(period="5d", interval="1d", auto_adjust=False)
        if hist is None or hist.empty:
            result["error"] = "empty history"
            return result
        close = hist["Close"].dropna()
        if close.empty:
            result["error"] = "missing close"
            return result
        price = float(close.iloc[-1])
        return_pct = None
        if len(close) >= 2 and float(close.iloc[-2]) > 0:
            return_pct = (price / float(close.iloc[-2]) - 1.0) * 100
        result.update(price=price, return_pct=return_pct, status="success")
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result


def _fetch_yfinance_intraday(symbol: str, period: str = "1d", interval: str = "1m") -> Optional[pd.DataFrame]:
    _configure_yfinance_cache()
    import yfinance as yf

    hist = yf.Ticker(symbol).history(period=period, interval=interval, prepost=True)
    if hist is None or hist.empty:
        return None
    df = hist.reset_index()
    df.columns = [str(col).lower() for col in df.columns]
    dt_col = "datetime" if "datetime" in df.columns else ("date" if "date" in df.columns else df.columns[0])
    df = df.rename(columns={dt_col: "datetime"})
    df["datetime"] = pd.to_datetime(df["datetime"])
    df["source"] = "yfinance"
    df["session"] = df["datetime"].apply(_classify_us_session)
    return df[["datetime", "open", "high", "low", "close", "volume", "source", "session"]]


def _fetch_yfinance_daily(symbol: str, period: str = "90d") -> Optional[pd.DataFrame]:
    _configure_yfinance_cache()
    import yfinance as yf

    hist = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=True)
    if hist is None or hist.empty:
        return None
    df = hist.reset_index()
    df.columns = [str(col).lower() for col in df.columns]
    dt_col = next((col for col in df.columns if "date" in col or "datetime" in col), df.columns[0])
    df = df.rename(columns={dt_col: "datetime"})
    df["datetime"] = pd.to_datetime(df["datetime"])
    df["source"] = "yfinance"
    return df[["datetime", "open", "high", "low", "close", "volume", "source"]]


def _fetch_yfinance_quote(symbol: str) -> Optional[dict]:
    quote = _quote_with_naver_then_yfinance(symbol)
    if quote.get("price") is None:
        return None
    return {
        "current_price": quote.get("price"),
        "premarket_return": None,
        "regular_return": quote.get("return_pct"),
    }


def _fetch_hynix_daily_from_kis(mode: str, n_days: int) -> Optional[pd.DataFrame]:
    import requests as rq

    app_key = os.environ.get(f"KIS_{mode.upper()}_APP_KEY", "")
    app_secret = os.environ.get(f"KIS_{mode.upper()}_APP_SECRET", "")
    if not app_key or not app_secret:
        raise ValueError("KIS 인증 정보 없음")

    from app.trading.kis_client import KISClient

    account_no = os.environ.get("KIS_ACCOUNT_NO", "00000000")
    product_code = os.environ.get("KIS_ACCOUNT_PRODUCT_CODE", "01")
    client = KISClient(app_key=app_key, app_secret=app_secret, account_no=account_no, product_code=product_code, mode=mode)
    token = client.get_access_token()

    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": "FHKST01010400",
    }
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=n_days + 30)).strftime("%Y%m%d")
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": "000660",
        "FID_INPUT_DATE_1": start_date,
        "FID_INPUT_DATE_2": end_date,
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0",
    }
    response = rq.get(
        f"{client.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-price",
        headers=headers,
        params=params,
        timeout=15,
    )
    response.raise_for_status()
    body = _safe_json(response)
    if body is None:
        raise ValueError("KIS daily response is not JSON")
    rows = body.get("output2") or body.get("output") or []
    records = []
    for row in rows:
        close = _float_or_none(row.get("stck_clpr"))
        if close is None or close <= 0:
            continue
        records.append(
            {
                "date": str(row.get("stck_bsop_date", "")),
                "datetime": pd.to_datetime(str(row.get("stck_bsop_date", "")), format="%Y%m%d", errors="coerce"),
                "open": _float_or_none(row.get("stck_oprc")) or close,
                "high": _float_or_none(row.get("stck_hgpr")) or close,
                "low": _float_or_none(row.get("stck_lwpr")) or close,
                "close": close,
                "volume": int(_float_or_none(row.get("acml_vol")) or 0),
            }
        )
    if not records:
        return None
    df = pd.DataFrame(records).dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)
    return df


def _fetch_hynix_current_from_kis(mode: str) -> Optional[float]:
    try:
        app_key = os.environ.get(f"KIS_{mode.upper()}_APP_KEY", "")
        app_secret = os.environ.get(f"KIS_{mode.upper()}_APP_SECRET", "")
        if not app_key or not app_secret:
            return None
        from app.trading.kis_client import KISClient

        client = KISClient(
            app_key=app_key,
            app_secret=app_secret,
            account_no=os.environ.get("KIS_ACCOUNT_NO", "00000000"),
            product_code=os.environ.get("KIS_ACCOUNT_PRODUCT_CODE", "01"),
            mode=mode,
        )
        data = client.get_current_price("000660")
        price = _float_or_none((data or {}).get("current_price"))
        ok, _ = validate_hynix_price(price)
        return price if ok else None
    except Exception as exc:
        logger.warning("[HYNIX_PRICE] current_price source=KIS error=%s", exc)
        return None


def _fetch_hynix_current_from_yfinance() -> Optional[float]:
    try:
        _configure_yfinance_cache()
        import yfinance as yf

        hist = yf.Ticker("000660.KS").history(period="5d", interval="1d", auto_adjust=False)
        if hist is None or hist.empty:
            return None
        close = hist["Close"].dropna()
        if close.empty:
            return None
        price = _float_or_none(close.iloc[-1])
        ok, _ = validate_hynix_price(price)
        return price if ok else None
    except Exception as exc:
        logger.warning("[HYNIX_PRICE] current_price source=yfinance error=%s", exc)
        return None


def _fetch_hynix_current_from_pykrx() -> Optional[float]:
    try:
        from pykrx import stock

        end = datetime.now()
        start = end - timedelta(days=10)
        df = stock.get_market_ohlcv_by_date(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), "000660")
        if df is None or df.empty:
            return None
        close_col = "종가" if "종가" in df.columns else "Close"
        price = _float_or_none(df.iloc[-1][close_col])
        ok, _ = validate_hynix_price(price)
        return price if ok else None
    except Exception as exc:
        logger.warning("[HYNIX_PRICE] current_price source=pykrx error=%s", exc)
        return None


def _normalize_yf_ohlcv(hist: pd.DataFrame) -> pd.DataFrame:
    df = hist.reset_index()
    df.columns = [str(col).lower() for col in df.columns]
    dt_col = next((col for col in df.columns if "date" in col or "datetime" in col), df.columns[0])
    df = df.rename(columns={dt_col: "datetime"})
    df["datetime"] = pd.to_datetime(df["datetime"])
    df["date"] = df["datetime"].dt.strftime("%Y.%m.%d")
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            df[col] = None
    return df[["date", "datetime", "open", "high", "low", "close", "volume"]]


def _resample_3min(df_1min: pd.DataFrame) -> pd.DataFrame:
    work = df_1min.copy()
    work["datetime"] = pd.to_datetime(work["datetime"])
    result = (
        work.resample("3min", on="datetime")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["close"])
        .reset_index()
    )
    source = work["source"].iloc[-1] if "source" in work.columns and not work.empty else None
    result["source"] = source or "resampled"
    return result


def _validate_real_candles(df: Optional[pd.DataFrame], source: str) -> tuple[bool, str, Optional[pd.DataFrame]]:
    if df is None or df.empty:
        return False, "unavailable", None
    required = {"datetime", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        return False, f"missing columns: {sorted(missing)}", None
    work = df.copy()
    work["datetime"] = pd.to_datetime(work["datetime"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume"]:
        work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=["datetime", "open", "high", "low", "close"])
    if len(work) < 10:
        return False, f"row count < 10 ({len(work)})", None
    if "price" in work.columns and not {"open", "high", "low", "close"}.issubset(work.columns):
        return False, "price-only quote data", None
    close_span = float(work["close"].max() - work["close"].min())
    close_mean = float(work["close"].mean())
    if close_mean <= 0 or close_span / close_mean < 0.0005:
        return False, "constant close values; synthetic rejected", None
    if work["volume"].fillna(0).sum() <= 0:
        return False, "volume missing or zero", None
    if ((work["open"] == work["high"]) & (work["high"] == work["low"]) & (work["low"] == work["close"])).mean() > 0.95:
        return False, "OHLC values copied from one quote; synthetic rejected", None
    work["source"] = source
    if "session" not in work.columns:
        work["session"] = work["datetime"].apply(_classify_us_session)
    return True, "real candle success", work.reset_index(drop=True)


def _validate_real_daily_candles(df: Optional[pd.DataFrame], source: str) -> tuple[bool, str, Optional[pd.DataFrame]]:
    ok, reason, work = _validate_real_candles(df, source)
    if not ok:
        return ok, reason, work
    if work is None or len(work) < 20:
        return False, f"daily row count < 20 ({0 if work is None else len(work)})", None
    return True, "real candle success", work


def _classify_us_session(ts) -> str:
    try:
        t = pd.Timestamp(ts)
        if t.tzinfo is not None:
            t = t.tz_convert("America/New_York")
        hour_min = t.hour * 60 + t.minute
        if hour_min < 9 * 60 + 30:
            return "premarket"
        if hour_min <= 16 * 60:
            return "regular"
        return "aftermarket"
    except Exception:
        return "regular"


def _float_or_none(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _save_hynix_daily(df: pd.DataFrame) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(_HYNIX_DAILY_CSV, index=False, encoding="utf-8-sig")
    try:
        LEGACY_HYNIX_DIR.mkdir(parents=True, exist_ok=True)
        df.to_csv(LEGACY_HYNIX_DIR / "hynix_daily.csv", index=False, encoding="utf-8-sig")
    except Exception:
        pass


def _load_hynix_daily_cache() -> Optional[pd.DataFrame]:
    if not _fresh_cache(_HYNIX_DAILY_CSV):
        return None
    try:
        df = pd.read_csv(_HYNIX_DAILY_CSV)
        if "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        return df
    except Exception:
        return None


def _save_hynix_current(price: float, source: str) -> None:
    _write_json_cache(_HYNIX_CURRENT_JSON, {"current_price": float(price), "source": source})


def _load_hynix_current_cache() -> Optional[dict]:
    return _read_json_cache(_HYNIX_CURRENT_JSON)


def _save_mu_1min(df: pd.DataFrame) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        df.to_csv(_MU_1MIN_CSV, index=False, encoding="utf-8-sig")
    except Exception:
        pass


def _load_mu_1min_cache() -> Optional[pd.DataFrame]:
    if not _fresh_cache(_MU_1MIN_CSV):
        return None
    try:
        df = pd.read_csv(_MU_1MIN_CSV)
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        return df.dropna(subset=["datetime"])
    except Exception:
        return None


def _save_global_quote(symbol: str, price: Optional[float], return_pct: Optional[float], source: Optional[str]) -> None:
    if price is None and return_pct is None:
        return
    payload = _read_json_cache(_GLOBAL_QUOTES_JSON) or {}
    payload[symbol.upper()] = {
        "price": price,
        "return_pct": return_pct,
        "source": source,
        "updated_at": datetime.now().isoformat(),
    }
    _write_json_cache(_GLOBAL_QUOTES_JSON, payload)


def _load_global_quote(symbol: str) -> Optional[dict]:
    payload = _read_json_cache(_GLOBAL_QUOTES_JSON)
    if not payload:
        return None
    return payload.get(symbol.upper())


def _load_complete_index_cache() -> Optional[dict]:
    qqq = _load_global_quote("QQQ")
    soxx = _load_global_quote("SOXX")
    usdkrw = _load_global_quote("USDKRW")
    if not (qqq and soxx and usdkrw):
        return None
    if any(item.get("return_pct") is None for item in (qqq, soxx, usdkrw)):
        return None
    return {
        "qqq_return": qqq.get("return_pct"),
        "sox_return": soxx.get("return_pct"),
        "usdkrw_change": usdkrw.get("return_pct"),
        "error": None,
    }
