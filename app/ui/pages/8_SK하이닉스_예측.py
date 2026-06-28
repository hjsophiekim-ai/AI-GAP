"""
8_SK하이닉스_예측.py — SK하이닉스 가격 예측 탭.

마이크론(MU) 프리마켓 데이터를 핵심 선행지표로 사용하여
SK하이닉스(000660)의 오늘/내일/3일 후 가격 흐름과
향후 2주 단기 고점/저점을 예측합니다.

이 페이지는 예측/분석 전용이며 실전 매매 기능과 연결되지 않습니다.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import json

import pandas as pd
import streamlit as st

# ── 모듈 임포트 (graceful fallback) ──────────────────────────────────────────

try:
    from app.data_sources.kis_overseas_minute import collect_and_save_mu, fetch_mu_current_price
    _data_ok = True
except Exception as _data_err:
    _data_ok = False
    _data_err_msg = str(_data_err)

try:
    from app.features.micron_premarket_features import compute_micron_features
    _feat_ok = True
except Exception as _feat_err:
    _feat_ok = False

try:
    from app.models.hynix_predictor import predict_hynix
    _pred_ok = True
except Exception as _pred_err:
    _pred_ok = False

try:
    from app.storage.prediction_logger import log_prediction, load_predictions
    _log_ok = True
except Exception as _log_err:
    _log_ok = False

try:
    from app.models.hynix_error_analyzer import analyze_prediction_error
    _err_ok = True
except Exception:
    _err_ok = False

try:
    from app.models.hynix_weight_adjuster import (
        load_weights,
        save_weights,
        adjust_weights_from_predictions,
    )
    _adj_ok = True
except Exception:
    _adj_ok = False

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_MICRON_1MIN = _ROOT / "data" / "micron" / "MU_1min.csv"
_MICRON_3MIN = _ROOT / "data" / "micron" / "MU_3min.csv"

# ─────────────────────────────────────────────────────────────────────────────
# 페이지 설정
# ─────────────────────────────────────────────────────────────────────────────

st.title("SK하이닉스 예측")
st.caption("마이크론(MU) 프리마켓을 선행지표로 SK하이닉스(000660) 가격 흐름을 예측합니다.")

if not (_data_ok and _feat_ok and _pred_ok):
    st.error("필수 모듈 로드 실패. 서버 로그를 확인하세요.")

st.info(
    "⚠️ 이 화면은 **투자 참고용**입니다. 예측값은 확률적 추정치이며, "
    "실제 매매 손익에 대한 책임은 전적으로 사용자에게 있습니다.",
    icon="⚠️",
)
st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# 사이드바: KIS 모드 선택
# ─────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.subheader("데이터 수집 설정")
    api_mode = st.selectbox(
        "KIS API 모드",
        options=["real", "mock"],
        index=0,
        help="해외주식 분봉은 real 키를 권장합니다.",
    )

# ─────────────────────────────────────────────────────────────────────────────
# Section 1: MU 현재가 & 분봉 수집
# ─────────────────────────────────────────────────────────────────────────────

st.subheader("1. 마이크론(MU) 데이터")

col_fetch, col_mu_price = st.columns([1, 3])

with col_fetch:
    fetch_clicked = st.button("MU 데이터 수집", use_container_width=True)

if fetch_clicked:
    if not _data_ok:
        st.error(f"데이터 수집 모듈 로드 실패: {_data_err_msg}")
    else:
        with st.spinner("MU 데이터 수집 중..."):
            result = collect_and_save_mu(mode=api_mode)
        if result.get("error"):
            st.warning(f"수집 주의: {result['error']}")
        else:
            st.success("MU 데이터 수집 완료")
        st.session_state["mu_collect_result"] = result

# MU 현재가 표시
with col_mu_price:
    mu_result = st.session_state.get("mu_collect_result", {})
    cp = mu_result.get("current_price")
    if cp:
        st.metric(
            label="MU 현재가 (USD)",
            value=f"${cp['price']:.2f}",
            delta=f"고: ${cp['high']:.2f}  저: ${cp['low']:.2f}",
        )
    else:
        st.metric(label="MU 현재가", value="—")

# 1분봉 차트
df_1min_session = mu_result.get("df_1min")
if df_1min_session is None and _MICRON_1MIN.exists():
    try:
        df_1min_session = pd.read_csv(_MICRON_1MIN, parse_dates=["datetime"])
    except Exception:
        df_1min_session = None

tab1min, tab3min = st.tabs(["1분봉", "3분봉"])

with tab1min:
    if df_1min_session is not None and not df_1min_session.empty:
        st.line_chart(df_1min_session.set_index("datetime")[["close"]])
        with st.expander("1분봉 데이터 보기"):
            st.dataframe(df_1min_session.tail(30), use_container_width=True)
    else:
        st.info("MU 1분봉 데이터 없음 — 위 버튼으로 수집하세요.")

with tab3min:
    df_3min_session = mu_result.get("df_3min")
    if df_3min_session is None and _MICRON_3MIN.exists():
        try:
            df_3min_session = pd.read_csv(_MICRON_3MIN, parse_dates=["datetime"])
        except Exception:
            df_3min_session = None
    if df_3min_session is not None and not df_3min_session.empty:
        st.line_chart(df_3min_session.set_index("datetime")[["close"]])
    else:
        st.info("MU 3분봉 데이터 없음")

st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# Section 2: 수동 입력 지표
# ─────────────────────────────────────────────────────────────────────────────

st.subheader("2. 보조 지표 입력 (수동)")

c1, c2 = st.columns(2)
with c1:
    kospilab_price = st.number_input(
        "코스피랩 예상가격 (원)",
        min_value=0.0, value=0.0, step=1000.0,
        help="코스피랩에서 제공하는 SK하이닉스 예상 시가",
    )
    kospilab_return = st.number_input(
        "코스피랩 예상등락률 (%)",
        min_value=-20.0, max_value=20.0, value=0.0, step=0.01,
    )
    hynix_prev_close = st.number_input(
        "SK하이닉스 전일 종가 (원)",
        min_value=0.0, value=0.0, step=500.0,
    )
    hynix_prev_return = st.number_input(
        "SK하이닉스 전일 등락률 (%)",
        min_value=-30.0, max_value=30.0, value=0.0, step=0.01,
    )

with c2:
    sox_return = st.number_input(
        "SOX 지수 등락률 (%)",
        min_value=-15.0, max_value=15.0, value=0.0, step=0.01,
    )
    nvda_return = st.number_input(
        "NVDA 등락률 (%)",
        min_value=-20.0, max_value=20.0, value=0.0, step=0.01,
    )
    qqq_return = st.number_input(
        "QQQ/나스닥 선물 등락률 (%)",
        min_value=-15.0, max_value=15.0, value=0.0, step=0.01,
    )
    usd_krw_change = st.number_input(
        "USD/KRW 환율 변화율 (%)",
        min_value=-5.0, max_value=5.0, value=0.0, step=0.01,
    )

with st.expander("SK하이닉스 최근 수익률 입력 (선택)"):
    hynix_3d  = st.number_input("최근 3일 수익률 (%)", value=0.0, step=0.01)
    hynix_5d  = st.number_input("최근 5일 수익률 (%)", value=0.0, step=0.01)
    hynix_10d = st.number_input("최근 10일 수익률 (%)", value=0.0, step=0.01)
    hynix_vol = st.number_input("거래량 변화율 (%)", value=0.0, step=0.1)

st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# Section 3: 예측 실행
# ─────────────────────────────────────────────────────────────────────────────

st.subheader("3. 예측 실행")

predict_clicked = st.button("예측 실행", type="primary", use_container_width=True)

if predict_clicked:
    if not (_feat_ok and _pred_ok):
        st.error("예측 모듈 로드 실패")
    else:
        # feature 계산
        mu_features = compute_micron_features(
            df_1min=df_1min_session,
            current_price=cp,
        )
        # 예측 실행
        pred = predict_hynix(
            micron_features=mu_features,
            kospilab_expected_price=kospilab_price if kospilab_price > 0 else None,
            kospilab_expected_return_pct=kospilab_return if kospilab_return != 0 else None,
            sox_return_pct=sox_return if sox_return != 0 else None,
            nvda_return_pct=nvda_return if nvda_return != 0 else None,
            qqq_return_pct=qqq_return if qqq_return != 0 else None,
            usd_krw_change_pct=usd_krw_change if usd_krw_change != 0 else None,
            hynix_prev_close=hynix_prev_close if hynix_prev_close > 0 else None,
            hynix_prev_return_pct=hynix_prev_return if hynix_prev_return != 0 else None,
            hynix_return_3d_pct=hynix_3d if hynix_3d != 0 else None,
            hynix_return_5d_pct=hynix_5d if hynix_5d != 0 else None,
            hynix_return_10d_pct=hynix_10d if hynix_10d != 0 else None,
            hynix_volume_change_pct=hynix_vol if hynix_vol != 0 else None,
        )
        st.session_state["hynix_pred"] = pred
        st.session_state["mu_features"] = mu_features

        # 로그 저장
        if _log_ok:
            try:
                log_prediction(
                    prediction=pred,
                    micron_features=mu_features,
                    micron_current_price=cp,
                    kospilab_inputs={
                        "kospilab_expected_price": kospilab_price,
                        "kospilab_expected_return_pct": kospilab_return,
                    },
                    other_inputs={
                        "sox_return_pct": sox_return,
                        "nvda_return_pct": nvda_return,
                        "qqq_return_pct": qqq_return,
                        "usd_krw_change_pct": usd_krw_change,
                        "hynix_prev_return_pct": hynix_prev_return,
                    },
                )
            except Exception as log_exc:
                st.warning(f"예측 로그 저장 실패: {log_exc}")

        st.success("예측 완료!")

# ─────────────────────────────────────────────────────────────────────────────
# Section 4: 예측 결과 표시
# ─────────────────────────────────────────────────────────────────────────────

pred = st.session_state.get("hynix_pred")
mu_features = st.session_state.get("mu_features", {})

if pred:
    st.divider()
    st.subheader("4. 예측 결과")

    # ── 오늘 예상 흐름 ───────────────────────────────────────────────────────
    st.markdown("#### 오늘 예상 흐름")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        _fmt = lambda v: f"{v:,}원" if v else "—"
        st.metric("예상 시가", _fmt(pred.get("today_open_expected")))
    with c2:
        st.metric("예상 고가", _fmt(pred.get("today_high_expected")))
    with c3:
        st.metric("예상 저가", _fmt(pred.get("today_low_expected")))
    with c4:
        ret = pred.get("today_return_pct", 0)
        color = "normal" if ret == 0 else ("inverse" if ret < 0 else "normal")
        st.metric(
            "예상 종가 (등락률)",
            _fmt(pred.get("today_close_expected")),
            delta=f"{ret:+.2f}%",
        )

    # ── 내일 / 3일 후 ────────────────────────────────────────────────────────
    st.markdown("#### 단기 예상 흐름")
    ca, cb = st.columns(2)
    with ca:
        t_ret = pred.get("tomorrow_return_pct", 0)
        st.metric("내일 예상 등락률", f"{t_ret:+.2f}%")
    with cb:
        d3_ret = pred.get("day3_return_pct", 0)
        st.metric("3일 후 예상 등락률", f"{d3_ret:+.2f}%")

    # ── 향후 2주 ─────────────────────────────────────────────────────────────
    st.markdown("#### 향후 2주 예상")
    cw1, cw2 = st.columns(2)
    with cw1:
        st.markdown(
            f"""
