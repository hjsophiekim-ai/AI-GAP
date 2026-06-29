"""
hynix_forecast_engine.py — SK하이닉스 예측 파이프라인 + 데이터 수집률 게이트.

데이터 품질 점수 기반으로 예측 실행 여부를 결정합니다:
  - 70% 이상 : 정상 예측
  - 40~70%  : 낮은 신뢰도 예측 (경고 표시)
  - 40% 미만 : 예측 차단 (데이터 부족)

신뢰도 게이트:
  - confidence_score < 40 : 매수/매도 추천 대신 "데이터 부족/저신뢰" 표시

실전 주문 기능과 절대 연결하지 않습니다.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

BLOCK_THRESHOLD   = 0.40  # 이하면 예측 차단
LOW_CONF_THRESHOLD = 0.70  # 이하면 낮은 신뢰도 경고
CONFIDENCE_GATE   = 40.0  # 이하면 매수/매도 추천 금지

# ── 디버그 로거 설정 ──────────────────────────────────────────────────────────

_ROOT = Path(__file__).resolve().parent.parent.parent
_LOG_DIR = _ROOT / "logs"

def _get_debug_logger() -> logging.Logger:
    """hynix_prediction_debug.log 파일 로거."""
    logger = logging.getLogger("hynix_prediction_debug")
    if not logger.handlers:
        try:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(
                _LOG_DIR / "hynix_prediction_debug.log",
                encoding="utf-8",
            )
            fh.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            logger.addHandler(fh)
            logger.setLevel(logging.DEBUG)
        except Exception:
            logger.addHandler(logging.NullHandler())
    return logger


# ── 최소 예측 조건 검증 ───────────────────────────────────────────────────────

def _check_minimum_conditions(auto_feat: dict) -> tuple[bool, str]:
    """
    예측에 필요한 최소 데이터 조건을 검증합니다.

    조건:
      1) SK하이닉스 전일 종가 필수
      2) MU 프리마켓 또는 코스피랩 데이터 중 하나 이상 필수
      3) 외부 지표 (SOX/NVDA/QQQ/USD·KRW) 중 2개 이상 필수
    """
    pf = auto_feat.get("predictor_kwargs", {})
    mf = auto_feat.get("micron_features", {})

    # 조건 1: 하이닉스 전일 종가
    if not pf.get("hynix_prev_close"):
        return False, "SK하이닉스 전일 종가 없음 — 예측 불가"

    # 조건 2: MU 또는 코스피랩
    has_mu       = mf.get("micron_session_strength_score") is not None
    has_kospilab = pf.get("kospilab_expected_return_pct") is not None
    if not has_mu and not has_kospilab:
        return False, "MU 프리마켓 데이터 및 코스피랩 데이터 모두 없음 — 예측 불가"

    # 조건 3: 외부 지표 2개 이상
    ext_vals = [
        pf.get("sox_return_pct"),
        pf.get("nvda_return_pct"),
        pf.get("qqq_return_pct"),
        pf.get("usd_krw_change_pct"),
    ]
    ext_count = sum(1 for v in ext_vals if v is not None)
    if ext_count < 2:
        return (
            False,
            f"외부 지표 {ext_count}개 수집 — 최소 2개 필요 "
            "(SOX·NVDA·QQQ·USD/KRW 중 2개 이상)",
        )

    return True, "ok"


# ── 예측 파이프라인 ───────────────────────────────────────────────────────────

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
        confidence_blocked : bool — confidence_score < 40 시 True
        message        : str  — 한글 상태 메시지
        auto_features  : dict | None
        prediction     : dict | None
        swing          : dict | None
        explanation    : str | None
        errors         : list[str]
        diagnostics    : dict — 각 소스별 수집 현황
    """
    log = _get_debug_logger()

    result: dict = {
        "status":            "blocked",
        "data_quality":      0.0,
        "confidence_blocked": False,
        "message":           "데이터 수집 전",
        "auto_features":     None,
        "prediction":        None,
        "swing":             None,
        "explanation":       None,
        "errors":            list(market_data.get("errors", [])),
        "diagnostics":       _build_diagnostics(market_data),
    }

    log.info("=== 예측 파이프라인 시작 ===")
    log.debug("market_data sources: mu=%s, nvda=%s, index=%s, hynix=%s, kospilab=%s",
              market_data.get("mu", {}).get("source"),
              market_data.get("nvda", {}).get("source"),
              market_data.get("index", {}).get("source"),
              market_data.get("hynix", {}).get("source"),
              market_data.get("kospilab", {}).get("source_status"))

    # ── 1. Auto feature 계산 ──────────────────────────────────────────────────
    try:
        from app.features.hynix_auto_features import build_auto_features
        auto_feat = build_auto_features(market_data)
        result["auto_features"] = auto_feat
        dq = float(auto_feat.get("data_quality", 0.0))
        result["data_quality"] = dq
        log.info("데이터 품질 점수: %.2f (%.0f%%)", dq, dq * 100)
    except Exception as e:
        result["message"] = f"Feature 계산 실패: {e}"
        result["errors"].append(str(e))
        log.error("Feature 계산 실패: %s", e)
        return result

    # ── 2. 최소 조건 검증 ──────────────────────────────────────────────────────
    min_ok, min_msg = _check_minimum_conditions(auto_feat)
    if not min_ok:
        result["status"] = "blocked"
        result["message"] = f"최소 예측 조건 미충족: {min_msg}"
        log.warning("최소 조건 미충족: %s", min_msg)
        return result

    # ── 3. 수집률 게이트 ──────────────────────────────────────────────────────
    if dq < BLOCK_THRESHOLD:
        result["status"] = "blocked"
        result["message"] = (
            f"데이터 수집률 {dq * 100:.0f}% — 신뢰도 부족으로 예측을 생략합니다. "
            f"(정상 예측 최소 기준: {BLOCK_THRESHOLD * 100:.0f}%)\n"
            "MU 프리마켓 또는 SK하이닉스 일봉 데이터를 확인하세요."
        )
        log.warning("수집률 게이트 차단: %.0f%%", dq * 100)
        return result

    # ── 4. 가격 예측 ─────────────────────────────────────────────────────────
    try:
        from app.models.hynix_predictor import predict_hynix
        pred = predict_hynix(
            micron_features=auto_feat["micron_features"],
            **auto_feat["predictor_kwargs"],
        )
        result["prediction"] = pred
        log.info("가격 예측 완료: 오늘등락률=%.2f%%, 신뢰도=%.1f",
                 pred.get("today_return_pct", 0), pred.get("confidence_score", 0))
    except Exception as e:
        result["message"] = f"예측 연산 실패: {e}"
        result["errors"].append(str(e))
        result["status"] = "blocked"
        log.error("가격 예측 실패: %s", e)
        return result

    # ── 5. 스윙 플래그 ────────────────────────────────────────────────────────
    try:
        from app.models.hynix_swing_flag import evaluate_swing_flag
        swing = evaluate_swing_flag(
            micron_features=auto_feat["micron_features"],
            prediction=pred,
            **auto_feat["swing_kwargs"],
        )
        result["swing"] = swing
        log.info("스윙 플래그: %s (score=%.1f, confidence=%.1f)",
                 swing.get("swing_flag"), swing.get("swing_score", 0),
                 swing.get("confidence_score", 0))

        # 가격 구간 논리 검증
        try:
            from app.data.market_data_validator import validate_swing_result
            zone_ok, zone_msg = validate_swing_result(swing)
            if not zone_ok:
                result["errors"].append(f"가격 구간 오류: {zone_msg}")
                log.error("가격 구간 논리 오류: %s", zone_msg)
        except ImportError:
            pass
    except Exception as e:
        result["errors"].append(f"스윙 플래그 오류: {e}")
        log.error("스윙 플래그 실패: %s", e)

    # ── 6. 신뢰도 게이트 (confidence < 40 → 추천 차단) ──────────────────────
    swing_cf = (result["swing"] or {}).get("confidence_score", 0.0)
    if swing_cf < CONFIDENCE_GATE:
        result["confidence_blocked"] = True
        log.warning("신뢰도 게이트 차단: confidence=%.1f < %.0f", swing_cf, CONFIDENCE_GATE)

    # ── 7. 한글 설명 ──────────────────────────────────────────────────────────
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
        log.warning("설명 생성 실패: %s", e)

    # ── 8. 최종 상태 결정 ─────────────────────────────────────────────────────
    if dq < LOW_CONF_THRESHOLD:
        result["status"] = "low_confidence"
        result["message"] = (
            f"데이터 수집률 {dq * 100:.0f}% — 일부 데이터가 누락되어 예측 신뢰도가 낮습니다. "
            "참고용으로만 활용하세요."
        )
    else:
        result["status"] = "ok"
        result["message"] = f"데이터 수집률 {dq * 100:.0f}% — 정상 예측"

    log.info("예측 완료: status=%s, confidence_blocked=%s",
             result["status"], result["confidence_blocked"])
    log.info("=== 예측 파이프라인 종료 ===")
    return result


