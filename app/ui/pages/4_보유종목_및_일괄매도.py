"""
4_보유종목_및_일괄매도.py

보유종목 조회 후 다양한 매도 유형을 지원합니다.
- 수동 일괄매도
- 10:15 일괄매도 (예약)
- 3% 절반매도 / 5% 전체매도
- 2% 하락시 손절
"""
import streamlit as st
import pandas as pd
from datetime import datetime

try:
    from app.trading.broker_factory import create_broker
    from app.trading.order_manager import OrderManager
    from app.trading.sell_manager import SellManager
    from app.config import get_config
    from app.utils.stock_utils import format_amount, format_price, format_rate
except Exception as e:
    st.error(f"모듈 로드 오류: {e}")
    st.stop()

st.title("보유 종목 및 매도")


def _colour_rate(rate: float) -> str:
    text = format_rate(rate)
    colour = "#2ecc71" if rate >= 0 else "#e74c3c"
    return f'<span style="color:{colour};font-weight:bold">{text}</span>'


# ---------------------------------------------------------------------------
# Section 0 — 계좌 모드
# ---------------------------------------------------------------------------
st.subheader("계좌 모드")

selected_mode = st.selectbox(
    "계좌 모드",
    options=["dry_run", "mock", "real"],
    index=0,
    key="sell_page_mode",
)

if selected_mode == "dry_run":
    st.info("드라이런 모드: 가상 포지션이 표시됩니다.")
elif selected_mode == "mock":
    st.warning("모의투자 모드: KIS 모의투자 계좌의 실제 보유종목이 표시됩니다.")
elif selected_mode == "real":
    st.error("실전투자 모드: 실제 KIS 계좌의 보유종목이 표시됩니다!")

confirm_text = ""
if selected_mode == "real":
    try:
        cfg_tmp = get_config()
        expected_text = cfg_tmp.real_confirm_text()
    except Exception:
        expected_text = "I_UNDERSTAND_REAL_TRADING_RISK"
    confirm_text = st.text_input(
        f"실전투자 확인 문구 입력 ('{expected_text}')",
        type="password",
        placeholder=expected_text,
        key="sell_page_confirm",
    )
    if confirm_text and confirm_text != expected_text:
        st.error("확인 문구가 틀립니다. 매도 버튼이 비활성화됩니다.")

if st.session_state.get("_sell_page_last_mode") != selected_mode:
    st.session_state.pop("sell_broker", None)
    st.session_state.pop("positions", None)
    st.session_state["_sell_page_last_mode"] = selected_mode

real_sell_ok = (
    selected_mode != "real"
    or (confirm_text and confirm_text == (
        get_config().real_confirm_text() if get_config else "I_UNDERSTAND_REAL_TRADING_RISK"
    ))
)

st.divider()

# ---------------------------------------------------------------------------
# Section 1 — 보유종목 조회
# ---------------------------------------------------------------------------
st.subheader("보유종목 조회")

if st.button("보유종목 조회", key="btn_fetch_positions", use_container_width=False):
    with st.spinner("보유종목을 조회하는 중..."):
        try:
            cfg = get_config()
            broker = create_broker(cfg=cfg, mode=selected_mode, confirm_text=confirm_text)
            st.session_state["sell_broker"] = broker
            positions = broker.get_positions()
            st.session_state["positions"] = positions
        except RuntimeError as exc:
            st.error(f"브로커 초기화 실패 (안전장치): {exc}")
        except Exception as exc:
            st.error(f"보유종목 조회 실패: {exc}")

positions = st.session_state.get("positions", [])