**향후 2주 최고점**
- 예상일: {pred.get('two_week_high_date', '—')}
- 예상가: {f"{pred.get('two_week_high_price', 0):,}원" if pred.get('two_week_high_price') else '—'}
- 확률: {f"{pred.get('two_week_high_prob', 0)*100:.0f}%" if pred.get('two_week_high_prob') else '—'}
"""
        )
    with cw2:
        st.markdown(
            f"""
**향후 2주 최저점**
- 예상일: {pred.get('two_week_low_date', '—')}
- 예상가: {f"{pred.get('two_week_low_price', 0):,}원" if pred.get('two_week_low_price') else '—'}
- 확률: {f"{pred.get('two_week_low_prob', 0)*100:.0f}%" if pred.get('two_week_low_prob') else '—'}
"""
        )

    # ── 상승/하락 확률 & 신뢰도 ──────────────────────────────────────────────
    st.markdown("#### 상승/하락 확률 & 신뢰도")
    cp1, cp2, cp3 = st.columns(3)
    with cp1:
        up = pred.get("up_probability", 50)
        st.metric("상승 확률", f"{up:.1f}%")
    with cp2:
        dn = pred.get("down_probability", 50)
        st.metric("하락 확률", f"{dn:.1f}%")
    with cp3:
        conf = pred.get("confidence_score", 0)
        st.metric("신뢰도 점수", f"{conf:.1f}/100")

    # ── 마이크론 feature 상세 ────────────────────────────────────────────────
    with st.expander("마이크론 프리마켓 Feature 상세"):
        feat_display = {
            k: (f"{v:.4f}" if v is not None else "—")
            for k, v in mu_features.items()
        }
        st.json(feat_display)

    with st.expander("예측 신호 상세"):
        st.json(pred.get("signals", {}))

st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# Section 5: 예측 이력 & 적중률
# ─────────────────────────────────────────────────────────────────────────────

st.subheader("5. 예측 이력")

if _log_ok:
    try:
        history = load_predictions()
        if history:
            df_hist = pd.DataFrame(history)
            # 방향 적중률 계산 (actual 데이터 있는 행만)
            completed = df_hist[
                df_hist["actual_close"].apply(lambda x: bool(x and str(x).strip()))
            ]
            if not completed.empty:
                try:
                    correct = sum(
                        (float(r["today_return_pct"]) >= 0) == (float(r["actual_close"]) >= float(r.get("actual_open") or r["actual_close"]))
                        for _, r in completed.iterrows()
                        if r["today_return_pct"] and r["actual_close"]
                    )
                    accuracy = correct / len(completed) * 100
                    st.metric("최근 예측 방향 적중률", f"{accuracy:.1f}%", delta=f"{len(completed)}건 분석")
                except Exception:
                    pass

            st.dataframe(
                df_hist[["predicted_at", "today_return_pct", "confidence_score",
                          "actual_close", "actual_open"]].tail(20),
                use_container_width=True,
            )
        else:
            st.info("예측 이력 없음")
    except Exception as e:
        st.warning(f"이력 로드 실패: {e}")
else:
    st.warning("예측 로거 모듈 로드 실패")

st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# Section 6: 현재 모델 가중치 & 자동 조정
# ─────────────────────────────────────────────────────────────────────────────

st.subheader("6. 모델 가중치")

if _adj_ok:
    weights = load_weights()
    wdf = pd.DataFrame(
        [{"지표": k, "가중치": f"{v*100:.1f}%"} for k, v in weights.items()]
    )
    st.dataframe(wdf, use_container_width=True, hide_index=True)

    if st.button("가중치 자동 조정 실행"):
        if _log_ok:
            try:
                history = load_predictions()
                adj_result = adjust_weights_from_predictions(history)
                new_w = adj_result["new_weights"]
                save_weights(new_w, reason=adj_result["reason"])
                st.success(f"가중치 조정 완료: {adj_result['reason']}")
                changes = {k: f"{v*100:+.2f}%p" for k, v in adj_result.get("changes", {}).items() if v != 0}
                if changes:
                    st.json(changes)
                else:
                    st.info("변경 없음")
            except Exception as exc:
                st.error(f"가중치 조정 실패: {exc}")
        else:
            st.error("예측 로거 모듈 필요")
else:
    st.warning("가중치 조정 모듈 로드 실패")

st.divider()
st.caption(
    "⚠️ 이 화면의 모든 예측 정보는 투자 참고용이며, "
    "실제 매매 손익에 대한 책임은 전적으로 사용자에게 있습니다."
)
