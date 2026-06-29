"""
market_data_validator.py — 시장 데이터 가격 범위·논리 검증.

실전 주문 기능과 절대 연결하지 않습니다.
MU(마이크론) 가격이 1,000USD 이상이면 소수점/환산 오류로 판단합니다.
"""

from __future__ import annotations

from typing import Optional


# ── 유효 가격 범위 상수 ───────────────────────────────────────────────────────

MU_PRICE_MIN: float = 20.0        # USD
MU_PRICE_MAX: float = 500.0       # USD — 이 이상이면 단위 오류 의심
MU_PRICE_HARD_MAX: float = 1000.0  # 이 이상이면 즉시 무효

HYNIX_PRICE_MIN: int = 50_000     # KRW
HYNIX_PRICE_MAX: int = 1_000_000  # KRW


# ── MU 가격 검증 ─────────────────────────────────────────────────────────────

def validate_mu_price(price: Optional[float]) -> tuple[bool, str]:
    """
    MU 가격이 유효 범위(20~500 USD)인지 검증.

    Returns
    -------
    (True, "ok") | (False, 오류메시지)
    """
    if price is None:
        return False, "MU 가격 없음 (None)"
    if price > MU_PRICE_HARD_MAX:
        return False, (
            f"MU 가격 {price:.2f}USD > {MU_PRICE_HARD_MAX:.0f} — "
            "소수점/환산 오류 의심 (예측 금지)"
        )
    if price > MU_PRICE_MAX:
        return False, (
            f"MU 가격 {price:.2f}USD > {MU_PRICE_MAX:.0f} — 비정상 고가"
        )
    if price < MU_PRICE_MIN:
        return False, (
            f"MU 가격 {price:.2f}USD < {MU_PRICE_MIN:.0f} — 비정상 저가"
        )
    return True, "ok"


def auto_fix_mu_price(price: Optional[float]) -> Optional[float]:
    """
    MU 가격 자동 보정.

    - 이미 정상 범위(20~500)이면 그대로 반환.
    - /10, /100 으로 범위 내로 들어오면 보정값 반환.
    - 여전히 범위 밖이면 None 반환.
    """
    if price is None:
        return None
    if MU_PRICE_MIN <= price <= MU_PRICE_MAX:
        return price
    for divisor in (10, 100):
        fixed = price / divisor
        if MU_PRICE_MIN <= fixed <= MU_PRICE_MAX:
            return fixed
    return None


def parse_mu_price_str(raw: object) -> Optional[float]:
    """
    KIS API 응답 문자열에서 MU 가격 파싱.

    - 콤마 제거 후 float 변환.
    - 범위 검증 후 자동 보정 시도.
    """
    if raw is None:
        return None
    try:
        cleaned = str(raw).replace(",", "").strip()
        if not cleaned or cleaned in ("0", "0.0", ""):
            return None
        price = float(cleaned)
        if price <= 0:
            return None
        return auto_fix_mu_price(price)
    except (ValueError, TypeError):
        return None


# ── SK하이닉스 가격 검증 ─────────────────────────────────────────────────────

def validate_hynix_price(price: Optional[float]) -> tuple[bool, str]:
    """
    SK하이닉스 종가가 유효 범위(50,000~1,000,000원)인지 검증.
    """
    if price is None:
        return False, "SK하이닉스 가격 없음 (None)"
    if price < HYNIX_PRICE_MIN:
        return False, (
            f"SK하이닉스 {price:,.0f}원 < {HYNIX_PRICE_MIN:,}원 — 비정상 저가"
        )
    if price > HYNIX_PRICE_MAX:
        return False, (
            f"SK하이닉스 {price:,.0f}원 > {HYNIX_PRICE_MAX:,}원 — 비정상 고가"
        )
    return True, "ok"


def validate_hynix_dataframe(df) -> tuple[bool, str, object]:
    """
    SK하이닉스 일봉 DataFrame 검증.

    - 최소 20개 행
    - close 가격이 유효 범위 내
    - 유효하지 않은 행 필터링 후 반환

    Returns
    -------
    (valid, message, filtered_df_or_original)
    """
    if df is None or df.empty:
        return False, "일봉 데이터 없음", df

    import pandas as pd

    df_work = df.copy()
    if "close" not in df_work.columns:
        return False, "close 컬럼 없음", df

    n_before = len(df_work)
    df_work = df_work[
        df_work["close"].apply(lambda x: HYNIX_PRICE_MIN <= x <= HYNIX_PRICE_MAX)
    ].reset_index(drop=True)
    n_after = len(df_work)

    if n_after < 20:
        return (
            False,
            f"유효 일봉 {n_after}개 < 최소 20개 필요 (검증 전 {n_before}개)",
            df_work,
        )
    return True, f"유효 일봉 {n_after}개", df_work


# ── 가격 구간 논리 검증 ───────────────────────────────────────────────────────

def validate_price_zones(
    target_price: Optional[float],
    stop_loss_price: Optional[float],
) -> tuple[bool, str]:
    """
    목표가 > 손절가 조건 검증.

    Returns
    -------
    (True, "ok") | (False, 오류메시지)
    """
    if target_price is None or stop_loss_price is None:
        return True, "ok (가격 구간 미설정)"
    if stop_loss_price >= target_price:
        return False, (
            f"손절가({stop_loss_price:,.0f}원) ≥ 목표가({target_price:,.0f}원) "
            "— 예측 결과 무효"
        )
    return True, "ok"


def validate_swing_result(swing: dict) -> tuple[bool, str]:
    """
    스윙 플래그 결과의 가격 구간 논리를 종합 검증.
    """
    ok, msg = validate_price_zones(
        swing.get("target_price"),
        swing.get("stop_loss_price"),
    )
    return ok, msg
