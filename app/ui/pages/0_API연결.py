"""
0_API연결.py - KIS API 연결 테스트 및 계좌 확인 페이지
"""
import streamlit as st

try:
    from app.trading.kis_client import create_kis_client
except Exception as e:
    st.error(f"모듈 로드 오류: {e}")

st.title("KIS API 연결")
st.caption("Mock(모의투자) / Real(실전투자) 계좌 연결 상태를 확인합니다.")

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _run_test(mode: str, test_name: str) -> None:
    key_client = f"{mode}_client"
    key_ok = f"{mode}_token_ok"

    if test_name == "token":
        with st.spinner("토큰 발급 중..."):
            try:
                client = create_kis_client(mode)
                if client is None:
                    st.error(f"{mode.upper()} KIS 클라이언트 생성 실패 — .env의 KIS_{mode.upper()}_* 환경변수를 확인하세요.")
                    return
                token = client.get_access_token()
                if token:
                    st.session_state[key_client] = client
                    st.session_state[key_ok] = True
                    st.success(f"토큰 발급 성공 (길이: {len(token)}자)")
                else:
                    st.error("토큰이 비어있습니다.")
            except Exception as exc:
                st.error(f"토큰 발급 실패: {exc}")

    elif test_name == "balance":
        client = st.session_state.get(key_client)
        if client is None:
            st.warning("먼저 토큰을 발급하세요.")
            return
        with st.spinner("계좌 잔고 조회 중..."):
            try:
                balance = client.get_balance()
                if "error" not in balance:
                    cash = balance.get("cash", 0)
                    pos_cnt = len(balance.get("positions", []))
                    st.success(f"예수금: **{cash:,.0f}원** | 보유종목: **{pos_cnt}개**")
                    st.session_state[f"{mode}_balance"] = balance
                else:
                    st.warning(f"잔고 조회 오류: {balance.get('error')} (장외시간일 수 있습니다)")
            except Exception as exc:
                st.warning(f"잔고 조회 실패: {exc}")

    elif test_name == "positions":
        client = st.session_state.get(key_client)
        if client is None:
            st.warning("먼저 토큰을 발급하세요.")
            return
        with st.spinner("보유종목 조회 중..."):
            try:
                balance = client.get_balance()
                if "error" not in balance:
                    positions = balance.get("positions", [])
                    if positions:
                        st.success(f"보유종목 **{len(positions)}개**")
                        import pandas as pd
                        rows = [
                            {
                                "종목코드": p.get("symbol", ""),
                                "종목명": p.get("name", ""),
                                "보유수량": p.get("quantity", 0),
                                "평균단가": f"{p.get('avg_price', 0):,.0f}",
                                "현재가": f"{p.get('current_price', 0):,.0f}",
                            }
                            for p in positions
                        ]
                        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                    else:
                        st.info("보유 종목 없음")
                else:
                    st.warning(f"조회 실패: {balance.get('error')}")
            except Exception as exc:
                st.warning(f"보유종목 조회 실패: {exc}")

    elif test_name == "buyable":
        client = st.session_state.get(key_client)
        if client is None:
            st.warning("먼저 토큰을 발급하세요.")
            return
        with st.spinner("주문가능금액 조회 중..."):
            try:
                buyable = client.get_buyable_cash()
                st.success(f"주문가능금액: **{buyable:,.0f}원**")
                st.session_state[f"{mode}_buyable"] = buyable
            except Exception as exc:
                st.warning(f"주문가능금액 조회 실패: {exc}")


# ---------------------------------------------------------------------------
# tabs
# ---------------------------------------------------------------------------

tab_mock, tab_real = st.tabs(["모의투자 (Mock)", "실전투자 (Real)"])

# ── Mock ──────────────────────────────────────────────────────────────────
with tab_mock:
    st.subheader("Mock 계좌 연결 테스트")
    st.info("KIS 모의투자 계좌 (`openapivts.koreainvestment.com:29443`)에 연결합니다.")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        if st.button("① 토큰 발급", key="mock_token_btn", use_container_width=True, type="primary"):
            _run_test("mock", "token")
    with c2:
        if st.button("② 계좌 잔고", key="mock_balance_btn", use_container_width=True):
            _run_test("mock", "balance")
    with c3:
        if st.button("③ 보유종목", key="mock_positions_btn", use_container_width=True):
            _run_test("mock", "positions")
    with c4:
        if st.button("④ 주문가능금액", key="mock_buyable_btn", use_container_width=True):
            _run_test("mock", "buyable")

    st.divider()

    mock_ok = st.session_state.get("mock_token_ok", False)
    mock_buyable = st.session_state.get("mock_buyable")
    mock_balance = st.session_state.get("mock_balance")

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        if mock_ok:
            st.success("토큰: 발급됨")
        else:
            st.error("토큰: 미발급")
    with col_b:
        if mock_balance and "error" not in mock_balance:
            st.success(f"예수금: {mock_balance.get('cash', 0):,.0f}원")
        else:
            st.warning("예수금: 미조회")
    with col_c:
        if mock_buyable is not None:
            st.success(f"주문가능: {mock_buyable:,.0f}원")
        else:
            st.warning("주문가능금액: 미조회")

    if mock_ok and mock_buyable is not None and mock_buyable > 0:
        st.success("Mock 계좌 주문 가능 상태입니다.")
    elif mock_ok:
        st.warning("토큰은 발급됐지만 주문가능금액을 확인하세요.")

# ── Real ──────────────────────────────────────────────────────────────────
with tab_real:
    st.subheader("Real 계좌 연결 테스트")
    st.error("실전투자 계좌 조회만 테스트합니다. **이 페이지에서 주문은 실행되지 않습니다.**")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        if st.button("① 토큰 발급", key="real_token_btn", use_container_width=True, type="primary"):
            _run_test("real", "token")
    with c2:
        if st.button("② 계좌 잔고", key="real_balance_btn", use_container_width=True):
            _run_test("real", "balance")
    with c3:
        if st.button("③ 보유종목", key="real_positions_btn", use_container_width=True):
            _run_test("real", "positions")
    with c4:
        if st.button("④ 주문가능금액", key="real_buyable_btn", use_container_width=True):
            _run_test("real", "buyable")

    st.divider()

    real_ok = st.session_state.get("real_token_ok", False)
    real_buyable = st.session_state.get("real_buyable")
    real_balance = st.session_state.get("real_balance")

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        if real_ok:
            st.success("토큰: 발급됨")
        else:
            st.error("토큰: 미발급")
    with col_b:
        if real_balance and "error" not in real_balance:
            st.success(f"예수금: {real_balance.get('cash', 0):,.0f}원")
        else:
            st.warning("예수금: 미조회")
    with col_c:
        if real_buyable is not None:
            st.success(f"주문가능: {real_buyable:,.0f}원")
        else:
            st.warning("주문가능금액: 미조회")

    if real_ok and real_buyable is not None and real_buyable > 0:
        st.success("Real 계좌 주문 가능 상태입니다.")
    elif real_ok:
        st.warning("토큰은 발급됐지만 주문가능금액을 확인하세요.")
