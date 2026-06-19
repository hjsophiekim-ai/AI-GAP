"""
KisRealBroker - KIS 실전투자 계좌 브로커.

SAFETY: 6가지 안전 조건.
  1. mode == "real"
  2. config.yaml kis.real.enabled == true  OR  runtime_real_mode == True
  3. config.yaml safety.enable_real_trading == true  OR  runtime_real_mode == True
  4. 확인 문구 "REAL_ORDER_CONFIRMED" 일치  (항상 필요)
  5. 매수 전: enable_real_buy == true  OR  runtime_real_mode == True  + 주문금액 한도
  6. 매도 전: enable_real_sell == true  OR  runtime_real_mode == True

gate 1~4: __init__에서 검사 → 브로커 자체를 못 만들게 차단
gate 5:   buy()에서 검사 → OrderResult(success=False) 반환
gate 6:   sell()에서 검사 → OrderResult(success=False) 반환

runtime_real_mode=True (UI 실전모드 버튼) 이면 gate 2~3 우회.
매수/매도 모두 runtime_real_mode가 True일 때만 실제 주문 가능.
"""

from app.trading.broker_base import BrokerBase
from app.models import OrderResult, Position
from app.logger import logger

_REAL_MODE_BLOCKED_MSG = (
    "실전모드가 활성화되어 있지 않습니다. "
    "실제 주문을 실행하려면 실전모드 버튼을 누르고 확인 문구를 입력하세요."
)