if positions:
    total_market_value = sum(p.market_value for p in positions)
    total_cost = sum(p.cost for p in positions)
    total_pnl = sum(p.profit_amount for p in positions)
    pnl_rate = (total_pnl / total_cost * 100) if total_cost else 0.0

    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("보유 종목", f"{len(positions)}개")
    col_m2.metric("총 평가금액", format_amount(total_market_value))
    col_m3.metric("총 평가손익", format_amount(total_pnl), delta=format_rate(pnl_rate))
    col_m4.metric("수익률", format_rate(pnl_rate))

    rows_html = "".join(
        f"<tr>"
        f"<td>{p.symbol}</td><td>{p.name}</td>"
        f"<td style='text-align:right'>{p.quantity:,}주</td>"
        f"<td style='text-align:right'>{format_price(p.avg_price)}</td>"
        f"<td style='text-align:right'>{format_price(p.current_price)}</td>"
        f"<td style='text-align:right'>{_colour_rate(p.profit_rate)}</td>"
        f"<td style='text-align:right;color:{'#2ecc71' if p.profit_amount>=0 else '#e74c3c'}'>"
        f"{format_amount(p.profit_amount)}</td>"
        f"</tr>"
        for p in positions
    )
    st.markdown(
        f"<style>.pos-t{{width:100%;border-collapse:collapse;font-size:.9rem}}"
        f".pos-t th{{background:#1e2d3d;color:#fff;padding:8px 12px;text-align:left}}"
        f".pos-t td{{padding:6px 12px;border-bottom:1px solid #2d3f50}}"
        f".pos-t tr:hover td{{background:#1a2535}}</style>"
        f"<table class='pos-t'><thead><tr>"
        f"<th>종목코드</th><th>종목명</th><th>보유수량</th>"
        f"<th>평균단가</th><th>현재가</th><th>수익률(%)</th><th>평가손익(원)</th>"
        f"</tr></thead><tbody>{rows_html}</tbody></table>",
        unsafe_allow_html=True,
    )
elif st.session_state.get("positions") is not None:
    st.info("보유 중인 종목이 없습니다.")

st.divider()

# ---------------------------------------------------------------------------
# Section 2 — 매도 조건 설정
# ---------------------------------------------------------------------------
st.subheader("매도 조건 설정")

col_tp1, col_tp2, col_sl = st.columns(3)
with col_tp1:
    tp1_rate = st.number_input("절반 매도 수익률 (%)", min_value=0.5, max_value=20.0, value=3.0, step=0.5)
with col_tp2:
    tp2_rate = st.number_input("전량 매도 수익률 (%)", min_value=1.0, max_value=30.0, value=5.0, step=0.5)
with col_sl:
    sl_rate = st.number_input("손절(익절) 하락률 (%)", min_value=0.5, max_value=10.0, value=2.0, step=0.5,
                               help="매수 평균단가 대비 이 비율 하락 시 전량 매도")

# 매도 조건 체크 결과 미리보기
if positions:
    half_sell = [p for p in positions if tp1_rate <= p.profit_rate < tp2_rate]
    full_sell = [p for p in positions if p.profit_rate >= tp2_rate]
    stop_loss = [p for p in positions if p.profit_rate <= -sl_rate]

    with st.expander(f"+{tp1_rate:.0f}% 절반매도 대상 — {len(half_sell)}개", expanded=bool(half_sell)):
        if half_sell:
            for p in half_sell:
                st.write(f"- **{p.name}** ({p.symbol}) | {format_rate(p.profit_rate)} | {p.quantity}주 @ {format_price(p.current_price)}")
        else:
            st.write("해당 없음")

    with st.expander(f"+{tp2_rate:.0f}% 전량매도 대상 — {len(full_sell)}개", expanded=bool(full_sell)):
        if full_sell:
            for p in full_sell:
                st.write(f"- **{p.name}** ({p.symbol}) | {format_rate(p.profit_rate)} | {p.quantity}주 @ {format_price(p.current_price)}")
        else:
            st.write("해당 없음")

    with st.expander(f"-{sl_rate:.0f}% 하락 손절 대상 — {len(stop_loss)}개", expanded=bool(stop_loss)):
        if stop_loss:
            for p in stop_loss:
                st.write(f"- **{p.name}** ({p.symbol}) | {format_rate(p.profit_rate)} | {p.quantity}주 @ {format_price(p.current_price)}")
        else:
            st.write("해당 없음")

st.divider()

# ---------------------------------------------------------------------------
# Section 3 — 매도 유형 선택 및 실행
# ---------------------------------------------------------------------------
st.subheader("매도 실행")

sell_type = st.radio(
    "매도 유형",
    options=[
        "조건 매도 (수익률 기준)",
        "수동 일괄매도",
        "10:15 일괄매도 (예약)",
    ],
    horizontal=True,
)

has_positions = bool(positions)


def _get_or_create_broker():
    broker = st.session_state.get("sell_broker")
    if broker is None:
        cfg = get_config()
        broker = create_broker(cfg=cfg, mode=selected_mode, confirm_text=confirm_text)
        st.session_state["sell_broker"] = broker
    return broker


