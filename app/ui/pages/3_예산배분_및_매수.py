"""
3_예산배분_및_매수.py

예산 배분 후 수동 매수 또는 9:20 일괄매수를 실행합니다.
데이터 소스: 거래량급증 Top10 (session_state["volume_spike_top10"]) 우선
"""
import sys
from pathlib import Path
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import types
import streamlit as st
import pandas as pd
from datetime import datetime
try:
    from zoneinfo import ZoneInfo as _ZoneInfo
    _KST = _ZoneInfo("Asia/Seoul")
except ImportError:
    import datetime as _dtmod
    _KST = _dtmod.timezone(_dtmod.timedelta(hours=9))

try:
    from app.trading.budget_allocator import BudgetAllocator
    from app.trading.broker_factory import create_broker
    from app.trading.order_manager import OrderManager, _is_etf_like
    from app.trading.kis_client import KISTokenError
    from app.config import get_config
    from app.utils.stock_utils import format_amount, format_price
except Exception as e:
    st.error(f"모듈 로드 오류: {e}")
    st.stop()


def _safe_create_broker(cfg, mode, confirm_text="", runtime_real_mode=False,
                         runtime_enable_real_buy=False, runtime_enable_real_sell=False):
    """구/신 버전 broker_factory 모두 호환 — TypeError 발생 시 순서대로 폴백."""
    try:
        return create_broker(
            cfg=cfg, mode=mode,
            confirm_text=confirm_text,
            runtime_real_mode=runtime_real_mode,
            runtime_enable_real_buy=runtime_enable_real_buy,
            runtime_enable_real_sell=runtime_enable_real_sell,
        )
    except TypeError:
        try:
            return create_broker(cfg=cfg, mode=mode, confirm_text=confirm_text,
                                 runtime_real_mode=runtime_real_mode)
        except TypeError:
            try:
                return create_broker(cfg=cfg, mode=mode, confirm_text=confirm_text)
            except TypeError:
                return create_broker(cfg=cfg, mode=mode)


# ---------------------------------------------------------------------------
# 거래량급증 dict → BudgetAllocator 호환 객체 변환
# ---------------------------------------------------------------------------

def _vs_to_candidate(d: dict, rank: int = None):
    return types.SimpleNamespace(
        rank=rank if rank is not None else int(d.get("rank", 0)),
        symbol=str(d.get("symbol", "")),
        name=str(d.get("name", "")),
        current_price=float(d.get("current_price", 0)),
        change_rate=float(d.get("change_rate", 0)),
        trade_value=float(d.get("trade_value", 0)),
        final_score=float(d.get("final_score", 0)),
        gap_rate=float(d.get("change_rate", 0)),
    )


def _load_vs_csv_today() -> list:
    date_str = datetime.now(_KST).strftime("%Y%m%d")
    csv_path = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "data" / "volume_spike" / f"{date_str}_volume_spike_top10.csv"
    )
    if not csv_path.exists():
        return []
    try:
        df = pd.read_csv(csv_path, dtype={"symbol": str})
        result = []
        for i, row in df.iterrows():
            result.append(types.SimpleNamespace(
                rank=int(row.get("rank", i + 1)),
                symbol=str(row.get("symbol", "")),
                name=str(row.get("name", "")),
                current_price=float(row.get("current_price", 0)),
                change_rate=float(row.get("change_rate", 0)),
                trade_value=float(row.get("trade_value", 0)),
                final_score=float(row.get("final_score", 0)),
                gap_rate=float(row.get("change_rate", 0)),
            ))
        return result
    except Exception:
        return []


