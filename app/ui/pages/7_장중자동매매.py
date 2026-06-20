"""
7_장중자동매매.py — 완전 자동 장중매매 (Sector Leader Auto Intraday Trader)
"""
import sys
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import json
from datetime import datetime

import streamlit as st

try:
    from app.config import get_config
    from app.trading.broker_factory import create_broker
    from app.services.intraday_budget_allocator import IntradayBudgetAllocator
    from app.services.intraday_auto_trade_service import IntradayAutoTradeService
except Exception as e:
    st.error(f"모듈 로드 오류: {e}")
    st.stop()

cfg = get_config()

st.title("완전 자동 장중매매 — 주도섹터 Top3")
st.caption("1분봉/3분봉 기반 자동매수·매도·재진입 | 상태머신 관리")

st.info(
    "**전략:** 주도섹터 Top3 종목을 선정 후, VWAP·EMA·RSI 기반으로 장중 자동매수/매도를 수행합니다.  \n"
    "- 매수: VWAP 위, EMA5>EMA20, 눌림목(-1.2%~-3.8%), 양봉 반전, 거래량 확인  \n"
    "- 매도: 손절(-1.2%), 절반익절(+1.8%), 전량익절(+3.2%), VWAP이탈, EMA데드크로스  \n"
    "- 하루 최대 3회 진입, 종목당 최대 2회, 쿨다운 10분  \n"
    "- **15:10 이후 강제 전량 청산**"
)

# 실전모드 경고
runtime_real = st.session_state.get("runtime_real_mode", False)
if runtime_real:
    st.error("⚠️ 실전모드 ON — 실제 주문이 발생할 수 있습니다. 안전장치를 확인하세요.")

st.divider()

# ── 예산 설정 ───────────────────────────────────────────────────────────────
col_budget, col_mode = st.columns([3, 1])
with col_budget:
    total_budget = st.number_input(
        "총 예산 (원)",
        min_value=1_000_000,
        max_value=100_000_000,
        value=int(cfg.trading.get("total_budget", 10_000_000)),
        step=1_000_000,
        format="%d",
    )
with col_mode:
    st.markdown("**현재 모드**")
    st.markdown(f"`{cfg.mode.upper()}`")

st.divider()

# ── Top3 종목 불러오기 ──────────────────────────────────────────────────────
if st.button("📋 Top3 종목 불러오기", use_container_width=True):
    top3 = st.session_state.get("sector_leader_top3", [])
    if not top3:
        st.warning("주도섹터 Top3 데이터 없음. 먼저 '주도섹터 Top3' 페이지에서 종목을 선정하세요.")
    else:
        allocator = IntradayBudgetAllocator()
        allocated = allocator.allocate(top3, float(total_budget))
        st.session_state["intraday_allocated_top3"] = allocated
        st.success(f"Top3 종목 불러오기 완료: {len(allocated)}개")

# ── 배분 결과 표시 ──────────────────────────────────────────────────────────
if st.session_state.get("intraday_allocated_top3"):
    allocated = st.session_state["intraday_allocated_top3"]
    st.subheader("종목별 예산 배분")
    rows = []
    for s in allocated:
        rows.append({
            "순위": s.get("rank", ""),
            "종목코드": s.get("symbol", ""),
            "종목명": s.get("name", ""),
            "현재가": f"{int(s.get('current_price', 0)):,}",
            "배분비중": f"{s.get('allocated_weight', 0)*100:.1f}%",
            "배분예산": f"{int(s.get('allocated_budget', 0)):,}",
            "배분수량": s.get("allocated_quantity", 0),
            "최종점수": round(s.get("final_score", 0), 2),
        })
    try:
        import pandas as pd
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    except ImportError:
        st.write(rows)

st.divider()

# ── 자동매매 ON/OFF ─────────────────────────────────────────────────────────
st.subheader("자동매매 제어")
running = st.session_state.get("intraday_auto_trade_running", False)

col_on, col_off = st.columns(2)
with col_on:
    if st.button("▶ 자동매매 ON", type="primary", use_container_width=True, disabled=running):
        if not st.session_state.get("intraday_allocated_top3"):
            st.error("먼저 Top3 종목을 불러오세요.")
        else:
            st.session_state["intraday_auto_trade_running"] = True
            st.rerun()
with col_off:
    if st.button("⏹ 자동매매 OFF", use_container_width=True, disabled=not running):
        st.session_state["intraday_auto_trade_running"] = False
        st.rerun()

if running:
    st.success("🟢 자동매매 실행 중")
else:
    st.info("⚫ 자동매매 대기 중")

st.divider()

# ── 상태 표시 ───────────────────────────────────────────────────────────────
if st.session_state.get("intraday_last_result"):
    result = st.session_state["intraday_last_result"]
    st.subheader("마지막 실행 결과")
    st.caption(f"실행 시각: {result.get('checked_at', '')}")

    sym_status = result.get("symbols", {})
    if sym_status:
        cols = st.columns(len(sym_status))
        for i, (sym, status) in enumerate(sym_status.items()):
            color = {"HOLDING": "🟢", "HALF_SOLD": "🟡", "WAITING_ENTRY": "⚪",
                     "COOLING_DOWN": "🔵", "DONE": "✅", "ERROR": "🔴"}.get(status, "⚫")
            cols[i].metric(sym, f"{color} {status}")

    actions = result.get("actions", [])
    if actions:
        st.write("**실행된 액션:**")
        st.json(actions)
    else:
        st.write("이번 실행에서 실행된 액션 없음.")

st.divider()

# ── 1회 실행 ────────────────────────────────────────────────────────────────
st.subheader("수동 실행")
if st.button("🔄 1회 실행 (run_once)", use_container_width=True):
    allocated = st.session_state.get("intraday_allocated_top3", [])
    if not allocated:
        st.warning("Top3 종목을 먼저 불러오세요.")
    else:
        with st.spinner("실행 중..."):
            try:
                broker = create_broker(
                    cfg=cfg,
                    mode=cfg.mode,
                    runtime_real_mode=runtime_real,
                    runtime_enable_real_buy=st.session_state.get("runtime_enable_real_buy", False),
                    runtime_enable_real_sell=st.session_state.get("runtime_enable_real_sell", False),
                )
                service = IntradayAutoTradeService(broker=broker, kis_client=None, cfg=cfg)
                service.load_top3(allocated)
                result = service.run_once()
                st.session_state["intraday_last_result"] = result
                st.success("run_once 완료")
                st.json(result)
            except Exception as ex:
                st.error(f"실행 오류: {ex}")

st.divider()
st.page_link(
    "pages/6_주도섹터_Top3.py",
    label="← 주도섹터 Top3 선정으로 이동",
    icon="🎯",
)
