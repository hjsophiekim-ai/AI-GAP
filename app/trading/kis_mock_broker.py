"""
KisMockBroker - KIS 모의투자(paper trading) 계좌 브로커.

KISClient에 모든 API 호출을 위임합니다.
API 키/토큰은 절대 로그에 출력하지 않습니다.
"""

from app.trading.broker_base import BrokerBase
from app.models import OrderResult, Position
from app.logger import logger


class KisMockBroker(BrokerBase):
    """KIS 모의투자 계좌 브로커."""

    mode = "mock"

    def __init__(self, kis_client) -> None:
        self.kis = kis_client

    # ------------------------------------------------------------------
    # BrokerBase interface
    # ------------------------------------------------------------------

    def get_current_price(self, symbol: str) -> float | None:
        try:
            result = self.kis.get_current_price(symbol)
            return result["current_price"] if result else None
        except Exception as e:
            logger.warning("MOCK get_current_price 예외 %s: %s", symbol, e)
            return None

    def get_balance(self) -> float:
        try:
            result = self.kis.get_balance()
            return result.get("cash", 0.0)
        except Exception as e:
            logger.error("MOCK get_balance 예외: %s", e)
            return 0.0

    def get_buyable_cash(self) -> float:
        try:
            return self.kis.get_buyable_cash()
        except Exception as e:
            logger.error("MOCK get_buyable_cash 예외: %s", e)
            return 0.0

    def get_positions(self) -> list[Position]:
        result = self.kis.get_balance()
        if "error" in result:
            err = result["error"]
            logger.error("MOCK get_positions 잔고 조회 오류: %s", err)
            raise RuntimeError(f"KIS 모의계좌 잔고 조회 실패: {err}")
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
        logger.info("MOCK get_positions: %d 종목", len(positions))
        return positions

    def buy(
        self,
        symbol: str,
        name: str,
        quantity: int,
        price: float,
        order_type: str = "limit",
    ) -> OrderResult:
        try:
            result = self.kis.buy(symbol, quantity, int(price), order_type)
            return OrderResult(
                success=result["success"],
                mode=self.mode,
                account_type="mock",
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
            logger.error("MOCK buy 예외 %s: %s", symbol, e)
            return OrderResult(
                success=False, mode=self.mode, account_type="mock",
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
        try:
            result = self.kis.sell(symbol, quantity, int(price), order_type)
            return OrderResult(
                success=result["success"],
                mode=self.mode,
                account_type="mock",
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
            logger.error("MOCK sell 예외 %s: %s", symbol, e)
            return OrderResult(
                success=False, mode=self.mode, account_type="mock",
                symbol=symbol, name=name, side="sell",
                quantity=quantity, price=price, order_type=order_type,
                order_id="", message=str(e),
            )
