"""
7_장중자동매매.py — 완전 자동 장중매매 (Sector Leader Auto Intraday Trader)
전략 선택: 기존 눌림목 1종목 / 고수 눌림목 Top3 동시 감시
"""
import sys
import csv
import json
import time
from pathlib import Path
from datetime import datetime

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import streamlit as st
import pandas as pd

try:
    from app.config import get_config
    from app.trading.broker_factory import create_broker
    from app.services.intraday_budget_allocator import IntradayBudgetAllocator
    from app.services.intraday_auto_trade_service import (
        IntradayAutoTradeService,
        STATUS_WAITING, STATUS_PENDING, STATUS_HOLDING,
        STATUS_HALF_SOLD, STATUS_COOLING, STATUS_DONE, STATUS_ERROR,
        _ROOT as _SVC_ROOT,
    )
except Exception as e:
    st.error(f"모듈 로드 오류: {e}")
    st.stop()

try:
    from app.services.master_pullback_top3_service import (
        MasterPullbackTop3Service,
        STRATEGY_NAME as _MASTER_PB_STRATEGY_NAME,
    )
    _MASTER_PB_AVAILABLE = True
except Exception:
    _MASTER_PB_AVAILABLE = False

cfg = get_config()
_today = datetime.now().strftime("%Y%m%d")


def _safe_create_broker(mode, confirm_text="", runtime_real_mode=False,
                         runtime_enable_real_buy=False, runtime_enable_real_sell=False):
    try:
        return create_broker(cfg=cfg, mode=mode, confirm_text=confirm_text,
                             runtime_real_mode=runtime_real_mode,
                             runtime_enable_real_buy=runtime_enable_real_buy,
                             runtime_enable_real_sell=runtime_enable_real_sell)
    except TypeError:
        try:
            return create_broker(cfg=cfg, mode=mode, confirm_text=confirm_text,
                                 runtime_real_mode=runtime_real_mode)
        except TypeError:
            return create_broker(cfg=cfg, mode=mode)

# ── 상태 이모지 매핑 ──────────────────────────────────────────────────────────
_STATUS_EMOJI = {
    STATUS_WAITING:  "⏳ 대기",
    STATUS_PENDING:  "📤 주문중",
    STATUS_HOLDING:  "📈 보유중",
    STATUS_HALF_SOLD:"📉 절반매도",
    STATUS_COOLING:  "❄️ 쿨다운",
    STATUS_DONE:     "✅ 완료",
    STATUS_ERROR:    "❌ 오류",
}

def _format_price(v):
    try:
        return f"{int(float(v)):,}"
    except Exception:
        return "-"

def _format_pct(v):
    try:
        return f"{float(v):+.2f}%"
    except Exception:
        return "-"

def _format_amt(v):
    try:
        v = float(v)
        if abs(v) >= 1_000_000:
            return f"{v/1_000_000:.1f}백만"
        return f"{v:,.0f}"
    except Exception:
        return "-"


