import csv
from pathlib import Path
from datetime import datetime

from app.models import BuyPlan, OrderResult, Position
from app.trading.broker_base import BrokerBase
from app.config import get_config
from app.logger import logger


class OrderManager:
    def __init__(self, broker: BrokerBase, cfg=None):
        self.broker = broker
        self.cfg = cfg or get_config()
        self.bought_symbols: set[str] = set()

    def execute_buy_plans(self, plans: list[BuyPlan]) -> list[OrderResult]:
        results: list[OrderResult] = []
        order_type = self.cfg.trading.get("order_type", "limit")

        for plan in plans:
            if plan.allocated_quantity < 1:
                logger.debug(f"[매수스킵] {plan.symbol} {plan.name}: allocated_quantity={plan.allocated_quantity}")
                continue

            symbol = plan.symbol

            if symbol in self.bought_symbols:
                logger.info(f"[매수중복스킵] {symbol} {plan.name}: 오늘 이미 매수됨")
                continue

            logger.info(
                f"[매수시도] {symbol} {plan.name} {plan.allocated_quantity}주 "
                f"@ {plan.current_price:,.0f}원 ({order_type})"
            )

            result = self.broker.buy(
                symbol=symbol,
                name=plan.name,
                quantity=plan.allocated_quantity,
                price=plan.current_price,
                order_type=order_type,
            )

            if result.success:
                self.bought_symbols.add(symbol)
                logger.info(
                    f"[매수성공] {symbol} {plan.name} {result.quantity}주 "
                    f"order_id={result.order_id}"
                )
            else:
                logger.warning(
                    f"[매수실패] {symbol} {plan.name}: {result.message}"
                )

            results.append(result)

        return results

    def execute_sell_all(
        self,
        positions: list[Position],
        prices: dict[str, float] = None,
    ) -> list[OrderResult]:
        results: list[OrderResult] = []
        order_type = self.cfg.trading.get("order_type", "limit")

        for position in positions:
            symbol = position.symbol
            price = (
                prices.get(symbol, position.current_price)
                if prices
                else position.current_price
            )

            logger.info(
                f"[매도시도] {symbol} {position.name} {position.quantity}주 "
                f"@ {price:,.0f}원 ({order_type})"
            )

            result = self.broker.sell(
                symbol=symbol,
                name=position.name,
                quantity=position.quantity,
                price=price,
                order_type=order_type,
            )

            if result.success:
                logger.info(
                    f"[매도성공] {symbol} {position.name} {result.quantity}주 "
                    f"order_id={result.order_id}"
                )
            else:
                logger.warning(
                    f"[매도실패] {symbol} {position.name}: {result.message}"
                )

            results.append(result)

        return results

    def execute_sell_partial(
        self,
        position: Position,
        quantity: int,
        price: float,
    ) -> OrderResult:
        order_type = self.cfg.trading.get("order_type", "limit")
        symbol = position.symbol

        logger.info(
            f"[부분매도시도] {symbol} {position.name} {quantity}주 "
            f"@ {price:,.0f}원 ({order_type})"
        )

        result = self.broker.sell(
            symbol=symbol,
            name=position.name,
            quantity=quantity,
            price=price,
            order_type=order_type,
        )

        if result.success:
            logger.info(
                f"[부분매도성공] {symbol} {position.name} {result.quantity}주 "
                f"order_id={result.order_id}"
            )
        else:
            logger.warning(
                f"[부분매도실패] {symbol} {position.name}: {result.message}"
            )

        return result

    def save_order_log(self, results: list[OrderResult], date_str: str = None) -> str:
        if date_str is None:
            date_str = datetime.now().strftime("%Y%m%d")

        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)

        file_path = log_dir / f"{date_str}_orders.csv"

        fieldnames = [
            "timestamp", "success", "mode", "account_type",
            "symbol", "name", "side", "quantity", "price",
            "order_type", "order_id", "message",
        ]

        write_header = not file_path.exists()

        with open(file_path, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            for result in results:
                row = result.to_dict()
                writer.writerow({k: row.get(k, "") for k in fieldnames})

        logger.info(f"주문 로그 저장: {file_path} ({len(results)}건)")
        return str(file_path)
