"""
intraday_auto_trade_service.py — 완전 자동 장중매매 서비스

전략: 주도섹터 Top3 종목에 대해 1분봉/3분봉 기반으로 장중 자동매수/매도 수행.
상태머신: WAITING_ENTRY → BUY_ORDER_PENDING → HOLDING → HALF_SOLD → COOLING_DOWN → DONE / ERROR
"""
import csv
import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from app.logger import logger
from app.services.intraday_budget_allocator import IntradayBudgetAllocator
from app.strategy.intraday_indicators import (
    calculate_vwap,
    calculate_ema,
    calculate_rsi,
    resample_1m_to_3m,
    detect_bullish_reversal_1m,
    detect_bearish_volume_candle_1m,
    calculate_intraday_high_pullback,
)

STATUS_WAITING = "WAITING_ENTRY"
STATUS_PENDING = "BUY_ORDER_PENDING"
STATUS_HOLDING = "HOLDING"
STATUS_HALF_SOLD = "HALF_SOLD"
STATUS_COOLING = "COOLING_DOWN"
STATUS_DONE = "DONE"
STATUS_ERROR = "ERROR"

_ROOT = Path(__file__).resolve().parent.parent.parent

_LOG_COLUMNS = [
    "timestamp", "action", "symbol", "name", "quantity", "price",
    "reason", "sell_type", "order_success", "order_id", "error",
]