# ── 상태 파일 직접 읽기 (서비스 인스턴스 없이) ──────────────────────────────
def _load_state_file(strategy_key="intraday_auto_trade") -> dict:
    ic = cfg._raw.get(strategy_key, {})
    if strategy_key == "master_pullback_top3":
        tmpl = ic.get("state_file", "data/state/master_pullback_top3_multi_entry_YYYYMMDD.json")
    else:
        tmpl = ic.get("state_file", "data/state/intraday_auto_trade_state_YYYYMMDD.json")
    path = _SVC_ROOT / tmpl.replace("YYYYMMDD", _today)
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ── 거래 로그 CSV 직접 읽기 ──────────────────────────────────────────────────
def _load_trade_log(strategy_key="intraday_auto_trade") -> list[dict]:
    ic = cfg._raw.get(strategy_key, {})
    if strategy_key == "master_pullback_top3":
        tmpl = ic.get("log_file", "data/logs/master_pullback_top3_YYYYMMDD.csv")
    else:
        tmpl = ic.get("log_file", "data/logs/intraday_auto_trades_YYYYMMDD.csv")
    path = _SVC_ROOT / tmpl.replace("YYYYMMDD", _today)
    if not path.exists():
        return []
    try:
        rows = []
        with open(path, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(dict(row))
        return rows
    except Exception:
        return []


# ===========================================================================
# Page
# ===========================================================================

st.title("완전 자동 장중매매 — 주도섹터 Top3")
st.caption("1분봉/3분봉 기반 자동매수·매도·재진입 | 상태머신 관리")

st.divider()

# ── 전략 선택 ─────────────────────────────────────────────────────────────────
strategy_options = ["기존 자동매매 (눌림목 1종목)", "고수 눌림목 Top3 동시 감시"]
selected_strategy = st.selectbox(
    "전략 선택",
    options=strategy_options,
    index=0,
    help="고수 눌림목 Top3: 오전 9:15~10:00 Top3 동시 감시, 조건 만족 종목 모두 순차 매수",
)
_use_master_pb = "Top3" in selected_strategy

if _use_master_pb:
    if _MASTER_PB_AVAILABLE:
        st.success("🎯 고수 눌림목 Top3 동시 감시 전략 | 오전 9:15~10:00 Top3 순차 매수")
    else:
        st.error("master_pullback_top3_service 로드 실패. 기존 전략으로 fallback됩니다.")
        _use_master_pb = False

_strategy_cfg_key = "master_pullback_top3" if _use_master_pb else "intraday_auto_trade"

st.divider()

# ── 계좌 모드 / 예산 ──────────────────────────────────────────────────────────
col_mode, col_budget = st.columns([1, 2])
with col_mode:
    selected_mode = st.selectbox(
        "계좌 모드",
        options=["dry_run", "mock", "real"],
        index=["dry_run", "mock", "real"].index(cfg.mode) if cfg.mode in ["dry_run", "mock", "real"] else 0,
        help="dry_run: 가상 | mock: KIS 모의투자 | real: KIS 실전투자",
    )
with col_budget:
    total_budget = st.number_input(
        "총 예산 (원)", min_value=1_000_000, max_value=100_000_000,
        value=int(cfg.trading.get("total_budget", 10_000_000)),
        step=1_000_000, format="%d",
    )

if selected_mode == "dry_run":
    st.info("드라이런 모드: 실제 주문 없이 가상 매수/매도가 실행됩니다.")
elif selected_mode == "mock":
    st.warning("모의투자 모드: KIS 모의투자 계좌에 주문됩니다. (실제 돈 아님)")
elif selected_mode == "real":
    st.error("실전투자 모드: 실제 KIS 계좌에 주문됩니다. 신중하게 확인하세요!")

_runtime_real_mode = False
_runtime_enable_real_buy = st.session_state.get("enable_real_buy", False)
_runtime_enable_real_sell = st.session_state.get("enable_real_sell", False)

if selected_mode == "real":
    _real_mode_enabled = st.session_state.get("real_mode_enabled", False)
    if _real_mode_enabled:
        st.error("실전모드 활성화 중 — 실제 계좌로 자동매매가 실행됩니다.", icon="🔴")
        _runtime_real_mode = True
    else:
        st.error("실전모드 미활성화 — 'API 연결' 페이지에서 실전모드 버튼을 먼저 활성화하세요.")

_confirm_text = ""
if selected_mode == "real":
    try:
        _expected_text = cfg.real_confirm_text()
    except Exception:
        _expected_text = "I_UNDERSTAND_REAL_TRADING_RISK"
    _confirm_text = st.text_input(
        f"실전투자 확인 문구 입력 ('{_expected_text}')",
        type="password", placeholder=_expected_text,
    )
    if _confirm_text and _confirm_text != _expected_text:
        st.error("확인 문구가 틀립니다. 자동매매 버튼이 비활성화됩니다.")

_real_confirm_ok = (
    selected_mode != "real"
    or (_confirm_text and _confirm_text == (
        cfg.real_confirm_text() if callable(getattr(cfg, "real_confirm_text", None))
        else "I_UNDERSTAND_REAL_TRADING_RISK"
    ))
)

if selected_mode in ("mock", "real"):
    with st.expander("브로커 연결 상태", expanded=False):
        import json as _json
        _cache_path = _SVC_ROOT / "data" / "cache" / f"kis_token_{selected_mode}.json"
        if _cache_path.exists():
            try:
                with open(_cache_path) as _f:
                    _cd = _json.load(_f)
                _exp = _cd.get("expires_at", "")
                st.caption(f"KIS {selected_mode.upper()} 토큰: 만료 {_exp[:19] if _exp else '알 수 없음'}")
            except Exception:
                st.caption("토큰 캐시 읽기 실패")
        else:
            st.caption(f"KIS {selected_mode.upper()} 토큰: 없음 (첫 실행 시 발급)")
        c1, c2, c3 = st.columns(3)
        c1.metric("선택 모드", selected_mode.upper())
        c2.metric("실전매수", "허용" if _runtime_enable_real_buy else "차단")
        c3.metric("실전매도", "허용" if _runtime_enable_real_sell else "차단")

# 전략별 추가 설정 (고수 눌림목 Top3)
if _use_master_pb:
    with st.expander("고수 눌림목 Top3 설정", expanded=False):
        use_morning_budget_ratio = st.slider(
            "오전 매수 예산 비율", min_value=0.5, max_value=1.0, value=1.0, step=0.05,
            help="1.0 = 배정예산 100% 사용, 0.5 = 50% 사용",
        )
        allow_second_entry = st.checkbox("10:00 이후 강화 재진입 허용", value=True)
    st.session_state["master_pb_morning_budget_ratio"] = use_morning_budget_ratio
    st.session_state["master_pb_allow_second"] = allow_second_entry

st.divider()

# ── Top3 불러오기 ─────────────────────────────────────────────────────────────
if st.button("📋 Top3 종목 불러오기", use_container_width=True):
    top3 = (st.session_state.get("sl_top3")
            or st.session_state.get("sector_leader_top3", []))
    if not top3:
        st.warning("'주도섹터 Top3' 탭에서 먼저 종목을 선정하세요.")
    else:
        allocator = IntradayBudgetAllocator()
        allocated = allocator.allocate(top3, float(total_budget))
        st.session_state["intraday_allocated_top3"] = allocated
        st.success(f"Top3 종목 {len(allocated)}개 로드 완료")

if st.session_state.get("intraday_allocated_top3"):
    allocated = st.session_state["intraday_allocated_top3"]
    rows = [{
        "순위": s.get("rank", ""),
        "종목코드": s.get("symbol", ""),
        "종목명": s.get("name", ""),
        "현재가": _format_price(s.get("current_price", 0)),
        "배분비중": f"{s.get('allocated_weight', 0)*100:.1f}%",
        "배분예산": _format_price(s.get("allocated_budget", 0)),
        "배분수량": s.get("allocated_quantity", 0),
        "최종점수": round(float(s.get("final_score", 0) or 0), 2),
    } for s in allocated]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

st.divider()

# ── 자동매매 ON/OFF ───────────────────────────────────────────────────────────
st.subheader("자동매매 제어")
running = st.session_state.get("intraday_auto_trade_running", False)

col_on, col_off, col_once, col_refresh = st.columns(4)
with col_on:
    _btn_disabled = running or (selected_mode == "real" and not _real_confirm_ok)
    if st.button("▶ 자동매매 ON", type="primary", use_container_width=True, disabled=_btn_disabled):
        if not st.session_state.get("intraday_allocated_top3"):
            st.error("먼저 Top3 종목을 불러오세요.")
        else:
            st.session_state["intraday_auto_trade_running"] = True
            st.session_state["intraday_selected_mode"] = selected_mode
            st.session_state["intraday_selected_strategy"] = selected_strategy
            st.rerun()
with col_off:
    if st.button("⏹ 자동매매 OFF", use_container_width=True, disabled=not running):
        st.session_state["intraday_auto_trade_running"] = False
        st.rerun()
with col_once:
    manual_run = st.button("🔄 1회 실행", use_container_width=True)
with col_refresh:
    if st.button("🔃 화면 갱신", use_container_width=True):
        st.rerun()

if running:
    st.success("🟢 자동매매 실행 중 — 10초마다 자동 갱신")
else:
    st.info("⚫ 자동매매 대기 중")

# ── 수동 또는 자동 run_once 실행 ─────────────────────────────────────────────
if running or manual_run:
    allocated = st.session_state.get("intraday_allocated_top3", [])
    if not allocated:
        st.warning("Top3 종목을 먼저 불러오세요.")
        st.session_state["intraday_auto_trade_running"] = False
    else:
        try:
            with st.spinner("실행 중..."):
                _active_mode = st.session_state.get("intraday_selected_mode", selected_mode)
                _active_strategy = st.session_state.get("intraday_selected_strategy", selected_strategy)
                broker = _safe_create_broker(
                    mode=_active_mode,
                    confirm_text=_confirm_text,
                    runtime_real_mode=_runtime_real_mode,
                    runtime_enable_real_buy=_runtime_enable_real_buy,
                    runtime_enable_real_sell=_runtime_enable_real_sell,
                )
                kis_client = getattr(broker, "_kis", None) or getattr(broker, "kis_client", None)

                if "Top3" in _active_strategy and _MASTER_PB_AVAILABLE:
                    svc = MasterPullbackTop3Service(broker=broker, kis_client=kis_client, cfg=cfg)
                    svc.total_budget = float(total_budget)
                    # 설정 override
                    svc.use_morning_budget_ratio = st.session_state.get("master_pb_morning_budget_ratio", 1.0)
                    svc.allow_second_entry = st.session_state.get("master_pb_allow_second", True)
                    svc.load_top3(allocated)
                else:
                    svc = IntradayAutoTradeService(broker=broker, kis_client=kis_client, cfg=cfg)
                    svc.total_budget = float(total_budget)
                    svc.load_top3(allocated)

                result = svc.run_once()
            st.session_state["intraday_last_result"] = result
            st.session_state["intraday_last_at"] = datetime.now().strftime("%H:%M:%S")
        except Exception as ex:
            st.error(f"실행 오류: {ex}")
            st.session_state["intraday_auto_trade_running"] = False

st.divider()

# ===========================================================================
# 감시 종목 현황 (상태 파일 직접 읽기)
# ===========================================================================
st.subheader("감시 종목 현황")

last_at = st.session_state.get("intraday_last_at", "")
if last_at:
    st.caption(f"마지막 실행: {last_at}")

state_data = _load_state_file(_strategy_cfg_key)
# 고수 눌림목 Top3는 symbols_state, 기존은 symbols 키 사용
sym_states = state_data.get("symbols_state", state_data.get("symbols", {}))

if not sym_states:
    last_result = st.session_state.get("intraday_last_result", {})
    sym_map = last_result.get("symbols", {})
    if sym_map:
        st.info("상태 파일 없음 — 마지막 실행 결과 기준 표시")
        for sym, status in sym_map.items():
            st.metric(sym, _STATUS_EMOJI.get(status, status))
    else:
        st.info("감시 중인 종목 없음 — Top3를 불러오고 1회 이상 실행하세요.")
else:
    if _use_master_pb:
        # ── 고수 눌림목 Top3 전용 상세 테이블 ────────────────────────────────
        monitor_rows = []
        for sym, s in sym_states.items():
            avg_p = float(s.get("avg_buy_price", 0) or 0)
            cur_p = float(s.get("current_price", 0) or 0)
            qty = int(s.get("position_quantity", 0) or 0)
            pnl_rate = ((cur_p - avg_p) / avg_p * 100) if avg_p > 0 and cur_p > 0 else None
            status = s.get("status", "")

            cooldown_str = ""
            if status == STATUS_COOLING:
                cu = s.get("cooldown_until", "")
                if cu:
                    try:
                        remaining = max(0, (datetime.fromisoformat(cu) - datetime.now()).seconds // 60)
                        cooldown_str = f"({remaining}분)"
                    except Exception:
                        pass

            monitor_rows.append({
                "종목명": f"{s.get('name', sym)}({sym})",
                "상태": _STATUS_EMOJI.get(status, status) + cooldown_str,
                "오전진입": "✅" if s.get("morning_entry_done") else "—",
                "morning점수": s.get("morning_score", 0),
                "second점수": s.get("second_score", 0),
                "Buy🚦": "🟢" if s.get("last_buy_flag") else "🔴",
                "현재가": _format_price(cur_p) if cur_p > 0 else "-",
                "수익률": (_format_pct(pnl_rate) if pnl_rate is not None else "-"),
                "진입횟수": f"{s.get('entries_count', 0)}회",
                "보유수량": qty if qty > 0 else "-",
                "평균단가": _format_price(avg_p) if avg_p > 0 else "-",
                "+1.35%절반": "✅" if s.get("half_sold_done") else "—",
                "+2.2%전량": "✅" if s.get("full_sold_done") else "—",
                "쿨다운": s.get("cooldown_until", "")[:16] if s.get("cooldown_until") else "-",
                "직전청산": s.get("last_exit_type", "-") or "-",
                "마지막사유": s.get("last_reason", ""),
                "실현손익": _format_amt(s.get("realized_pnl", 0)),
                "배분예산": _format_price(s.get("allocated_budget", 0)),
            })

        if monitor_rows:
            def _row_color(row):
                status_str = str(row.get("상태", ""))
                if "보유" in status_str:
                    return ["background-color:#e8f5e9"] * len(row)
                if "절반" in status_str:
                    return ["background-color:#fff9c4"] * len(row)
                if "오류" in status_str:
                    return ["background-color:#ffebee"] * len(row)
                return [""] * len(row)

            st.dataframe(
                pd.DataFrame(monitor_rows).style.apply(_row_color, axis=1),
                use_container_width=True, hide_index=True,
            )

        total_entries = state_data.get("total_entries_today", 0)
        total_realized = sum(float(s.get("realized_pnl", 0) or 0) for s in sym_states.values())
        mc1, mc2, mc3, mc4 = st.columns(4)
        mc1.metric("오늘 총 진입", f"{total_entries}회")
        mc2.metric("오늘 실현손익", _format_amt(total_realized))
        mc3.metric("감시 종목 수", len(sym_states))
        morning_done_count = sum(1 for s in sym_states.values() if s.get("morning_entry_done"))
        mc4.metric("오전 진입 완료", f"{morning_done_count}종목")

    else:
        # ── 기존 전략 테이블 ─────────────────────────────────────────────────
        monitor_rows = []
        for sym, s in sym_states.items():
            avg_p = float(s.get("avg_buy_price", 0) or 0)
            cur_p = float(s.get("current_price", 0) or 0)
            qty = int(s.get("position_quantity", 0) or 0)
            pnl_rate = ((cur_p - avg_p) / avg_p * 100) if avg_p > 0 and cur_p > 0 else None
            pnl_unrealized = (cur_p - avg_p) * qty if avg_p > 0 and qty > 0 else 0
            status = s.get("status", "")

            cooldown_str = ""
            if status == STATUS_COOLING:
                cu = s.get("cooldown_until", "")
                if cu:
                    try:
                        remaining = (datetime.fromisoformat(cu) - datetime.now()).seconds // 60
                        cooldown_str = f"({remaining}분 남음)"
                    except Exception:
                        pass

            monitor_rows.append({
                "종목명": f"{s.get('name', sym)}({sym})",
                "상태": _STATUS_EMOJI.get(status, status) + cooldown_str,
                "진입횟수": f"{s.get('entries_count', 0)}회",
                "보유수량": qty if qty > 0 else "-",
                "평균단가": _format_price(avg_p) if avg_p > 0 else "-",
                "현재가": _format_price(cur_p) if cur_p > 0 else "-",
                "평가손익": (_format_pct(pnl_rate) if pnl_rate is not None else "-"),
                "미실현손익": _format_amt(pnl_unrealized) if pnl_unrealized else "-",
                "실현손익": _format_amt(s.get("realized_pnl", 0)),
                "배분예산": _format_price(s.get("allocated_budget", 0)),
                "마지막사유": s.get("last_reason", ""),
            })

        if monitor_rows:
            df_monitor = pd.DataFrame(monitor_rows)

            def _row_color(row):
                status_str = str(row.get("상태", ""))
                if "보유" in status_str:
                    return ["background-color:#e8f5e9"] * len(row)
                if "절반" in status_str:
                    return ["background-color:#fff9c4"] * len(row)
                if "오류" in status_str:
                    return ["background-color:#ffebee"] * len(row)
                return [""] * len(row)

            st.dataframe(
                df_monitor.style.apply(_row_color, axis=1),
                use_container_width=True, hide_index=True,
            )

        total_entries = state_data.get("total_entries_today", 0)
        ic = cfg._raw.get("intraday_auto_trade", {})
        max_entries = ic.get("max_total_entries_per_day", 3)
        total_realized = sum(
            float(s.get("realized_pnl", 0) or 0) for s in sym_states.values()
        )
        mc1, mc2, mc3 = st.columns(3)
        mc1.metric("오늘 총 진입 횟수", f"{total_entries} / {max_entries}회")
        mc2.metric("오늘 실현손익 합계", _format_amt(total_realized))
        mc3.metric("감시 종목 수", len(sym_states))

st.divider()

# ===========================================================================
# 오늘 거래내역 전체
# ===========================================================================
st.subheader(f"오늘 거래내역 ({_today})")

trade_logs = _load_trade_log(_strategy_cfg_key)

if not trade_logs:
    st.info("오늘 거래내역 없음")
else:
    col_map = {
        "timestamp": "시각", "action": "구분", "symbol": "종목코드", "name": "종목명",
        "quantity": "수량", "price": "가격", "reason": "사유", "sell_type": "매도유형",
        "order_success": "성공", "order_id": "주문번호", "error": "오류",
        "morning_score": "오전점수", "second_score": "재진입점수",
    }

    df_log = pd.DataFrame(trade_logs)
    df_log = df_log.rename(columns={k: v for k, v in col_map.items() if k in df_log.columns})

    if "구분" in df_log.columns:
        df_log["구분"] = df_log["구분"].map(
            {"buy": "🔴 매수", "sell": "🔵 매도", "force_close": "🟠 강제청산"}
        ).fillna(df_log["구분"])

    def _log_color(row):
        act = str(row.get("구분", ""))
        if "매수" in act:
            return ["background-color:#fce4ec"] * len(row)
        if "매도" in act or "청산" in act:
            return ["background-color:#e3f2fd"] * len(row)
        return [""] * len(row)

    st.dataframe(
        df_log.style.apply(_log_color, axis=1),
        use_container_width=True, hide_index=True,
    )

    buys = [r for r in trade_logs if r.get("action") == "buy" and r.get("order_success") in ("True", True)]
    sells = [r for r in trade_logs if r.get("action") in ("sell", "force_close") and r.get("order_success") in ("True", True)]
    s1, s2, s3 = st.columns(3)
    s1.metric("매수 체결", f"{len(buys)}건")
    s2.metric("매도 체결", f"{len(sells)}건")
    s3.metric("전체 로그", f"{len(trade_logs)}건")

    csv_bytes = df_log.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(
        "거래내역 CSV 다운로드", csv_bytes,
        file_name=f"intraday_trades_{_today}.csv",
        mime="text/csv",
    )

st.divider()
st.page_link("pages/6_주도섹터_Top3.py", label="← 주도섹터 Top3 선정으로 이동", icon="🎯")

# ── 자동 루프 ─────────────────────────────────────────────────────────────────
if running:
    time.sleep(10)
    st.rerun()