def _show_buy_results(results, log_path=None):
    """매수 결과 공통 표시 함수."""
    success_count = sum(1 for r in results if r.success)
    fail_count = sum(1 for r in results if not r.success and not r.excluded_reason
                     and r.error_type not in ("excluded_etf", "duplicate", "validation_error", "batch_aborted"))
    etf_count = sum(1 for r in results if r.error_type == "excluded_etf")
    skip_count = sum(1 for r in results if r.error_type in ("duplicate", "validation_error", "batch_aborted"))
    token_error = any(r.error_type == "token_403" for r in results)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("매수 성공", f"{success_count}건")
    c2.metric("매수 실패", f"{fail_count}건")
    c3.metric("ETF 제외", f"{etf_count}건")
    c4.metric("스킵", f"{skip_count}건")

    if token_error:
        st.error(
            "tokenP 403 오류 발생 — 배치가 중단되었습니다.  \n"
            "KIS 앱키/시크릿이 올바른지, 하루 1회 이상 호출하지 않았는지 확인하세요."
        )

    # 상세 결과 테이블
    result_rows = []
    for r in results:
        if r.excluded_reason:
            status_label = "ETF제외"
        elif r.error_type == "batch_aborted":
            status_label = "중단"
        elif r.error_type in ("duplicate", "validation_error"):
            status_label = "스킵"
        elif r.success:
            status_label = "성공"
        else:
            status_label = "실패"

        result_rows.append({
            "종목코드": r.symbol,
            "종목명": r.name,
            "수량": r.quantity,
            "가격": format_price(r.price),
            "주문번호": r.order_id,
            "결과": status_label,
            "오류유형": r.error_type,
            "메시지": r.message[:60] if not r.success else "",
        })

    if result_rows:
        def _hl(row):
            label = str(row.get("결과", ""))
            if label == "성공":
                color = "#d4edda"
            elif label in ("ETF제외", "스킵", "중단"):
                color = "#fff3cd"
            else:
                color = "#f8d7da"
            return [f"background-color:{color}"] * len(row)

        df_res = pd.DataFrame(result_rows)
        st.dataframe(df_res.style.apply(_hl, axis=1), use_container_width=True, hide_index=True)

    # 실패 이유 요약
    failures = [r for r in results if not r.success and r.error_type not in
                ("excluded_etf", "duplicate", "validation_error", "batch_aborted")]
    if failures:
        with st.expander(f"실패 상세 ({len(failures)}건)", expanded=True):
            for r in failures:
                st.markdown(
                    f"- **{r.symbol} {r.name}**: "
                    f"`{r.error_type}` HTTP {r.http_status} — {r.message}"
                )

    # ETF 제외 목록
    etf_excluded = [r for r in results if r.error_type == "excluded_etf"]
    if etf_excluded:
        with st.expander(f"ETF/지수상품 제외 목록 ({len(etf_excluded)}건)"):
            for r in etf_excluded:
                st.markdown(f"- {r.symbol} **{r.name}**: {r.excluded_reason}")

    # 로그 다운로드
    if log_path:
        try:
            with open(log_path, "rb") as f:
                log_bytes = f.read()
            st.download_button(
                label="주문 로그 CSV 다운로드",
                data=log_bytes,
                file_name=Path(log_path).name,
                mime="text/csv",
                key=f"dl_log_{hash(log_path)}",
            )
        except Exception:
            st.caption(f"주문 로그: {log_path}")

    # 결과 요약 메시지
    if results and all(r.success for r in results if r.error_type == ""):
        st.balloons()
        st.success("모든 매수 주문 완료!")
    elif success_count > 0:
        st.warning(f"{success_count}개 성공 / {fail_count}개 실패 / {etf_count}개 ETF 제외")
    elif etf_count == len(results):
        st.warning("모든 종목이 ETF/지수상품으로 제외되었습니다.")
    else:
        st.error("매수 주문이 모두 실패했습니다.")


# ===========================================================================
# Page layout
# ===========================================================================

st.title("예산 배분 및 매수")

# ---------------------------------------------------------------------------
# Section 1 — 계좌 모드 및 예산 설정
# ---------------------------------------------------------------------------
st.subheader("계좌 모드 및 예산")

col_mode, col_budget, col_shares = st.columns(3)

with col_mode:
    selected_mode = st.selectbox(
        "계좌 모드",
        options=["dry_run", "mock", "real"],
        index=0,
        help="dry_run: 가상 | mock: KIS 모의투자 | real: KIS 실전투자",
    )

with col_budget:
    total_budget = st.number_input(
        "총 예산 (원)",
        min_value=100_000,
        max_value=100_000_000,
        value=10_000_000,
        step=100_000,
        format="%d",
    )

with col_shares:
    max_shares = st.number_input(
        "종목당 최대 수량",
        min_value=1,
        max_value=10,
        value=2,
    )