# ── 진단 정보 빌더 ────────────────────────────────────────────────────────────

def _build_diagnostics(market_data: dict) -> dict:
    """각 데이터 소스별 수집 현황 요약."""
    mu     = market_data.get("mu", {})
    nvda   = market_data.get("nvda", {})
    idx    = market_data.get("index", {})
    hynix  = market_data.get("hynix", {})
    klab   = market_data.get("kospilab", {})

    mu_ok      = mu.get("source") is not None and mu.get("current_price") is not None
    nvda_ok    = nvda.get("source") is not None and nvda.get("current_price") is not None
    sox_ok     = idx.get("sox_return") is not None
    qqq_ok     = idx.get("qqq_return") is not None
    usdkrw_ok  = idx.get("usdkrw_change") is not None
    hynix_ok   = hynix.get("df_daily") is not None and hynix.get("prev_close") is not None
    klab_ok    = klab.get("source_status") == "success" and klab.get("hynix_reference_return") is not None

    return {
        "mu":      {"ok": mu_ok,     "source": mu.get("source"), "error": mu.get("error")},
        "nvda":    {"ok": nvda_ok,   "source": nvda.get("source"), "error": nvda.get("error")},
        "sox":     {"ok": sox_ok,    "source": idx.get("source"), "value": idx.get("sox_return")},
        "qqq":     {"ok": qqq_ok,    "source": idx.get("source"), "value": idx.get("qqq_return")},
        "usdkrw":  {"ok": usdkrw_ok, "source": idx.get("source"), "value": idx.get("usdkrw_change")},
        "hynix":   {"ok": hynix_ok,  "source": hynix.get("source"), "error": hynix.get("error"),
                    "prev_close": hynix.get("prev_close")},
        "kospilab": {"ok": klab_ok,  "status": klab.get("source_status"),
                     "error": klab.get("error_message")},
    }


# ── 레이블 ────────────────────────────────────────────────────────────────────

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