def _save_and_show_results(results, order_mgr):
    try:
        log_path = order_mgr.save_order_log(results)
        st.caption(f"주문 로그: {log_path}")
    except Exception:
        pass
    st.session_state["sell_results"] = results
    success_cnt = sum(1 for r in results if r.success)
    fail_cnt = len(results) - success_cnt
    st.success(f"매도 완료: 성공 {success_cnt}건 / 실패 {fail_cnt}건")
    total_proceeds = sum(r.quantity * r.price for r in results if r.success)
    st.metric("총 매도금액", format_amount(total_proceeds))


# ── 조건 매도 ──────────────────────────────────────────────────────────────
if sell_type == "조건 매도 (수익률 기준)":
    st.write(f"- **+{tp1_rate:.0f}%** 도달 종목: 절반 매도")
    st.write(f"- **+{tp2_rate:.0f}%** 도달 종목: 전량 매도")
    st.write(f"- **-{sl_rate:.0f}%** 하락 종목: 손절 전량 매도 (2% 하락 익절 포함)")

    if not has_positions:
        st.warning("먼저 보유종목을 조회하세요.")
    else:
        exit_plans: list[dict] = []
        pos_map = {p.symbol: p for p in positions}

        for p in positions:
            if p.profit_rate >= tp2_rate:
                exit_plans.append({"symbol": p.symbol, "name": p.name,
                                   "action": "sell_all", "quantity": p.quantity,
                                   "current_price": p.current_price,
                                   "reason": f"+{p.profit_rate:.1f}% 전량매도"})
            elif p.profit_rate >= tp1_rate:
                exit_plans.append({"symbol": p.symbol, "name": p.name,
                                   "action": "sell_half", "quantity": max(1, p.quantity // 2),
                                   "current_price": p.current_price,
                                   "reason": f"+{p.profit_rate:.1f}% 절반매도"})
            elif p.profit_rate <= -sl_rate:
                exit_plans.append({"symbol": p.symbol, "name": p.name,
                                   "action": "sell_all", "quantity": p.quantity,
                                   "current_price": p.current_price,
                                   "reason": f"{p.profit_rate:.1f}% 하락 손절"})

        if exit_plans:
            for plan in exit_plans:
                action_label = {"sell_all": "전량매도", "sell_half": "절반매도"}.get(plan["action"], plan["action"])
                st.write(f"- **{plan['name']}** ({plan['symbol']}) | {action_label} {plan['quantity']}주 | {plan['reason']}")

            confirm_cond = st.checkbox("위 매도 내역을 확인했습니다", key="chk_cond_sell")
            if st.button("조건 매도 실행", disabled=not (confirm_cond and real_sell_ok), type="primary"):
                try:
                    broker = _get_or_create_broker()
                    cfg = get_config()
                    order_mgr = OrderManager(broker, cfg=cfg)
                    cond_results = []
                    with st.spinner("조건 매도 중..."):
                        for plan in exit_plans:
                            pos = pos_map.get(plan["symbol"])
                            if pos is None:
                                continue
                            if plan["action"] == "sell_all":
                                r = order_mgr.execute_sell_all([pos])
                                cond_results.extend(r)
                            else:
                                r = order_mgr.execute_sell_partial(pos, plan["quantity"], plan["current_price"])
                                cond_results.append(r)
                    _save_and_show_results(cond_results, order_mgr)
                except RuntimeError as exc:
                    st.error(f"안전장치 차단: {exc}")
                except Exception as exc:
                    st.error(f"매도 실패: {exc}")
        else:
            st.success("현재 매도 조건을 충족하는 종목이 없습니다.")

# ── 수동 일괄매도 ──────────────────────────────────────────────────────────
elif sell_type == "수동 일괄매도":
    st.warning("현재 보유 중인 모든 종목을 즉시 매도합니다.")
    if not has_positions:
        st.warning("먼저 보유종목을 조회하세요.")
    else:
        confirm_bulk = st.checkbox("전체 매도를 확인했습니다", key="chk_bulk_sell")
        if st.button(
            "일괄매도 실행",
            disabled=not (confirm_bulk and real_sell_ok),
            type="primary",
            use_container_width=True,
        ):
            try:
                broker = _get_or_create_broker()
                cfg = get_config()
                order_mgr = OrderManager(broker, cfg=cfg)
                with st.spinner("전체 종목 매도 중..."):
                    bulk_results = order_mgr.execute_sell_all(positions)
                _save_and_show_results(bulk_results, order_mgr)
            except RuntimeError as exc:
                st.error(f"안전장치 차단: {exc}")
            except Exception as exc:
                st.error(f"매도 실패: {exc}")

# ── 10:15 일괄매도 ─────────────────────────────────────────────────────────
elif sell_type == "10:15 일괄매도 (예약)":
    now = datetime.now()
    h, m = now.hour, now.minute
    is_1015 = (h == 10 and 13 <= m <= 17)  # 10:13~10:17 허용

    st.markdown(f"**현재 시각**: {now.strftime('%H:%M:%S')}")

    if is_1015:
        st.success("10:15 매도 시간입니다! 아래 버튼을 눌러 일괄매도를 실행하세요.")
    else:
        st.info("10:15 일괄매도 예약 중 — 10:13~10:17 사이에 버튼이 활성화됩니다.")

    if not has_positions:
        st.warning("먼저 보유종목을 조회하세요.")
    else:
        sell_disabled = not is_1015 or not real_sell_ok
        confirm_sched = st.checkbox("10:15 일괄매도를 확인했습니다", key="chk_sched_sell")
        if st.button(
            "10:15 일괄매도 실행",
            disabled=not (is_1015 and confirm_sched and real_sell_ok),
            type="primary",
            use_container_width=True,
        ):
            try:
                broker = _get_or_create_broker()
                cfg = get_config()
                order_mgr = OrderManager(broker, cfg=cfg)
                with st.spinner("10:15 일괄매도 중..."):
                    sched_results = order_mgr.execute_sell_all(positions)
                _save_and_show_results(sched_results, order_mgr)
            except RuntimeError as exc:
                st.error(f"안전장치 차단: {exc}")
            except Exception as exc:
                st.error(f"매도 실패: {exc}")

        if not is_1015:
            st.caption(f"현재 {now.strftime('%H:%M')} — 10:13~10:17 사이에만 활성화됩니다.")

# ---------------------------------------------------------------------------
# Section 4 — 매도 결과
# ---------------------------------------------------------------------------
if st.session_state.get("sell_results"):
    st.divider()
    st.subheader("매도 결과")
    sell_results = st.session_state["sell_results"]
    if not sell_results:
        st.info("매도 결과가 없습니다.")
    else:
        rows_html = "".join(
            f"<tr>"
            f"<td>{r.symbol}</td><td>{r.name}</td>"
            f"<td style='text-align:right'>{r.quantity:,}주</td>"
            f"<td style='text-align:right'>{format_price(r.price)}</td>"
            f"<td style='text-align:right'>{format_amount(r.quantity * r.price)}</td>"
            f"<td style='color:{'#2ecc71' if r.success else '#e74c3c'};font-weight:bold'>"
            f"{'성공' if r.success else '실패'}</td>"
            f"<td>{r.message}</td>"
            f"</tr>"
            for r in sell_results
        )
        st.markdown(
            f"<style>.res-t{{width:100%;border-collapse:collapse;font-size:.9rem}}"
            f".res-t th{{background:#1e2d3d;color:#fff;padding:8px 12px;text-align:left}}"
            f".res-t td{{padding:6px 12px;border-bottom:1px solid #2d3f50}}"
            f".res-t tr:hover td{{background:#1a2535}}</style>"
            f"<table class='res-t'><thead><tr>"
            f"<th>종목코드</th><th>종목명</th><th>수량</th>"
            f"<th>매도가</th><th>매도금액</th><th>결과</th><th>메시지</th>"
            f"</tr></thead><tbody>{rows_html}</tbody></table>",
            unsafe_allow_html=True,
        )
        success_results = [r for r in sell_results if r.success]
        total_proceeds = sum(r.quantity * r.price for r in success_results)
        s1, s2, s3 = st.columns(3)
        s1.metric("성공/실패", f"{len(success_results)}건/{len(sell_results)-len(success_results)}건")
        s2.metric("총 매도금액", format_amount(total_proceeds))
        s3.metric("매도 완료 시각", datetime.now().strftime("%H:%M:%S"))
