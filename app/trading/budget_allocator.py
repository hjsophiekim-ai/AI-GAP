from app.models import Candidate, BuyPlan
from app.config import get_config
from app.logger import logger
import pandas as pd
from pathlib import Path
from datetime import datetime


class BudgetAllocator:
    def __init__(self, cfg=None):
        self.cfg = cfg or get_config()

    def allocate(self, candidates: list, total_budget: float = None, max_shares: int = None) -> list:
        trading = self.cfg.trading

        if total_budget is None:
            total_budget = float(trading.get("total_budget", 0))
        if max_shares is None:
            max_shares = int(trading.get("max_shares_per_stock", 2))

        remaining_budget = total_budget
        allocations = {c.symbol: 0 for c in candidates}

        for round_num in range(1, max_shares + 1):
            for candidate in candidates:
                symbol = candidate.symbol
                price = candidate.current_price
                if allocations[symbol] >= max_shares:
                    continue
                if price > remaining_budget:
                    continue
                allocations[symbol] += 1
                remaining_budget -= price

        plans = []
        cumulative_used = 0.0
        for candidate in candidates:
            qty = allocations[candidate.symbol]
            amount = qty * candidate.current_price
            cumulative_used += amount
            status = "배분완료" if qty > 0 else "예산부족"

            if status != "배분완료":
                continue

            plan = BuyPlan(
                rank=candidate.rank,
                symbol=candidate.symbol,
                name=candidate.name,
                current_price=candidate.current_price,
                allocated_quantity=qty,
                allocated_amount=amount,
                remaining_budget_after=total_budget - cumulative_used,
                allocation_round=max_shares,
                allocation_status=status,
            )
            plans.append(plan)

        total_used = total_budget - remaining_budget
        logger.info(
            f"예산배분 완료: {len(plans)}개 종목, 총 {total_used:,.0f}원 사용, 잔여 {remaining_budget:,.0f}원"
        )

        return plans

    def save_buy_plan(self, plans: list, date_str: str = None) -> str:
        if date_str is None:
            date_str = datetime.now().strftime("%Y%m%d")

        output_dir = Path("data/orders")
        output_dir.mkdir(parents=True, exist_ok=True)

        file_path = output_dir / f"{date_str}_buy_plan.csv"

        rows = []
        for plan in plans:
            rows.append({
                "rank": plan.rank,
                "symbol": plan.symbol,
                "name": plan.name,
                "current_price": plan.current_price,
                "allocated_quantity": plan.allocated_quantity,
                "allocated_amount": plan.allocated_amount,
                "remaining_budget_after": plan.remaining_budget_after,
                "allocation_round": plan.allocation_round,
                "allocation_status": plan.allocation_status,
            })

        df = pd.DataFrame(rows, columns=[
            "rank", "symbol", "name", "current_price",
            "allocated_quantity", "allocated_amount",
            "remaining_budget_after", "allocation_round", "allocation_status",
        ])

        df.to_csv(file_path, index=False, encoding="utf-8-sig")
        logger.info(f"매수 계획 저장: {file_path}")

        return str(file_path)