if selected_mode == "dry_run":
    st.info("드라이런 모드: 실제 주문 없이 가상 매수가 실행됩니다.")
elif selected_mode == "mock":
    st.warning("모의투자 모드: KIS 모의투자 계좌에 주문됩니다. (실제 돈 아님)")
elif selected_mode == "real":
    st.error("실전투자 모드: 실제 KIS 계좌에 주문됩니다. 신중하게 확인하세요!")

# 실전모드 런타임 플래그
_runtime_real_mode = False
_runtime_enable_real_buy = st.session_state.get("enable_real_buy", False)
_runtime_enable_real_sell = st.session_state.get("enable_real_sell", False)

if selected_mode == "real":
    _real_mode_enabled = st.session_state.get("real_mode_enabled", False)
    if _real_mode_enabled:
        st.error("실전모드 활성화 중: 실제 계좌로 매수가 실행됩니다.", icon="🔴")
        _runtime_real_mode = True
    else:
        st.error(
            "실전모드가 활성화되어 있지 않습니다.  \n"
            "'API 연결' 페이지에서 실전모드 버튼을 먼저 활성화하세요."
        )

# 실전투자 확인 문구
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
    )
    if confirm_text and confirm_text != expected_text:
        st.error("확인 문구가 틀립니다. 매수 버튼이 비활성화됩니다.")

# ── 브로커/모드 상태 표시 ──────────────────────────────────────────────────
with st.expander("브로커 상태 확인", expanded=False):
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("선택 모드", selected_mode.upper())
    s2.metric("실전모드", "ON" if _runtime_real_mode else "OFF")
    s3.metric("실전매수", "허용" if _runtime_enable_real_buy else "차단")
    s4.metric("실전매도", "허용" if _runtime_enable_real_sell else "차단")

    # 토큰 캐시 상태
    try:
        from pathlib import Path as _Path
        import json as _json
        _repo_root = _Path(__file__).resolve().parent.parent.parent.parent
        _cache_path = _repo_root / "data" / "cache" / f"kis_token_{selected_mode}.json"
        if _cache_path.exists():
            with open(_cache_path) as _f:
                _cdata = _json.load(_f)
            _exp = _cdata.get("expires_at", "")
            st.caption(f"토큰 캐시: {selected_mode} — 만료 {_exp[:19] if _exp else '알 수 없음'}")
        else:
            st.caption(f"토큰 캐시: {selected_mode} — 없음 (첫 매수 시 발급)")
    except Exception:
        st.caption("토큰 캐시 상태 조회 실패")
    st.caption("※ API 키/계좌번호 등 민감정보는 표시하지 않습니다.")

st.divider()

# ---------------------------------------------------------------------------
# Section 2 — 거래량급증 Top10 불러오기
# ---------------------------------------------------------------------------
st.subheader("거래량급증 Top10 불러오기")
st.caption("2단계(거래량급증 Top10 선정)에서 선정한 종목을 불러옵니다.")

col_load1, col_load2 = st.columns([1, 3])
with col_load1:
    if st.button("Top10 불러오기", use_container_width=True):
        loaded = []
        source_label = ""

        vs_dicts = st.session_state.get("volume_spike_top10") or []
        if vs_dicts:
            loaded = [_vs_to_candidate(d) for d in vs_dicts]
            source_label = f"세션 (거래량급증 {len(loaded)}개)"

        if not loaded:
            loaded = _load_vs_csv_today()
            if loaded:
                source_label = f"오늘 CSV (거래량급증 {len(loaded)}개)"

        if loaded:
            st.session_state["top15"] = loaded
            st.success(f"종목 {len(loaded)}개 로드 완료 ({source_label})")
        else:
            st.warning(
                "거래량급증 종목이 없습니다.  \n"
                "'거래량급증 Top10 선정' 탭에서 먼저 종목을 선정하세요."
            )