class KisRealBroker(BrokerBase):
    """KIS 실전투자 계좌 브로커."""

    mode = "real"

    def __init__(
        self,
        kis_client,
        cfg=None,
        confirm_text: str = "",
        runtime_real_mode: bool = False,
        runtime_enable_real_buy: bool = False,
        runtime_enable_real_sell: bool = False,
        **kwargs,
    ) -> None:
        from app.config import get_config
        self._cfg = cfg or get_config()
        self._runtime_real_mode = runtime_real_mode
        self._runtime_enable_real_buy = runtime_enable_real_buy
        self._runtime_enable_real_sell = runtime_enable_real_sell

        # gate 2: kis.real.enabled OR runtime_real_mode
        kis_cfg = self._cfg._raw.get("kis", {})
        real_section = kis_cfg.get("real", {})
        real_enabled = real_section.get("enabled", False)
        if not runtime_real_mode and not real_enabled:
            raise RuntimeError(
                "실전 계좌가 비활성화되어 있습니다. "
                "현재 kis.real.enabled=false 또는 실전모드 버튼이 활성화되지 않았습니다. "
                "실제 주문을 원하면 실전모드를 활성화하세요."
            )

        # gate 3: safety.enable_real_trading OR runtime_real_mode
        if not runtime_real_mode and not self._cfg.real_trading_enabled():
            raise RuntimeError(
                "실전투자 모드가 비활성화되어 있습니다. "
                "config.yaml의 safety.enable_real_trading을 true로 설정하거나 "
                "실전모드를 활성화하세요."
            )

        # gate 4: 확인 문구 (항상 필요)
        expected = self._cfg.real_confirm_text()
        if self._cfg.require_real_confirm() and confirm_text != expected:
            raise RuntimeError(
                f"실전투자 확인 문구가 틀립니다. '{expected}'를 정확히 입력하세요."
            )

        self.kis = kis_client
        self._daily_ordered_amount: float = 0.0

    # ------------------------------------------------------------------
    # 주문 금액 안전장치 (gate 5, 매수 전용)
    # ------------------------------------------------------------------

    def _check_order_limits(self, quantity: int, price: float) -> str | None:
        """금액 한도 확인. 위반 시 사유 문자열 반환, 통과 시 None."""
        safety = self._cfg.safety
        order_amt = quantity * price
        # 새 키 우선, 구 키 fallback
        max_order = float(
            safety.get("max_order_amount")
            or safety.get("max_real_order_amount", 1_000_000)
        )
        max_daily = float(
            safety.get("max_daily_order_amount")
            or safety.get("max_real_daily_budget", 1_000_000)
        )
        if order_amt > max_order:
            return f"주문금액 초과: {order_amt:,.0f}원 > 한도 {max_order:,.0f}원"
        if self._daily_ordered_amount + order_amt > max_daily:
            return (
                f"일일 한도 초과: "
                f"{self._daily_ordered_amount + order_amt:,.0f}원 > {max_daily:,.0f}원"
            )
        return None

    # ------------------------------------------------------------------
    # BrokerBase interface
    # ------------------------------------------------------------------

    def get_current_price(self, symbol: str) -> float | None:
        try:
            result = self.kis.get_current_price(symbol)
            return result["current_price"] if result else None
        except Exception as e:
            logger.warning("REAL get_current_price 예외 %s: %s", symbol, e)
            return None

    def get_balance(self) -> float:
        try:
            result = self.kis.get_balance()
            return result.get("cash", 0.0)
        except Exception as e:
            logger.error("REAL get_balance 예외: %s", e)
            return 0.0

    def get_buyable_cash(self) -> float:
        try:
            return self.kis.get_buyable_cash()
        except Exception as e:
            logger.error("REAL get_buyable_cash 예외: %s", e)
            return 0.0

    def get_positions(self) -> list[Position]:
        """잔고 조회. API 오류 시 RuntimeError 발생."""
        result = self.kis.get_balance()
        if "error" in result:
            err = result["error"]
            logger.error("REAL get_positions 잔고 조회 오류: %s", err)
            raise RuntimeError(f"KIS 실계좌 잔고 조회 실패: {err}")
        positions = []
        for item in (result.get("positions") or []):
            positions.append(
                Position(
                    symbol=item["symbol"],
                    name=item["name"],
                    quantity=item["quantity"],
                    avg_price=item["avg_price"],
                    current_price=item["current_price"],
                )
            )
        logger.info("REAL get_positions: %d 종목", len(positions))
        return positions

    def buy(
        self,
        symbol: str,
        name: str,
        quantity: int,
        price: float,
        order_type: str = "limit",
    ) -> OrderResult:
        # gate 5a: enable_real_buy OR runtime flags
        real_buy_ok = (
            self._runtime_real_mode
            or self._runtime_enable_real_buy
            or self._cfg.real_buy_enabled()
        )
        if not real_buy_ok:
            logger.warning("REAL 매수 차단 (실전모드 미활성화): %s", symbol)
            return OrderResult(
                success=False, mode=self.mode, account_type="real",
                symbol=symbol, name=name, side="buy",
                quantity=quantity, price=price, order_type=order_type,
                order_id="", message=_REAL_MODE_BLOCKED_MSG,
            )

        # gate 5b: 주문금액 한도
        limit_msg = self._check_order_limits(quantity, price)
        if limit_msg:
            logger.warning("REAL 매수 차단: %s", limit_msg)
            return OrderResult(
                success=False, mode=self.mode, account_type="real",
                symbol=symbol, name=name, side="buy",
                quantity=quantity, price=price, order_type=order_type,
                order_id="", message=f"real trading blocked by safety rule: {limit_msg}",
            )

        logger.info(
            "REAL BUY: symbol=%s name=%s quantity=%d price=%s order_type=%s",
            symbol, name, quantity, price, order_type,
        )
        try:
            result = self.kis.buy(symbol, quantity, int(price), order_type)
            if result["success"]:
                self._daily_ordered_amount += quantity * price
            return OrderResult(
                success=result["success"],
                mode=self.mode,
                account_type="real",
                symbol=symbol,
                name=name,
                side="buy",
                quantity=quantity,
                price=price,
                order_type=order_type,
                order_id=result.get("order_id", ""),
                message=result.get("message", ""),
                raw=result.get("raw", {}),
            )
        except Exception as e:
            logger.error("REAL buy 예외 %s: %s", symbol, e)
            return OrderResult(
                success=False, mode=self.mode, account_type="real",
                symbol=symbol, name=name, side="buy",
                quantity=quantity, price=price, order_type=order_type,
                order_id="", message=str(e),
            )

    def sell(
        self,
        symbol: str,
        name: str,
        quantity: int,
        price: float,
        order_type: str = "limit",
    ) -> OrderResult:
        # gate 6: enable_real_sell OR runtime flags
        real_sell_ok = (
            self._runtime_real_mode
            or self._runtime_enable_real_sell
            or self._cfg.real_sell_enabled()
        )
        if not real_sell_ok:
            logger.warning("REAL 매도 차단 (실전모드 미활성화): %s", symbol)
            return OrderResult(
                success=False, mode=self.mode, account_type="real",
                symbol=symbol, name=name, side="sell",
                quantity=quantity, price=price, order_type=order_type,
                order_id="", message=_REAL_MODE_BLOCKED_MSG,
            )

        logger.info(
            "REAL SELL: symbol=%s quantity=%d price=%s", symbol, quantity, price
        )
        try:
            result = self.kis.sell(symbol, quantity, int(price), order_type)
            return OrderResult(
                success=result["success"],
                mode=self.mode,
                account_type="real",
                symbol=symbol,
                name=name,
                side="sell",
                quantity=quantity,
                price=price,
                order_type=order_type,
                order_id=result.get("order_id", ""),
                message=result.get("message", ""),
                raw=result.get("raw", {}),
            )
        except Exception as e:
            logger.error("REAL sell 예외 %s: %s", symbol, e)
            return OrderResult(
                success=False, mode=self.mode, account_type="real",
                symbol=symbol, name=name, side="sell",
                quantity=quantity, price=price, order_type=order_type,
                order_id="", message=str(e),
            )