class IntradayAutoTradeService:
    """장중 자동매매 서비스 — 주도섹터 Top3 상태머신."""

    def __init__(self, broker, kis_client=None, cfg=None):
        from app.config import get_config
        self._cfg = cfg or get_config()
        self.broker = broker
        self.kis_client = kis_client

        ic = self._cfg._raw.get("intraday_auto_trade", {})
        self.total_budget: float = float(ic.get("total_budget", 10_000_000))
        self.max_position_count: int = int(ic.get("max_position_count", 3))
        self.check_interval_seconds: int = int(ic.get("check_interval_seconds", 10))
        self.buy_start_time: str = ic.get("buy_start_time", "09:10")
        self.buy_end_time: str = ic.get("buy_end_time", "14:40")
        self.force_sell_time: str = ic.get("force_sell_time", "15:10")
        self.max_total_entries_per_day: int = int(ic.get("max_total_entries_per_day", 3))
        self.max_entries_per_symbol: int = int(ic.get("max_entries_per_symbol", 2))
        self.cooldown_minutes: int = int(ic.get("cooldown_minutes", 10))
        self.allow_breakout: bool = bool(ic.get("allow_breakout_entry_if_no_pullback", True))

        buy_cond = ic.get("buy_conditions", {})
        self.min_pullback_pct: float = float(buy_cond.get("min_pullback_pct", -3.8))
        self.max_pullback_pct: float = float(buy_cond.get("max_pullback_pct", -1.2))
        self.min_volume_ratio: float = float(buy_cond.get("min_volume_ratio", 1.15))
        self.min_rsi: float = float(buy_cond.get("min_rsi", 42.0))
        self.max_rsi: float = float(buy_cond.get("max_rsi", 72.0))
        self.crash_threshold_pct: float = float(buy_cond.get("crash_threshold_pct", -5.0))

        relaxed = ic.get("relaxed_buy_conditions", {})
        self.relaxed_min_pullback: float = float(relaxed.get("min_pullback_pct", -0.8))
        self.relaxed_min_vol_ratio: float = float(relaxed.get("min_volume_ratio", 1.0))

        sell_cond = ic.get("sell_conditions", {})
        self.stop_loss_pct: float = float(sell_cond.get("stop_loss_pct", -1.2))
        self.half_tp_pct: float = float(sell_cond.get("half_take_profit_pct", 1.8))
        self.full_tp_pct: float = float(sell_cond.get("full_take_profit_pct", 3.2))
        self.trailing_stop_pct: float = float(sell_cond.get("trailing_stop_pct", -1.8))

        today = datetime.now().strftime("%Y%m%d")
        state_tmpl = ic.get("state_file", "data/state/intraday_auto_trade_state_YYYYMMDD.json")
        log_tmpl = ic.get("log_file", "data/logs/intraday_auto_trades_YYYYMMDD.csv")
        self.state_file = _ROOT / state_tmpl.replace("YYYYMMDD", today)
        self.log_file = _ROOT / log_tmpl.replace("YYYYMMDD", today)

        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

        # 런타임 상태
        self.symbols_state: dict[str, dict] = {}
        self.total_entries_today: int = 0
        self.breakout_entries_today: int = 0
        self.prev_rsi: dict[str, float] = {}

        self.load_state()

    # ── Top3 종목 로드 ─────────────────────────────────────────────────────

    def load_top3(self, top3: list[dict]) -> None:
        allocated = IntradayBudgetAllocator().allocate(top3, self.total_budget)
        for stock in allocated:
            sym = stock.get("symbol", "")
            if not sym:
                continue
            if sym in self.symbols_state:
                # 기존 상태 유지 (재시작 복원)
                self.symbols_state[sym]["allocated_budget"] = stock["allocated_budget"]
                self.symbols_state[sym]["allocated_weight"] = stock["allocated_weight"]
            else:
                self.symbols_state[sym] = {
                    "symbol": sym,
                    "name": stock.get("name", ""),
                    "rank": stock.get("rank", 0),
                    "allocated_budget": stock["allocated_budget"],
                    "allocated_weight": stock["allocated_weight"],
                    "entries_count": 0,
                    "position_quantity": 0,
                    "avg_buy_price": 0.0,
                    "current_price": float(stock.get("current_price", 0) or 0),
                    "highest_price_after_entry": 0.0,
                    "first_take_profit_done": False,
                    "second_take_profit_done": False,
                    "last_buy_at": "",
                    "last_sell_at": "",
                    "cooldown_until": "",
                    "status": STATUS_WAITING,
                    "last_buy_flag": False,
                    "last_sell_flag": "",
                    "last_reason": "",
                    "realized_pnl": 0.0,
                    "order_history": [],
                }
        self.save_state()

    # ── 메인 루프 ──────────────────────────────────────────────────────────

    def run_once(self) -> dict:
        now = datetime.now()
        summary = {
            "checked_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "actions": [],
            "symbols": {},
        }

        # 강제청산 시간 체크
        force_time = self._parse_time(self.force_sell_time)
        if now.time() >= force_time:
            results = self._force_sell_all()
            summary["actions"].extend(results)

        for sym, state in self.symbols_state.items():
            # 쿨다운 종료 체크
            if state["status"] == STATUS_COOLING:
                cooldown_until = state.get("cooldown_until", "")
                if cooldown_until:
                    try:
                        cu = datetime.fromisoformat(cooldown_until)
                        if now >= cu:
                            state["status"] = STATUS_WAITING
                            state["cooldown_until"] = ""
                    except Exception:
                        pass

            current_price = self._get_current_price(sym, state)
            if current_price > 0:
                state["current_price"] = current_price

            # 장중 고점 갱신
            if state["status"] in (STATUS_HOLDING, STATUS_HALF_SOLD):
                if current_price > state.get("highest_price_after_entry", 0):
                    state["highest_price_after_entry"] = current_price

            candles_1m = self._get_candles_1m(sym)

            if state["status"] == STATUS_WAITING:
                flag, reason = self._check_buy_flag(sym, state, candles_1m)
                state["last_buy_flag"] = flag
                state["last_reason"] = reason
                if flag:
                    result = self._execute_buy(sym, state, current_price)
                    summary["actions"].append({"symbol": sym, "action": "buy", **result})

            elif state["status"] in (STATUS_HOLDING, STATUS_HALF_SOLD):
                sell_type, reason = self._check_sell_flag(sym, state, candles_1m, current_price)
                state["last_sell_flag"] = sell_type
                if sell_type:
                    result = self._execute_sell(sym, state, sell_type, current_price)
                    summary["actions"].append({"symbol": sym, "action": "sell", "sell_type": sell_type, **result})

            summary["symbols"][sym] = state["status"]

        self.save_state()
        return summary

    # ── 분봉 조회 ──────────────────────────────────────────────────────────

    def _get_candles_1m(self, symbol: str) -> list[dict]:
        if self.kis_client is None:
            return []
        try:
            candles = self.kis_client.get_minute_candles(symbol, period_min=1, count=60)
            if not candles:
                logger.warning(f"[Intraday] 1분봉 빈 응답 {symbol} (장 마감 또는 API 오류)")
            return candles or []
        except Exception as e:
            logger.warning(f"[Intraday] 1분봉 조회 실패 {symbol}: {e}")
            return []

    def _get_current_price(self, symbol: str, state: dict) -> float:
        if self.kis_client is None:
            return state.get("current_price", 0.0)
        try:
            data = self.kis_client.get_current_price(symbol)
            if data:
                return float(data.get("current_price", 0) or 0)
        except Exception:
            pass
        return state.get("current_price", 0.0)

    # ── Buy Flag ───────────────────────────────────────────────────────────

    def _check_buy_flag(self, symbol: str, state: dict, candles_1m: list[dict]) -> tuple[bool, str]:
        now = datetime.now()

        # 시간 체크
        buy_start = self._parse_time(self.buy_start_time)
        buy_end = self._parse_time(self.buy_end_time)
        if not (buy_start <= now.time() < buy_end):
            return False, "outside_buy_window"

        # 진입 횟수 체크
        if self.total_entries_today >= self.max_total_entries_per_day:
            return False, "max_total_entries_reached"
        if state["entries_count"] >= self.max_entries_per_symbol:
            return False, "max_symbol_entries_reached"

        current_price = state.get("current_price", 0.0)
        if current_price <= 0:
            return False, "no_price_data"

        if len(candles_1m) < 5:
            return False, "insufficient_candle_data"

        # 3분봉 최소 5개 미만이면 지표 계산 불가
        candles_3m_pre = resample_1m_to_3m(candles_1m)
        if len(candles_3m_pre) < 5:
            logger.warning(
                f"[Intraday] 3분봉 부족 {symbol}: 1분봉={len(candles_1m)}개 "
                f"→ 3분봉={len(candles_3m_pre)}개 (최소 5개 필요)"
            )
            return False, "insufficient_3m_candles"

        # 표준 조건 체크
        ok, reason = self._standard_buy_check(symbol, state, candles_1m, current_price)
        if ok:
            return True, reason

        # 완화 조건 (10:00 이후, 당일 진입 0회)
        if now.hour >= 10 and self.total_entries_today == 0:
            ok2, reason2 = self._relaxed_buy_check(symbol, state, candles_1m, current_price)
            if ok2:
                return True, "relaxed_" + reason2

        # 돌파 진입
        if self.allow_breakout and self.breakout_entries_today < 1:
            ok3, reason3 = self._breakout_buy_check(symbol, state, candles_1m, current_price)
            if ok3:
                self.breakout_entries_today += 1
                return True, "breakout_" + reason3

        return False, reason or "no_buy_signal"

    def _standard_buy_check(self, symbol: str, state: dict, candles_1m: list[dict], current_price: float) -> tuple[bool, str]:
        vwap = calculate_vwap(candles_1m)
        if vwap > 0 and current_price <= vwap:
            return False, "below_vwap"

        candles_3m = resample_1m_to_3m(candles_1m)
        if len(candles_3m) >= 20:
            ema5 = calculate_ema(candles_3m, 5)
            ema20 = calculate_ema(candles_3m, 20)
            if ema5 and ema20 and ema5[0] <= ema20[0]:
                return False, "ema_reverse"

        intraday_high = max((c["high"] for c in candles_1m), default=current_price)
        pullback = calculate_intraday_high_pullback(current_price, intraday_high)
        if pullback < self.crash_threshold_pct:
            return False, "price_crashed"
        if not (self.min_pullback_pct <= pullback <= self.max_pullback_pct):
            return False, f"pullback_out_of_range({pullback:.2f}%)"

        if not detect_bullish_reversal_1m(candles_1m):
            return False, "no_bullish_reversal"

        latest_vol = candles_1m[0]["volume"]
        prior_vols = [c["volume"] for c in candles_1m[1:4]]
        avg_prior = sum(prior_vols) / len(prior_vols) if prior_vols else 0
        if avg_prior > 0 and latest_vol < avg_prior * self.min_volume_ratio:
            return False, "volume_insufficient"

        rsi = calculate_rsi(candles_1m)
        self.prev_rsi[symbol] = rsi
        if not (self.min_rsi <= rsi <= self.max_rsi):
            return False, f"rsi_out_of_range({rsi:.1f})"

        return True, "standard_buy"

    def _relaxed_buy_check(self, symbol: str, state: dict, candles_1m: list[dict], current_price: float) -> tuple[bool, str]:
        vwap = calculate_vwap(candles_1m)
        if vwap > 0 and current_price <= vwap:
            return False, "below_vwap"

        candles_3m = resample_1m_to_3m(candles_1m)
        if len(candles_3m) >= 20:
            ema5 = calculate_ema(candles_3m, 5)
            ema20 = calculate_ema(candles_3m, 20)
            if ema5 and ema20 and ema5[0] <= ema20[0]:
                return False, "ema_reverse"

        intraday_high = max((c["high"] for c in candles_1m), default=current_price)
        pullback = calculate_intraday_high_pullback(current_price, intraday_high)
        if pullback < self.relaxed_min_pullback:
            return False, "pullback_too_deep"

        latest_vol = candles_1m[0]["volume"]
        prior_vols = [c["volume"] for c in candles_1m[1:4]]
        avg_prior = sum(prior_vols) / len(prior_vols) if prior_vols else 0
        if avg_prior > 0 and latest_vol < avg_prior * self.relaxed_min_vol_ratio:
            return False, "volume_insufficient"

        rsi = calculate_rsi(candles_1m)
        if not (self.min_rsi <= rsi <= self.max_rsi):
            return False, f"rsi_out_of_range({rsi:.1f})"

        return True, "relaxed_buy"

    def _breakout_buy_check(self, symbol: str, state: dict, candles_1m: list[dict], current_price: float) -> tuple[bool, str]:
        vwap = calculate_vwap(candles_1m)
        if vwap > 0 and current_price <= vwap:
            return False, "below_vwap"

        candles_3m = resample_1m_to_3m(candles_1m)
        if len(candles_3m) >= 20:
            ema5 = calculate_ema(candles_3m, 5)
            ema20 = calculate_ema(candles_3m, 20)
            if ema5 and ema20 and ema5[0] <= ema20[0]:
                return False, "ema_reverse"

        intraday_high = max((c["high"] for c in candles_1m), default=current_price)
        pullback = calculate_intraday_high_pullback(current_price, intraday_high)
        if pullback < -0.5:
            return False, "not_near_high"

        rsi = calculate_rsi(candles_1m)
        if rsi > self.max_rsi:
            return False, "rsi_overbought"

        latest_vol = candles_1m[0]["volume"]
        prior_vols = [c["volume"] for c in candles_1m[1:4]]
        avg_prior = sum(prior_vols) / len(prior_vols) if prior_vols else 0
        if avg_prior > 0 and latest_vol < avg_prior * 1.0:
            return False, "volume_not_increasing"

        return True, "breakout"

    # ── Sell Flag ──────────────────────────────────────────────────────────

    def _check_sell_flag(self, symbol: str, state: dict, candles_1m: list[dict], current_price: float) -> tuple[str, str]:
        avg_buy = state.get("avg_buy_price", 0.0)
        if avg_buy <= 0 or current_price <= 0:
            return "", ""

        profit_rate = (current_price - avg_buy) / avg_buy * 100.0

        # 1. 손절
        if profit_rate <= self.stop_loss_pct:
            return "stop_loss", f"profit_rate={profit_rate:.2f}%"

        # 2. 강제청산
        now = datetime.now()
        force_time = self._parse_time(self.force_sell_time)
        if now.time() >= force_time:
            return "force_close", "force_sell_time_reached"

        # 3. 전량익절 (+3.2%)
        if profit_rate >= self.full_tp_pct:
            return "full_tp", f"profit_rate={profit_rate:.2f}%"

        # 4. 절반익절 (+1.8%, 1회만)
        if profit_rate >= self.half_tp_pct and not state.get("first_take_profit_done", False):
            return "half_tp", f"profit_rate={profit_rate:.2f}%"

        # 5. 트레일링 스탑 (고점 대비 -1.8%)
        highest = state.get("highest_price_after_entry", 0.0)
        if highest > 0:
            trail_rate = (current_price - highest) / highest * 100.0
            if trail_rate <= self.trailing_stop_pct:
                return "trailing_stop", f"trail_rate={trail_rate:.2f}%"

        if not candles_1m:
            return "", ""

        vwap = calculate_vwap(candles_1m)
        if vwap > 0 and current_price < vwap:
            return "vwap_break", f"price={current_price} vwap={vwap:.0f}"

        candles_3m = resample_1m_to_3m(candles_1m)
        if len(candles_3m) >= 20:
            ema5 = calculate_ema(candles_3m, 5)
            ema20 = calculate_ema(candles_3m, 20)
            if ema5 and ema20 and ema5[0] < ema20[0]:
                return "ema_cross", "ema5_below_ema20"

        cur_rsi = calculate_rsi(candles_1m)
        prev_rsi = self.prev_rsi.get(symbol, cur_rsi)
        self.prev_rsi[symbol] = cur_rsi
        if prev_rsi >= 75 and cur_rsi < prev_rsi:
            return "rsi_peak", f"rsi {prev_rsi:.1f}→{cur_rsi:.1f}"

        if detect_bearish_volume_candle_1m(candles_1m):
            return "bearish_candle", "bearish_volume_spike"

        return "", ""

    # ── 매수 실행 ──────────────────────────────────────────────────────────

    def _execute_buy(self, symbol: str, state: dict, current_price: float) -> dict:
        if current_price <= 0:
            return {"success": False, "reason": "no_price"}

        quantity = int(state["allocated_budget"] / current_price)
        if quantity < 1:
            return {"success": False, "reason": "qty_too_small"}

        # 실전모드 안전장치
        mode = getattr(self.broker, "mode", "dry_run")
        if mode == "real":
            safety = self._cfg._raw.get("safety", {})
            if not safety.get("enable_real_buy", False):
                return {"success": False, "reason": "real_buy_disabled"}

        try:
            result = self.broker.buy(symbol, quantity, int(current_price))
            success = result.get("success", False) if isinstance(result, dict) else False
            order_id = result.get("order_id", "") if isinstance(result, dict) else ""
        except Exception as e:
            logger.error(f"[Intraday] 매수 예외 {symbol}: {e}")
            state["status"] = STATUS_ERROR
            state["last_reason"] = str(e)
            return {"success": False, "reason": str(e)}

        if success:
            state["status"] = STATUS_HOLDING
            state["avg_buy_price"] = current_price
            state["position_quantity"] = quantity
            state["entries_count"] += 1
            state["last_buy_at"] = datetime.now().isoformat()
            state["highest_price_after_entry"] = current_price
            state["first_take_profit_done"] = False
            state["order_history"].append({"action": "buy", "price": current_price, "qty": quantity, "at": state["last_buy_at"]})
            self.total_entries_today += 1
            logger.info(f"[Intraday] 매수 성공 {symbol} {quantity}주 @{current_price:,}")
        else:
            state["status"] = STATUS_ERROR
            state["last_reason"] = "buy_failed"

        self.save_state()
        self._log_trade("buy", symbol, state.get("name", ""), quantity, current_price, "buy_flag", "", success, order_id, "")
        return {"success": success, "order_id": order_id, "quantity": quantity, "price": current_price}

    # ── 매도 실행 ──────────────────────────────────────────────────────────

    def _execute_sell(self, symbol: str, state: dict, sell_type: str, current_price: float) -> dict:
        pos_qty = state.get("position_quantity", 0)
        if pos_qty <= 0:
            return {"success": False, "reason": "no_position"}

        if sell_type == "half_tp":
            sell_qty = math.ceil(pos_qty * 0.5)
        else:
            sell_qty = pos_qty

        mode = getattr(self.broker, "mode", "dry_run")
        if mode == "real":
            safety = self._cfg._raw.get("safety", {})
            if not safety.get("enable_real_sell", False):
                return {"success": False, "reason": "real_sell_disabled"}

        try:
            result = self.broker.sell(symbol, sell_qty, int(current_price))
            success = result.get("success", False) if isinstance(result, dict) else False
            order_id = result.get("order_id", "") if isinstance(result, dict) else ""
        except Exception as e:
            logger.error(f"[Intraday] 매도 예외 {symbol}: {e}")
            state["last_reason"] = str(e)
            return {"success": False, "reason": str(e)}

        if success:
            avg_buy = state.get("avg_buy_price", current_price)
            pnl = (current_price - avg_buy) * sell_qty
            state["realized_pnl"] = state.get("realized_pnl", 0.0) + pnl
            state["last_sell_at"] = datetime.now().isoformat()
            state["order_history"].append({"action": "sell", "type": sell_type, "price": current_price, "qty": sell_qty, "at": state["last_sell_at"]})

            if sell_type == "half_tp":
                state["status"] = STATUS_HALF_SOLD
                state["first_take_profit_done"] = True
                state["position_quantity"] -= sell_qty
            else:
                state["position_quantity"] = 0
                cooldown_until = datetime.now() + timedelta(minutes=self.cooldown_minutes)
                state["cooldown_until"] = cooldown_until.isoformat()
                state["status"] = STATUS_COOLING

            logger.info(f"[Intraday] 매도 성공 {symbol} {sell_qty}주 @{current_price:,} [{sell_type}] PnL={pnl:+,.0f}")

        self.save_state()
        self._log_trade("sell", symbol, state.get("name", ""), sell_qty, current_price, sell_type, sell_type, success, order_id, "")
        return {"success": success, "order_id": order_id, "sell_type": sell_type, "quantity": sell_qty, "price": current_price}

    # ── 강제 전량 청산 ─────────────────────────────────────────────────────

    def _force_sell_all(self) -> list[dict]:
        results = []
        for sym, state in self.symbols_state.items():
            if state["status"] in (STATUS_HOLDING, STATUS_HALF_SOLD):
                price = state.get("current_price", 0.0)
                if price <= 0:
                    price = state.get("avg_buy_price", 1.0)
                result = self._execute_sell(sym, state, "force_close", price)
                results.append({"symbol": sym, "action": "force_sell", **result})
        return results

    # ── 상태 저장/복원 ─────────────────────────────────────────────────────

    def save_state(self) -> None:
        data = {
            "date": datetime.now().strftime("%Y%m%d"),
            "total_entries_today": self.total_entries_today,
            "breakout_entries_today": self.breakout_entries_today,
            "total_budget": self.total_budget,
            "symbols": self.symbols_state,
        }
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[Intraday] 상태 저장 실패: {e}")

    def load_state(self) -> None:
        if not self.state_file.exists():
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            today = datetime.now().strftime("%Y%m%d")
            if data.get("date") != today:
                return  # 다른 날 상태 파일 무시
            self.total_entries_today = data.get("total_entries_today", 0)
            self.breakout_entries_today = data.get("breakout_entries_today", 0)
            self.symbols_state = data.get("symbols", {})
            logger.info(f"[Intraday] 상태 복원: {len(self.symbols_state)}종목")
        except Exception as e:
            logger.warning(f"[Intraday] 상태 로드 실패: {e}")

    # ── 거래 로그 ──────────────────────────────────────────────────────────

    def _log_trade(self, action: str, symbol: str, name: str, qty: int, price: float, reason: str, sell_type: str, success: bool, order_id: str, error: str) -> None:
        try:
            write_header = not self.log_file.exists()
            with open(self.log_file, "a", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=_LOG_COLUMNS)
                if write_header:
                    writer.writeheader()
                writer.writerow({
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "action": action,
                    "symbol": symbol,
                    "name": name,
                    "quantity": qty,
                    "price": price,
                    "reason": reason,
                    "sell_type": sell_type,
                    "order_success": success,
                    "order_id": order_id,
                    "error": error,
                })
        except Exception as e:
            logger.warning(f"[Intraday] 로그 기록 실패: {e}")

    # ── 유틸 ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_time(time_str: str):
        """'HH:MM' 형식을 datetime.time으로 변환."""
        from datetime import time as dtime
        parts = time_str.split(":")
        return dtime(int(parts[0]), int(parts[1]))