top15 = st.session_state.get("top15", [])
if top15:
    rows = []
    for c in top15:
        etf_flag = _is_etf_like(c.symbol, c.name)
        rows.append({
            "순위": c.rank,
            "종목코드": c.symbol,
            "종목명": c.name,
            "현재가": format_price(c.current_price),
            "상승률(%)": f"{c.change_rate:.2f}",
            "최종점수": f"{c.final_score:.2f}",
            "ETF제외": "⚠️ ETF/지수" if etf_flag else "",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

st.divider()

# ---------------------------------------------------------------------------
# Section 3 — 예산 배분
# ---------------------------------------------------------------------------
st.subheader("예산 배분")

col_alloc1, col_alloc2 = st.columns([1, 3])
with col_alloc1:
    if st.button("예산 배분 계산", use_container_width=True):
        if not top15:
            st.warning("먼저 Top 15 종목을 불러오세요.")
        else:
            try:
                allocator = BudgetAllocator()
                buy_plan = allocator.allocate(
                    top15,
                    total_budget=float(total_budget),
                    max_shares=int(max_shares),
                )
                st.session_state["buy_plan"] = buy_plan
                st.success(f"배분 완료: {len(buy_plan)}개 종목")
            except Exception as e:
                st.error(f"예산 배분 오류: {e}")

buy_plan = st.session_state.get("buy_plan", [])
if buy_plan:
    rows = []
    for p in buy_plan:
        etf_flag = _is_etf_like(p.symbol, p.name)
        rows.append({
            "순위": p.rank,
            "종목코드": p.symbol,
            "종목명": p.name,
            "현재가": format_price(p.current_price),
            "배분수량": p.allocated_quantity,
            "배분금액": format_amount(p.allocated_amount),
            "ETF제외": "⚠️" if etf_flag else "",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    total_invested = sum(p.allocated_amount for p in buy_plan)
    remaining = float(total_budget) - total_invested
    etf_in_plan = sum(1 for p in buy_plan if _is_etf_like(p.symbol, p.name))
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("배분 종목", f"{len(buy_plan)}개")
    m2.metric("총 투자금", format_amount(total_invested))
    m3.metric("잔여 예산", format_amount(remaining))
    m4.metric("ETF 제외 예정", f"{etf_in_plan}개")

st.divider()

# ---------------------------------------------------------------------------
# Section 4 — 매수 유형 선택 및 실행
# ---------------------------------------------------------------------------
st.subheader("매수 실행")

buy_type = st.radio(
    "매수 유형",
    options=["수동 매수", "9:20 일괄매수 (예약)", "종목 선택 매수"],
    horizontal=True,
    help="수동: 즉시 전종목 매수 | 9:20 예약: 9:20에 활성화 | 선택: 원하는 종목만 골라서 매수",
)

has_plan = bool(buy_plan)
real_confirm_ok = (
    selected_mode != "real"
    or (confirm_text and confirm_text == (
        get_config().real_confirm_text() if get_config else "I_UNDERSTAND_REAL_TRADING_RISK"
    ))
)

_execute_buy = False
_selected_plan_for_buy = buy_plan  # 기본값: 전체

# ── 수동 매수 ──────────────────────────────────────────────────────────────
if buy_type == "수동 매수":
    buy_disabled = not has_plan or not real_confirm_ok
    if st.button(
        "현재 리스트 전부 매수",
        disabled=buy_disabled,
        type="primary",
        use_container_width=True,
        key="btn_manual_buy_all",
    ):
        _execute_buy = True

    if not has_plan:
        st.caption("먼저 예산 배분 계산을 실행하세요.")
    elif buy_disabled and selected_mode == "real":
        st.caption("실전투자 확인 문구를 입력해야 활성화됩니다.")

# ── 9:20 일괄매수 예약 ────────────────────────────────────────────────────
elif buy_type == "9:20 일괄매수 (예약)":
    now = datetime.now(_KST)
    h, m = now.hour, now.minute
    is_920 = (h == 9 and 18 <= m <= 22)

    col_time, col_refresh = st.columns([4, 1])
    with col_time:
        st.markdown(f"**현재 시각 (KST)**: {now.strftime('%H:%M:%S')}")
    with col_refresh:
        if st.button("시간 갱신", use_container_width=True, key="btn_refresh_time"):
            st.rerun()

    if is_920:
        st.success("9:20 매수 시간입니다! 아래 버튼을 눌러 매수를 실행하세요.")
    else:
        st.info("9:20 일괄매수 예약 중 — 9:18~9:22 사이에 버튼이 활성화됩니다.")

    buy_disabled = not has_plan or not is_920 or not real_confirm_ok
    if st.button(
        "9:20 일괄매수 실행",
        disabled=buy_disabled,
        type="primary",
        use_container_width=True,
    ):
        _execute_buy = True

    if not is_920:
        st.caption(
            f"현재 {now.strftime('%H:%M')} (KST) — 9:18~9:22 사이에만 버튼이 활성화됩니다.  \n"
            "시간이 되면 위 '시간 갱신' 버튼을 눌러 페이지를 새로고침하세요."
        )

# ── 종목 선택 매수 ────────────────────────────────────────────────────────
elif buy_type == "종목 선택 매수":
    if not buy_plan:
        st.warning("먼저 위에서 '예산 배분 계산'을 실행하세요.")
    else:
        option_labels = [
            f"#{p.rank}  {p.name} ({p.symbol})  |  {format_price(p.current_price)}  |  {format_amount(p.allocated_amount)} / {p.allocated_quantity}주"
            for p in buy_plan
        ]
        selected_labels = st.multiselect(
            "매수할 종목 선택 (복수 선택 가능)",
            options=option_labels,
            default=[],
            placeholder="종목을 선택하세요",
            key="sel_buy_symbols",
        )

        label_to_plan = dict(zip(option_labels, buy_plan))
        selected_plan = [label_to_plan[l] for l in selected_labels if l in label_to_plan]

        if selected_plan:
            total_sel_amount = sum(p.allocated_amount for p in selected_plan)
            c1, c2 = st.columns(2)
            c1.metric("선택 종목", f"{len(selected_plan)}개")
            c2.metric("예상 투자금", format_amount(total_sel_amount))

            confirm_sel = st.checkbox("위 종목을 매수하겠습니다", key="chk_sel_buy")
            if st.button(
                "선택 종목 매수 실행",
                disabled=not (confirm_sel and real_confirm_ok),
                type="primary",
                use_container_width=True,
                key="btn_sel_buy",
            ):
                _execute_buy = True
                _selected_plan_for_buy = selected_plan
        else:
            st.info("위에서 매수할 종목을 선택하세요.")

# ── 공통 매수 실행 로직 ───────────────────────────────────────────────────
if _execute_buy:
    try:
        cfg = get_config()
        with st.spinner("브로커 초기화 중..."):
            broker = _safe_create_broker(
                cfg=cfg, mode=selected_mode,
                confirm_text=confirm_text,
                runtime_real_mode=_runtime_real_mode,
                runtime_enable_real_buy=_runtime_enable_real_buy,
                runtime_enable_real_sell=_runtime_enable_real_sell,
            )
        order_manager = OrderManager(broker=broker, cfg=cfg)

        plan_to_execute = _selected_plan_for_buy
        with st.spinner(f"{len(plan_to_execute)}개 종목 매수 중..."):
            results = order_manager.execute_buy_plans(plan_to_execute)

        st.session_state["buy_results"] = results

        log_path = None
        try:
            log_path = order_manager.save_order_log(results)
        except Exception as log_err:
            st.warning(f"로그 저장 오류: {log_err}")

        _show_buy_results(results, log_path=log_path)

    except KISTokenError as e:
        st.error(
            f"토큰 오류 (403): {e}\n\n"
            "KIS 앱키/시크릿이 올바른지, 동일 키로 당일 이미 토큰을 발급받지 않았는지 확인하세요."
        )
    except RuntimeError as e:
        st.error(f"매수 실행 오류 (안전장치 차단): {e}")
    except Exception as e:
        st.error(f"브로커 생성 실패: 실전모드 설정 또는 KIS 환경변수를 확인하세요.\n상세: {e}")

# 이전 결과 표시
if st.session_state.get("buy_results") and not _execute_buy:
    prev_results = st.session_state["buy_results"]
    success_count = sum(1 for r in prev_results if r.success)
    st.info(f"이전 매수 결과: {success_count}/{len(prev_results)}건 성공")
    if st.button("→ 보유종목으로 이동"):
        st.switch_page("pages/4_보유종목_및_일괄매도.py")
