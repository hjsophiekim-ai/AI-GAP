"""
auto_market_collector.py — 시장 데이터 자동 수집 허브.

우선순위:
1. KIS Open API (MU·NVDA 해외주식, 하이닉스 국내주식)
2. 코스피랩 (SK하이닉스 해외 참고가)
3. yfinance (MU·NVDA·QQQ·SOXX·USD/KRW·KOSPI·NASDAQ)
4. 수동 입력 fallback

API 키, 계좌번호, secret은 .env에서만 읽습니다.
실전 주문 기능과 절대 연결하지 않습니다.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

try:
    from app.data.safe_api import safe_json as _safe_json
    _SAFE_API_OK = True
except ImportError:
    _SAFE_API_OK = False

    def _safe_json(response):  # type: ignore[misc]
        try:
            return response.json() if response else None
        except Exception:
            return None

try:
    from app.data.naver_stock_collector import (
        fetch_naver_daily_ohlcv as _naver_daily_ohlcv,
        fetch_naver_current_price as _naver_current_price,
    )
    _NAVER_COLLECTOR_OK = True
except ImportError:
    _NAVER_COLLECTOR_OK = False

    def _naver_daily_ohlcv(code="000660", pages=3):  # type: ignore[misc]
        return None

    def _naver_current_price(code="000660"):  # type: ignore[misc]
        return {"current_price": None, "status": "failed"}

try:
    from app.data.naver_global_stock_collector import fetch_naver_global_quote as _naver_global_quote
    _NAVER_GLOBAL_OK = True
except ImportError:
    _NAVER_GLOBAL_OK = False

    def _naver_global_quote(symbol):  # type: ignore[misc]
        return {"price": None, "return_pct": None, "source": "failed", "status": "failed"}

try:
    from app.data.market_data_validator import validate_hynix_dataframe
    _HYNIX_VALIDATOR_OK = True
except ImportError:
    _HYNIX_VALIDATOR_OK = False

    def validate_hynix_dataframe(df):  # type: ignore[misc]
        if df is None or df.empty:
            return False, "데이터 없음", df
        valid = df[df["close"].apply(lambda x: 50_000 <= x <= 1_000_000)].reset_index(drop=True)
        return (len(valid) >= 20, f"유효 행: {len(valid)}", valid)

_ROOT = Path(__file__).resolve().parent.parent.parent
_MICRON_DIR = _ROOT / "data" / "micron"
_HYNIX_DIR  = _ROOT / "data" / "hynix"

_HYNIX_DAILY_CSV = _HYNIX_DIR / "hynix_daily.csv"


# ── KIS 자격증명 체크 ─────────────────────────────────────────────────────────

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


# ── 1. MU 데이터 수집 ─────────────────────────────────────────────────────────

def collect_mu_data(mode: Optional[str] = None) -> dict:
    """
    MU 1분봉·현재가 수집.

    Returns
    -------
    dict with keys:
        df_1min, df_3min, current_price, source, error
    """
    mode = mode or _kis_mode()
    result = {"df_1min": None, "df_3min": None, "current_price": None, "source": None, "error": None}

    # 1순위: KIS API
    if mode:
        try:
            from app.data_sources.kis_overseas_minute import (
                collect_and_save_mu,
                fetch_mu_current_price,
            )
            collect_and_save_mu(mode=mode)
            current_price = fetch_mu_current_price(mode=mode)

            df_1min = pd.read_csv(_MICRON_DIR / "MU_1min.csv") if (_MICRON_DIR / "MU_1min.csv").exists() else None
            df_3min = pd.read_csv(_MICRON_DIR / "MU_3min.csv") if (_MICRON_DIR / "MU_3min.csv").exists() else None

            if df_1min is not None and not df_1min.empty:
                result.update(df_1min=df_1min, df_3min=df_3min, current_price=current_price, source="kis")
                return result
        except Exception as e:
            result["error"] = f"KIS MU 수집 실패: {e}"

    # 2순위: yfinance
    try:
        mu_yf = _fetch_yfinance_intraday("MU", period="1d", interval="1m")
        if mu_yf is not None and not mu_yf.empty:
            df_3min = mu_yf.resample("3min", on="datetime").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
            ).dropna(subset=["close"]).reset_index()
            last_row = mu_yf.iloc[-1]
            current_price = {
                "price": float(last_row["close"]),
                "open":  float(mu_yf.iloc[0]["open"]),
                "high":  float(mu_yf["high"].max()),
                "low":   float(mu_yf["low"].min()),
            }
            result.update(df_1min=mu_yf, df_3min=df_3min, current_price=current_price, source="yfinance")
            return result
    except Exception as e:
        result["error"] = (result.get("error") or "") + f" | yfinance MU 실패: {e}"

    # 3순위: Naver 글로벌 (현재가만)
    try:
        naver_mu = _naver_global_quote("MU")
        if naver_mu.get("status") == "success" and naver_mu.get("price"):
            cp = naver_mu["price"]
            result.update(
                current_price={"price": cp, "open": None, "high": None, "low": None},
                source="naver_global",
            )
    except Exception:
        pass

    return result


# ── 2. NVDA 데이터 수집 ───────────────────────────────────────────────────────

def collect_nvda_data(mode: Optional[str] = None) -> dict:
    """
    NVDA 현재가 및 등락률 수집.

    Returns
    -------
    dict with keys:
        current_price, premarket_return, regular_return, source, error
    """
    mode = mode or _kis_mode()
    result = {"current_price": None, "premarket_return": None, "regular_return": None, "source": None, "error": None}

    # 1순위: KIS API (해외주식현재가)
    if mode:
        try:
            from app.data_sources.kis_overseas_minute import _get_access_token, _load_credentials
            import requests as rq

            base_url = "real" if mode == "real" else "mock"
            from app.data_sources.kis_overseas_minute import BASE_URL_REAL, BASE_URL_MOCK
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
            resp = rq.get(f"{base_url}/uapi/overseas-stock/v1/quotations/price", headers=headers, params=params, timeout=10)
            body = _safe_json(resp)
            if body is None:
                raise ValueError("NVDA KIS 응답이 JSON이 아님 (HTML 또는 빈 응답)")
            out = body.get("output", {})
            price_str = out.get("last") or out.get("zdiv")
            if price_str:
                price = float(price_str)
                change_pct_str = out.get("rate") or out.get("diff_rate")
                change_pct = float(change_pct_str) if change_pct_str else None
                result.update(current_price=price, regular_return=change_pct, source="kis")
                return result
        except Exception as e:
            result["error"] = f"KIS NVDA 수집 실패: {e}"

    # 2순위: yfinance
    try:
        info = _fetch_yfinance_quote("NVDA")
        if info:
            result.update(**info, source="yfinance")
            return result
    except Exception as e:
        result["error"] = (result.get("error") or "") + f" | yfinance NVDA 실패: {e}"

    return result


# ── 3. 지수/ETF 데이터 수집 ───────────────────────────────────────────────────

def collect_index_data() -> dict:
    """
    QQQ, SOXX(^SOX fallback), USD/KRW 수집 (yfinance 개별 다운로드).

    Returns
    -------
    dict with keys:
        qqq_return, sox_return, usdkrw_change, source, error, fallback_detail
    """
    result: dict = {
        "qqq_return": None,
        "sox_return": None,
        "usdkrw_change": None,
        "source": None,
        "error": None,
        "fallback_detail": {},
    }

    def _pct(sym: str) -> Optional[float]:
        """yfinance 개별 티커 등락률."""
        try:
            import yfinance as yf
            t = yf.Ticker(sym)
            hist = t.history(period="5d", interval="1d", auto_adjust=True)
            if hist.empty:
                return None
            closes = hist["Close"].dropna()
            if len(closes) < 2:
                return None
            return round(float(closes.iloc[-1] / closes.iloc[-2] - 1) * 100, 2)
        except Exception as exc:
            logger.debug("yfinance %s 실패: %s", sym, exc)
            return None

    qqq    = _pct("QQQ")
    sox    = _pct("SOXX")
    if sox is None:
        sox = _pct("^SOX")
    usdkrw = _pct("USDKRW=X")

    result["qqq_return"]    = qqq
    result["sox_return"]    = sox
    result["usdkrw_change"] = usdkrw
    result["fallback_detail"] = {
        "QQQ":    "성공" if qqq    is not None else "실패",
        "SOXX":   "성공" if sox    is not None else "실패",
        "USDKRW": "성공" if usdkrw is not None else "실패",
    }

    n_ok = sum(1 for v in [qqq, sox, usdkrw] if v is not None)
    if n_ok > 0:
        result["source"] = "yfinance"
    else:
        result["error"] = "QQQ/SOXX/USD·KRW 모두 수집 실패"

    return result


# ── 4. SK하이닉스 일봉 데이터 수집 ───────────────────────────────────────────

def collect_hynix_daily(mode: Optional[str] = None, n_days: int = 70) -> dict:
    """
    SK하이닉스(000660) 일봉 수집 및 저장.

    수집 우선순위: KIS → Naver 금융 → yfinance → 캐시 (24시간 이내만)

    Returns
    -------
    dict with keys:
        df_daily, prev_close, current_price, source, error, fallback_chain
    """
    import time as _time

    mode = mode or _kis_mode()
    result: dict = {
        "df_daily": None,
        "prev_close": None,
        "current_price": None,
        "source": None,
        "error": None,
        "fallback_chain": [],
    }

    def _accept(df, src: str) -> bool:
        """검증 통과 시 result 업데이트 후 True."""
        valid, msg, df_ok = validate_hynix_dataframe(df)
        if valid:
            _save_hynix_daily(df_ok)
            last_close = float(df_ok.iloc[-1]["close"])
            result.update(
                df_daily=df_ok,
                prev_close=last_close,
                current_price=last_close,
                source=src,
            )
            result["fallback_chain"].append(f"{src}: 성공")
            return True
        result["fallback_chain"].append(f"{src}: 검증 실패 ({msg})")
        return False

    # 1순위: KIS 국내주식 일봉
    if mode:
        try:
            df_kis = _fetch_hynix_daily_from_kis(mode, n_days)
            if df_kis is not None and not df_kis.empty:
                if _accept(df_kis, "KIS"):
                    return result
        except Exception as exc:
            result["fallback_chain"].append(f"KIS: 실패 ({exc})")
            result["error"] = f"KIS 하이닉스 일봉 실패: {exc}"
    else:
        result["fallback_chain"].append("KIS: 건너뜀 (인증 키 없음)")

    # 2순위: Naver 금융 일별시세
    try:
        df_naver = _naver_daily_ohlcv("000660", pages=4)
        if df_naver is not None and not df_naver.empty:
            if _accept(df_naver, "naver"):
                return result
    except Exception as exc:
        result["fallback_chain"].append(f"Naver: 실패 ({exc})")
        result["error"] = (result.get("error") or "") + f" | Naver 일봉 실패: {exc}"

    # 3순위: yfinance 000660.KS
    try:
        import yfinance as yf
        ticker = yf.Ticker("000660.KS")
        hist = ticker.history(period=f"{n_days + 30}d", interval="1d", auto_adjust=True)
        if not hist.empty:
            df = _normalize_yf_ohlcv(hist)
            if _accept(df, "yfinance"):
                return result
        else:
            result["fallback_chain"].append("yfinance: 빈 응답")
    except Exception as exc:
        result["fallback_chain"].append(f"yfinance: 실패 ({exc})")
        result["error"] = (result.get("error") or "") + f" | yfinance 하이닉스 실패: {exc}"

    # 4순위: 캐시 CSV (24시간 이내만)
    if _HYNIX_DAILY_CSV.exists():
        try:
            mtime = _HYNIX_DAILY_CSV.stat().st_mtime
            age_hours = (_time.time() - mtime) / 3600
            if age_hours > 24:
                result["fallback_chain"].append(f"캐시: 오래됨 ({age_hours:.1f}시간)")
                result["error"] = (result.get("error") or "") + " | 캐시 24시간 초과 — 예측 불가"
            else:
                df = pd.read_csv(_HYNIX_DAILY_CSV)
                if "close" in df.columns and not df.empty:
                    if _accept(df, "cache"):
                        result["error"] = (result.get("error") or "") + f" | 캐시 사용 ({age_hours:.1f}시간 전)"
                        return result
        except Exception as exc:
            result["fallback_chain"].append(f"캐시: 로드 실패 ({exc})")
            result["error"] = (result.get("error") or "") + f" | 캐시 실패: {exc}"

    return result


# ── 5. 코스피랩 참고가 수집 ───────────────────────────────────────────────────

def collect_kospilab_data(force_refresh: bool = False) -> dict:
    """
    코스피랩 SK하이닉스 해외 참고가 수집.
    """
    try:
        from app.data_sources.kospilab_scraper import fetch_kospilab_data
        return fetch_kospilab_data(force_refresh=force_refresh)
    except Exception as e:
        return {
            "hynix_reference_price":    None,
            "hynix_reference_return":   None,
            "samsung_reference_return": None,
            "hyundai_reference_return": None,
            "source_status": "failed",
            "error_message": str(e),
        }


# ── 6. 전체 자동 수집 진입점 ──────────────────────────────────────────────────

def collect_all(mode: Optional[str] = None) -> dict:
    """
    MU·NVDA·지수·SK하이닉스·코스피랩 데이터를 한 번에 수집.

    Parameters
    ----------
    mode : 'real' | 'mock' | None (None이면 자동 감지)

    Returns
    -------
    dict
        mu      : MU 수집 결과 (df_1min, df_3min, current_price, source, error)
        nvda    : NVDA 수집 결과
        index   : 지수/ETF 수집 결과
        hynix   : SK하이닉스 일봉 수집 결과
        kospilab: 코스피랩 수집 결과
        collected_at : ISO 타임스탬프
        errors  : 오류 목록
    """
    mu       = collect_mu_data(mode=mode)
    nvda     = collect_nvda_data(mode=mode)
    index    = collect_index_data()
    hynix    = collect_hynix_daily(mode=mode)
    kospilab = collect_kospilab_data()

    errors = [
        f"MU: {mu['error']}"       if mu.get("error")       else None,
        f"NVDA: {nvda['error']}"   if nvda.get("error")     else None,
        f"지수: {index['error']}"  if index.get("error")    else None,
        f"하이닉스: {hynix['error']}" if hynix.get("error") else None,
        f"코스피랩: {kospilab.get('error_message')}" if kospilab.get("source_status") == "failed" else None,
    ]
    errors = [e for e in errors if e]

    return {
        "mu":          mu,
        "nvda":        nvda,
        "index":       index,
        "hynix":       hynix,
        "kospilab":    kospilab,
        "collected_at": datetime.now().isoformat(),
        "errors":      errors,
    }


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

def _fetch_yfinance_intraday(symbol: str, period: str = "1d", interval: str = "1m") -> Optional[pd.DataFrame]:
    """yfinance 분봉 수집 → datetime/open/high/low/close/volume DataFrame."""
    import yfinance as yf
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period=period, interval=interval)
    if hist.empty:
        return None
    hist = hist.reset_index()
    hist.columns = [c.lower() for c in hist.columns]
    dt_col = "datetime" if "datetime" in hist.columns else hist.columns[0]
    if dt_col != "datetime":
        hist = hist.rename(columns={dt_col: "datetime"})
    hist["datetime"] = pd.to_datetime(hist["datetime"])
    return hist[["datetime", "open", "high", "low", "close", "volume"]]


def _fetch_yfinance_quote(symbol: str) -> Optional[dict]:
    """yfinance 당일 시세 → {current_price, premarket_return, regular_return}."""
    import yfinance as yf
    ticker = yf.Ticker(symbol)
    info = ticker.fast_info
    try:
        price = float(info.last_price or 0) or None
        prev  = float(info.previous_close or 0) or None
        reg_ret = round((price / prev - 1) * 100, 2) if price and prev else None
        return {"current_price": price, "premarket_return": None, "regular_return": reg_ret}
    except Exception:
        return None


def _fetch_hynix_daily_from_kis(mode: str, n_days: int) -> Optional[pd.DataFrame]:
    """KIS 국내주식 일봉 API로 SK하이닉스 수집."""
    import requests as rq

    if mode == "real":
        app_key    = os.environ.get("KIS_REAL_APP_KEY", "")
        app_secret = os.environ.get("KIS_REAL_APP_SECRET", "")
    else:
        app_key    = os.environ.get("KIS_MOCK_APP_KEY", "")
        app_secret = os.environ.get("KIS_MOCK_APP_SECRET", "")

    account_no   = os.environ.get("KIS_ACCOUNT_NO", "00000000")
    product_code = os.environ.get("KIS_ACCOUNT_PRODUCT_CODE", "01")

    if not app_key or not app_secret:
        raise ValueError(
            f"KIS {mode} 인증 정보 없음 — "
            f".env에서 KIS_{mode.upper()}_APP_KEY, KIS_{mode.upper()}_APP_SECRET 설정 필요"
        )

    from app.trading.kis_client import KISClient
    client = KISClient(
        app_key=app_key,
        app_secret=app_secret,
        account_no=account_no,
        product_code=product_code,
        mode=mode,
    )
    token    = client.get_access_token()
    base_url = client.base_url
    headers  = {
        "Content-Type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": "FHKST01010400",
    }

    end_date   = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=n_days + 30)).strftime("%Y%m%d")

    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD":         "000660",
        "FID_INPUT_DATE_1":       start_date,
        "FID_INPUT_DATE_2":       end_date,
        "FID_PERIOD_DIV_CODE":    "D",
        "FID_ORG_ADJ_PRC":        "0",
    }
    resp = rq.get(
        f"{base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-price",
        headers=headers, params=params, timeout=15,
    )
    resp.raise_for_status()
    body = _safe_json(resp)
    if body is None:
        raise ValueError("KIS 일봉 API 응답이 JSON이 아님 (HTML 또는 빈 응답)")
    rows = body.get("output2") or body.get("output") or []
    if not rows:
        return None

    records = []
    for r in rows:
        try:
            close = float(r.get("stck_clpr", 0) or 0)
            if close <= 0:
                continue
            records.append({
                "datetime": r.get("stck_bsop_date", ""),
                "open":   float(r.get("stck_oprc", 0) or 0),
                "high":   float(r.get("stck_hgpr", 0) or 0),
                "low":    float(r.get("stck_lwpr", 0) or 0),
                "close":  close,
                "volume": int(r.get("acml_vol", 0) or 0),
            })
        except Exception:
            continue

    df = pd.DataFrame(records)
    df = df[df["close"] > 0].sort_values("datetime").reset_index(drop=True)
    return df if not df.empty else None


def _normalize_yf_ohlcv(hist: pd.DataFrame) -> pd.DataFrame:
    """yfinance history → 표준 OHLCV DataFrame."""
    df = hist.reset_index()
    df.columns = [c.lower() for c in df.columns]
    dt_col = next((c for c in df.columns if "date" in c or "datetime" in c), df.columns[0])
    df = df.rename(columns={dt_col: "datetime"})
    df["datetime"] = pd.to_datetime(df["datetime"])
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            df[col] = None
    return df[["datetime", "open", "high", "low", "close", "volume"]]


def _save_hynix_daily(df: pd.DataFrame) -> None:
    """SK하이닉스 일봉 CSV 저장."""
    _HYNIX_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(_HYNIX_DAILY_CSV, index=False, encoding="utf-8-sig")
