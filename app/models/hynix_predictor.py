"""
hynix_predictor.py — SK하이닉스(000660) 가격 예측 모델.

규칙 기반 + 가중치 모델로 구현.
추후 LightGBM/XGBoost 등 머신러닝으로 교체 가능한 인터페이스 유지.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent.parent
_WEIGHTS_PATH = _ROOT / "config" / "hynix_model_weights.json"

MODEL_VERSION = "rule_based_v1.0"


# ── 가중치 로드 ───────────────────────────────────────────────────────────────

def _load_weights() -> dict:
    """config/hynix_model_weights.json 로드. 파일 없으면 초기값 반환."""
    defaults = {
        "micron_premarket_aftermarket": 0.45,
        "kospilab_expected_price":      0.25,
        "sox_index":                    0.10,
        "nvda":                         0.07,
        "qqq_nasdaq_futures":           0.05,
        "usd_krw":                      0.03,
        "hynix_momentum_volume":        0.05,
    }
    try:
        if _WEIGHTS_PATH.exists():
            with open(_WEIGHTS_PATH, "r", encoding="utf-8") as f:
                return json.load(f).get("weights", defaults)
    except Exception:
        pass
    return defaults


# ── 공개 예측 함수 ────────────────────────────────────────────────────────────

def predict_hynix(
    # Micron feature dict (compute_micron_features() 결과)
    micron_features: dict,
    # 코스피랩 수동 입력
    kospilab_expected_price: Optional[float] = None,
    kospilab_expected_return_pct: Optional[float] = None,
    # 지수/ETF 등락률 (%)
    sox_return_pct: Optional[float] = None,
    nvda_return_pct: Optional[float] = None,
    qqq_return_pct: Optional[float] = None,
    usd_krw_change_pct: Optional[float] = None,
    # SK하이닉스 자체 지표
    hynix_prev_close: Optional[float] = None,
    hynix_prev_return_pct: Optional[float] = None,
    hynix_return_3d_pct: Optional[float] = None,
    hynix_return_5d_pct: Optional[float] = None,
    hynix_return_10d_pct: Optional[float] = None,
    hynix_volume_change_pct: Optional[float] = None,
) -> dict:
    """
    SK하이닉스 가격 예측.

    Returns
    -------
    dict
        오늘 예상 OHLC, 내일/3일후 등락률, 2주 최고/최저점,
        상승/하락 확률, 신뢰도 점수, 메타정보 포함.
    """
    weights = _load_weights()

    signals = _build_signals(
        micron_features=micron_features,
        kospilab_return=kospilab_expected_return_pct,
        sox_return=sox_return_pct,
        nvda_return=nvda_return_pct,
        qqq_return=qqq_return_pct,
        usd_krw_change=usd_krw_change_pct,
        hynix_prev_return=hynix_prev_return_pct,
        hynix_return_3d=hynix_return_3d_pct,
        hynix_return_5d=hynix_return_5d_pct,
        hynix_volume_change=hynix_volume_change_pct,
    )

    composite = _weighted_composite(signals, weights)

    today_return_pct = _estimate_today_return(
        composite=composite,
        kospilab_return=kospilab_expected_return_pct,
        micron_strength=micron_features.get("micron_session_strength_score"),
    )

    # 기준 가격: 전일 종가 > 코스피랩 예상가 역산 > 0
    base_price = hynix_prev_close
    if (base_price is None or base_price <= 0) and kospilab_expected_price and kospilab_expected_return_pct is not None:
        base_price = kospilab_expected_price / (1 + kospilab_expected_return_pct / 100)
    if (base_price is None or base_price <= 0) and kospilab_expected_price:
        base_price = kospilab_expected_price
    if base_price is None:
        base_price = 0.0

    today_prices   = _estimate_price_range(base_price, today_return_pct, composite)
    tomorrow_return = _estimate_future_return(composite, days=1)
    day3_return     = _estimate_future_return(composite, days=3)
    two_week        = _estimate_two_week_range(base_price, composite)
    up_prob, dn_prob = _estimate_probabilities(composite)
    confidence       = _estimate_confidence(signals, micron_features)

    return {
        "today_open_expected":  today_prices["open"],
        "today_high_expected":  today_prices["high"],
        "today_low_expected":   today_prices["low"],
        "today_close_expected": today_prices["close"],
        "today_return_pct":     round(today_return_pct, 2),
        "tomorrow_return_pct":  round(tomorrow_return, 2),
        "day3_return_pct":      round(day3_return, 2),
        "two_week_high_date":   two_week["high_date"],
        "two_week_high_price":  two_week["high_price"],
        "two_week_high_prob":   two_week["high_prob"],
        "two_week_low_date":    two_week["low_date"],
        "two_week_low_price":   two_week["low_price"],
        "two_week_low_prob":    two_week["low_prob"],
        "up_probability":       round(up_prob, 1),
        "down_probability":     round(dn_prob, 1),
        "confidence_score":     round(confidence, 1),
        "predicted_at":         datetime.now().isoformat(),
        "model_version":        MODEL_VERSION,
        "weights_used":         weights,
        "composite_signal":     round(composite, 4),
        "signals":              signals,
    }


# ── 내부 헬퍼 ────────────────────────────────────────────────────────────────

def _norm(val: Optional[float], scale: float = 3.0) -> float:
    """등락률(%) → -1~+1 정규화 (scale%에서 포화)."""
    if val is None:
        return 0.0
    return max(-1.0, min(1.0, val / scale))


def _build_signals(
    micron_features: dict,
    kospilab_return: Optional[float],
    sox_return: Optional[float],
    nvda_return: Optional[float],
    qqq_return: Optional[float],
    usd_krw_change: Optional[float],
    hynix_prev_return: Optional[float],
    hynix_return_3d: Optional[float],
    hynix_return_5d: Optional[float],
    hynix_volume_change: Optional[float],
) -> dict:
    """각 지표를 -1 ~ +1 방향 신호로 변환. 데이터 없으면 None."""
    pm_ret   = micron_features.get("micron_premarket_return")
    pm_mom30 = micron_features.get("micron_premarket_30m_momentum")
    pm_mom60 = micron_features.get("micron_premarket_60m_momentum")
    strength = micron_features.get("micron_session_strength_score")
    af_ret   = micron_features.get("micron_aftermarket_return")

    # 마이크론 신호: 가용한 서브-신호만 집계, 가중 평균으로 정규화
    mu_num, mu_den = 0.0, 0.0
    for val, scale, w in [
        (pm_ret, 3.0, 0.40), (pm_mom30, 2.0, 0.20),
        (pm_mom60, 2.0, 0.15), (af_ret, 2.0, 0.10),
    ]:
        if val is not None:
            mu_num += _norm(val, scale) * w
            mu_den += w
    if strength is not None:
        mu_num += (strength - 50) / 50 * 0.15
        mu_den += 0.15
    micron_signal = mu_num / mu_den if mu_den > 0 else None

    # 하이닉스 자체 신호: 가용한 것만 집계
    hy_num, hy_den = 0.0, 0.0
    for val, scale, w in [
        (hynix_prev_return, 3.0, 0.30), (hynix_return_3d, 5.0, 0.30),
        (hynix_return_5d, 7.0, 0.20), (hynix_volume_change, 30.0, 0.20),
    ]:
        if val is not None:
            hy_num += _norm(val, scale) * w
            hy_den += w
    hynix_self = hy_num / hy_den if hy_den > 0 else None

    return {
        "micron":     round(micron_signal, 4) if micron_signal is not None else None,
        "kospilab":   round(_norm(kospilab_return, 2.0), 4) if kospilab_return is not None else None,
        "sox":        round(_norm(sox_return, 2.0), 4) if sox_return is not None else None,
        "nvda":       round(_norm(nvda_return, 3.0), 4) if nvda_return is not None else None,
        "qqq":        round(_norm(qqq_return, 2.0), 4) if qqq_return is not None else None,
        # 환율 상승은 외국인 수급에 부정적
        "usd_krw":    round(_norm(-usd_krw_change, 1.5), 4) if usd_krw_change is not None else None,
        "hynix_self": round(hynix_self, 4) if hynix_self is not None else None,
    }


def _weighted_composite(signals: dict, weights: dict) -> float:
    """신호 × 가중치 합산. None 신호는 제외하고 가용 가중치로 정규화."""
    mapping = {
        "micron":     "micron_premarket_aftermarket",
        "kospilab":   "kospilab_expected_price",
        "sox":        "sox_index",
        "nvda":       "nvda",
        "qqq":        "qqq_nasdaq_futures",
        "usd_krw":    "usd_krw",
        "hynix_self": "hynix_momentum_volume",
    }
    total, weight_sum = 0.0, 0.0
    for s, w_key in mapping.items():
        val = signals.get(s)
        if val is not None:
            w = weights.get(w_key, 0.0)
            total += val * w
            weight_sum += w
    return total / weight_sum if weight_sum > 1e-9 else 0.0


def _estimate_today_return(
    composite: float,
    kospilab_return: Optional[float],
    micron_strength: Optional[float],
) -> float:
    """오늘 예상 등락률(%)."""
    base = composite * 5.0  # composite ±1 → ±5%

    # 코스피랩 입력이 있으면 30% 반영
    if kospilab_return is not None:
        base = base * 0.70 + kospilab_return * 0.30

    # 마이크론 강도 보정 (±0.5%)
    if micron_strength is not None:
        base += (micron_strength - 50) / 50 * 0.5

    return round(base, 4)


def _estimate_price_range(
    base_price: float,
    today_return_pct: float,
    composite: float,
) -> dict:
    """오늘 예상 OHLC (호가단위 반올림)."""
    if base_price <= 0:
        return {"open": None, "high": None, "low": None, "close": None}

    volatility = abs(composite) * 2.0 + 1.5  # 최소 1.5% 변동폭
    close  = base_price * (1 + today_return_pct / 100)
    open_px = base_price * (1 + today_return_pct * 0.4 / 100)

    if composite >= 0:
        high = close   * (1 + volatility / 200)
        low  = open_px * (1 - volatility / 300)
    else:
        high = open_px * (1 + volatility / 300)
        low  = close   * (1 - volatility / 200)

    return {
        "open":  _round_krx(open_px),
        "high":  _round_krx(high),
        "low":   _round_krx(low),
        "close": _round_krx(close),
    }


def _round_krx(price: float) -> int:
    """KRX 호가 단위로 반올림."""
    if price <= 0:
        return 0
    if price < 5_000:      unit = 5
    elif price < 10_000:   unit = 10
    elif price < 50_000:   unit = 50
    elif price < 100_000:  unit = 100
    elif price < 500_000:  unit = 500
    else:                  unit = 1_000
    return int(round(price / unit) * unit)


def _estimate_future_return(composite: float, days: int) -> float:
    """내일/N일 후 예상 등락률 (시간 감쇠 적용)."""
    decay = 0.6 ** (days - 1)
    return round(composite * 4.0 * decay, 4)


def _estimate_two_week_range(base_price: float, composite: float) -> dict:
    """향후 2주 예상 최고/최저점 날짜·가격·확률."""
    empty = {
        "high_date": None, "high_price": None, "high_prob": None,
        "low_date":  None, "low_price":  None, "low_prob":  None,
    }
    if base_price <= 0:
        return empty

    today = datetime.now()

    # 강한 상승: 고점 빠름, 약한 상승: 고점 늦음
    if composite > 0.3:
        high_days, low_days = 5, 10
    elif composite > 0:
        high_days, low_days = 8, 3
    else:
        high_days, low_days = 3, 8

    high_date = _add_trading_days(today, high_days)
    low_date  = _add_trading_days(today, low_days)

    magnitude = min(abs(composite) * 10 + 5, 15)
    if composite >= 0:
        high_price = base_price * (1 + magnitude / 100)
        low_price  = base_price * (1 - magnitude / 2 / 100)
    else:
        high_price = base_price * (1 + magnitude / 2 / 100)
        low_price  = base_price * (1 - magnitude / 100)

    abs_c      = abs(composite)
    high_prob  = round(min(0.30 + abs_c * 0.40, 0.85), 2)
    low_prob   = round(min(0.30 + abs_c * 0.30, 0.75), 2)

    return {
        "high_date":  high_date.strftime("%Y-%m-%d"),
        "high_price": _round_krx(high_price),
        "high_prob":  high_prob,
        "low_date":   low_date.strftime("%Y-%m-%d"),
        "low_price":  _round_krx(low_price),
        "low_prob":   low_prob,
    }


def _add_trading_days(start: datetime, n: int) -> datetime:
    """n 영업일 후 날짜 (토/일 건너뜀)."""
    d = start
    added = 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def _estimate_probabilities(composite: float) -> tuple[float, float]:
    """sigmoid 기반 상승/하락 확률(%)."""
    up = 100 / (1 + math.exp(-composite * 4))
    return round(up, 1), round(100 - up, 1)


def _estimate_confidence(signals: dict, micron_features: dict) -> float:
    """
    신뢰도 점수 (0~100).

    데이터 충분성 40점 + 신호 일치도 30점 + 마이크론 강도 30점.
    None 신호는 데이터 없음으로 처리 (0.0과 구별).
    """
    available  = sum(1 for v in signals.values() if v is not None)
    data_score = available / max(len(signals), 1) * 40

    positive   = sum(1 for v in signals.values() if v is not None and v > 0.05)
    negative   = sum(1 for v in signals.values() if v is not None and v < -0.05)
    consensus  = abs(positive - negative) / max(len(signals), 1)
    cons_score = consensus * 30

    strength  = micron_features.get("micron_session_strength_score")
    str_score = abs(strength - 50) / 50 * 30 if strength is not None else 0.0

    return round(min(data_score + cons_score + str_score, 100.0), 1)
