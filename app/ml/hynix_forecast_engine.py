"""
hynix_forecast_engine.py — SK하이닉스 예측 파이프라인 + 데이터 수집률 게이트.

데이터 품질 점수 기반으로 예측 실행 여부를 결정합니다:
  - 70% 이상 : 정상 예측
  - 40~70%  : 낮은 신뢰도 예측 (경고 표시)
  - 40% 미만 : 예측 차단 (데이터 부족)

실전 주문 기능과 절대 연결하지 않습니다.
"""

from __future__ import annotations

from typing import Optional

BLOCK_THRESHOLD   = 0.40  # 이하면 예측 차단
LOW_CONF_THRESHOLD = 0.70  # 이하면 낮은 신뢰도 경고


def run_forecast(market_data: dict) -> dict:
    """
    시장 데이터를 받아 전체 예측 파이프라인을 실행합니다.

    Parameters
    ----------
    market_data : collect_all() 반환 dict

    Returns
    -------
    dict
        status         : "ok" | "low_confidence" | "blocked"
        data_quality   : float (0~1)
        message        : str  — 한글 상태 메시지
        auto_features  : dict | None
        prediction     : dict | None
        swing          : dict | None
        explanation    : str | None
        errors         : list[str]
    """
    result: dict = {
        "status":        "blocked",
        "data_quality":  0.0,
        "message":       "데이터 수집 전",
        "auto_features": None,
        "prediction":    None,
        "swing":         None,
        "explanation":   None,
        "errors":        list(market_data.get("errors", [])),
    }

    # ── 1. Auto feature 계산 ──────────────────────────────────────────────────
    try:
        from app.features.hynix_auto_features import build_auto_features
        auto_feat = build_auto_features(market_data)
        result["auto_features"] = auto_feat
        dq = float(auto_feat.get("data_quality", 0.0))
        result["data_quality"] = dq
    except Exception as e:
        result["message"] = f"Feature 계산 실패: {e}"
        result["errors"].append(str(e))
        return result

    # ── 2. 수집률 게이트 ──────────────────────────────────────────────────────
    if dq < BLOCK_THRESHOLD:
        result["status"] = "blocked"
        result["message"] = (
            f"데이터 수집률 {dq * 100:.0f}% — 신뢰도 부족으로 예측을 생략합니다. "
            f"(정상 예측 최소 기준: {BLOCK_THRESHOLD * 100:.0f}%)\n"
            f"MU 프리마켓 또는 SK하이닉스 일봉 데이터를 확인하세요."
        )
        return result

    # ── 3. 가격 예측 ─────────────────────────────────────────────────────────
    try:
        from app.models.hynix_predictor import predict_hynix
        pred = predict_hynix(
            micron_features=auto_feat["micron_features"],
            **auto_feat["predictor_kwargs"],
        )
        result["prediction"] = pred
    except Exception as e:
        result["message"] = f"예측 연산 실패: {e}"
        result["errors"].append(str(e))
        result["status"] = "blocked"
        return result

    # ── 4. 스윙 플래그 ────────────────────────────────────────────────────────
    try:
        from app.models.hynix_swing_flag import evaluate_swing_flag
        swing = evaluate_swing_flag(
            micron_features=auto_feat["micron_features"],
            prediction=pred,
            **auto_feat["swing_kwargs"],
        )
        result["swing"] = swing
    except Exception as e:
        result["errors"].append(f"스윙 플래그 오류: {e}")

    # ── 5. 한글 설명 ──────────────────────────────────────────────────────────
    try:
        from app.models.hynix_swing_explainer import generate_swing_explanation
        if result["swing"]:
            expl = generate_swing_explanation(
                swing_result=result["swing"],
                micron_features=auto_feat["micron_features"],
                tech_indicators=auto_feat.get("tech_indicators"),
                kospilab_return=auto_feat.get("kospilab_return"),
            )
            result["explanation"] = expl
    except Exception as e:
        result["errors"].append(f"설명 생성 오류: {e}")

    # ── 6. 최종 상태 결정 ─────────────────────────────────────────────────────
    if dq < LOW_CONF_THRESHOLD:
        result["status"] = "low_confidence"
        result["message"] = (
            f"데이터 수집률 {dq * 100:.0f}% — 일부 데이터가 누락되어 예측 신뢰도가 낮습니다. "
            f"참고용으로만 활용하세요."
        )
    else:
        result["status"] = "ok"
        result["message"] = f"데이터 수집률 {dq * 100:.0f}% — 정상 예측"

    return result


def collection_rate_label(data_quality: float) -> tuple[str, str]:
    """
    데이터 품질 점수에서 (레이블, 색상) 반환.

    Returns
    -------
    tuple[str, str]
        label : "정상" | "낮은 신뢰도" | "수집 부족"
        color : CSS 색상 문자열
    """
    if data_quality >= LOW_CONF_THRESHOLD:
        return "정상", "#2ecc71"
    if data_quality >= BLOCK_THRESHOLD:
        return "낮은 신뢰도", "#e67e22"
    return "수집 부족", "#e74c3c"
