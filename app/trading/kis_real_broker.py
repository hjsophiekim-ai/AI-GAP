"""
KisRealBroker - KIS 실전투자 계좌 브로커.

SAFETY: 주문 전 6가지 안전 조건을 모두 통과해야 합니다.
  1. mode == "real"
  2. config.yaml kis.real.enabled == true
  3. config.yaml safety.enable_real_trading == true
  4. 사용자가 real_confirm_text를 정확히 입력
  5. 주문금액 <= max_real_order_amount
  6. 일일 총 주문금액 <= max_real_daily_budget

하나라도 실패하면 주문 차단 (RuntimeError 또는 success=False OrderResult 반환).
API 키/토큰은 절대 로그에 출력하지 않습니다.
"""

from app.trading.broker_base import BrokerBase
from app.models import OrderResult, Position
from app.logger import logger


class KisRealBroker(BrokerBase):
    """KIS 실전투자 계좌 브로커."""

    mode = "real"

    def __init__(self, kis_client, cfg=None, confirm_text: str = "") -> None:
        from app.config import get_config
        self._cfg = cfg or get_config()

        # Safety gate 1+2+3: config 플래그 확인
        kis_cfg = self._cfg._raw.get("kis", {})
        real_section = kis_cfg.get("real", {})
        if not real_section.get("enabled", False):
            raise RuntimeError(
                "KIS real 계좌가 비활성화되어 있습니다. "
                "config.yaml의 kis.real.enabled를 true로 설정하세요."
            )
        if not self._cfg.real_trading_enabled():
            raise RuntimeError(
                "실전투자 모드가 비활성화되어 있습니다. "
                "config.yaml의 safety.enable_real_trading을 true로 설정하세요."
            )

        # Safety gate 4: 확인 문구
        expected = self._cfg.real_confirm_text()
        if self._cfg.require_real_confirm() and confirm_text != expected:
            raise RuntimeError(
                f"실전투자 안전 확인 문구가 틀립니다. '{expected}'를 정확히 입력하세요."
            )

        self.kis = kis_client
        self._daily_ordered_amount: float = 0.0

    # ------------------------------------------------------------------
    # 주문 금액 안전장치 (gate 5+6)
    # ------------------------------------------------------------------

    def _check_order_limits(self, quantity: int, price: float) -> str | None:
        """금액 한도 확인. 위반 시 사유 문자열 반환, 통과 시 None."""
        safety = self._cfg.safety
        order_amt = quantity * price
        max_order = float(safety.get("max_real_order_amount", 1_000_000))
        max_daily = float(safety.get("max_real_daily_budget", 1_000_000))
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
        try:
            result = self.kis.get_balance()
            positions = []
            for item in result.get("positions", []):
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
        except Exception as e:
            logger.error("REAL get_positions 예외: %s", e)
            return []

    def buy(
        self,
        symbol: str,
        name: str,
        quantity: int,
        price: float,
        order_type: str = "limit",
    ) -> OrderResult:
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
