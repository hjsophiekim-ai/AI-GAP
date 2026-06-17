from app.models import Candidate
from app.config import get_config
from app.logger import logger
import pandas as pd
from pathlib import Path
from datetime import datetime


class Top15Selector:
    def __init__(self, cfg=None):
        self.cfg = cfg or get_config()

    def select(self, candidates: list[Candidate]) -> list[Candidate]:
        """
        Sort by final_score, apply sector diversification and additional filters,
        return up to max_positions candidates with ranks reassigned.
        """
        max_positions = self.cfg.trading.get("max_positions", 15)

        # Step 1: Sort by final_score descending
        sorted_candidates = sorted(candidates, key=lambda c: c.final_score, reverse=True)

        # Step 2: Apply additional filters — open_to_current_rate < -1.5% skip,
        # gap_rate > 15% move to back
        normal = []
        deprioritized = []
        for c in sorted_candidates:
            if c.open_to_current_rate < -1.5:
                # Skip entirely
                logger.debug(
                    f"[Top15] 제외 (시가대비 하락): {c.symbol} {c.name} "
                    f"open_to_current_rate={c.open_to_current_rate:.2f}%"
                )
                continue
            if c.gap_rate > 15.0:
                deprioritized.append(c)
            else:
                normal.append(c)

        # Recombine: normal first, then deprioritized (already sorted by score within each group)
        filtered = normal + deprioritized

        # Step 3: Apply sector diversification
        # Keep at most 3 stocks per sector in the top-15 window
        selected = []
        sector_counts: dict[str, int] = {}
        overflow = []

        for c in filtered:
            sector = getattr(c, "sector", "") or f"__unique_{c.symbol}__"
            # Unknown sector treats each stock as unique
            if not sector or sector.strip() == "":
                sector = f"__unique_{c.symbol}__"
            count = sector_counts.get(sector, 0)
            if count < 3:
                selected.append(c)
                sector_counts[sector] = count + 1
            else:
                overflow.append(c)

        # Fill remaining slots from overflow candidates (already sorted by score)
        remaining_slots = max_positions - len(selected)
        if remaining_slots > 0:
            selected.extend(overflow[:remaining_slots])

        # Step 4: Trim to max_positions
        result = selected[:max_positions]

        # Step 5: Reassign ranks 1..N
        for i, c in enumerate(result, start=1):
            c.rank = i

        names = [c.name for c in result]
        logger.info(f"Top 15 선정 완료: {names}")

        return result

    def select_from_csv(self, filepath: str) -> list[Candidate]:
        """Load candidates from CSV and return top 15."""
        path = Path(filepath)
        if not path.exists():
            logger.warning(f"[Top15] CSV 파일 없음: {filepath}")
            return []

        df = pd.read_csv(path, dtype={"symbol": str})
        candidates = self._df_to_candidates(df)
        return self.select(candidates)

    def save_top15(self, candidates: list[Candidate], date_str: str = None) -> str:
        """
        Save top15 candidates to data/selected/YYYYMMDD_top15.csv.
        Returns the filepath as string.
        """
        if date_str is None:
            date_str = datetime.now().strftime("%Y%m%d")

        save_dir = Path(__file__).parent.parent.parent / "data" / "selected"
        save_dir.mkdir(parents=True, exist_ok=True)
        filepath = save_dir / f"{date_str}_top15.csv"

        columns = [
            "rank", "symbol", "name", "current_price", "open", "high", "low",
            "previous_close", "gap_rate", "open_to_current_rate", "trade_value",
            "ml_score", "rule_score", "final_score", "selected_reason", "risk_comment",
        ]

        rows = []
        for c in candidates:
            rows.append({
                "rank": c.rank,
                "symbol": c.symbol,
                "name": c.name,
                "current_price": c.current_price,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "previous_close": c.previous_close,
                "gap_rate": c.gap_rate,
                "open_to_current_rate": c.open_to_current_rate,
                "trade_value": c.trade_value,
                "ml_score": c.ml_score,
                "rule_score": c.rule_score,
                "final_score": c.final_score,
                "selected_reason": c.selected_reason,
                "risk_comment": c.risk_comment,
            })

        df = pd.DataFrame(rows, columns=columns)
        df.to_csv(filepath, index=False, encoding="utf-8-sig")
        logger.info(f"[Top15] 저장 완료: {filepath}")
        return str(filepath)

    def load_top15(self, date_str: str = None) -> list[Candidate]:
        """
        Load top15 from data/selected/YYYYMMDD_top15.csv.
        Returns empty list if file not found.
        """
        if date_str is None:
            date_str = datetime.now().strftime("%Y%m%d")

        filepath = (
            Path(__file__).parent.parent.parent
            / "data"
            / "selected"
            / f"{date_str}_top15.csv"
        )

        if not filepath.exists():
            logger.warning(f"[Top15] 파일 없음: {filepath}")
            return []

        df = pd.read_csv(filepath, dtype={"symbol": str})
        return self._df_to_candidates(df)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _df_to_candidates(self, df: pd.DataFrame) -> list[Candidate]:
        """Convert a DataFrame row-set to a list of Candidate objects."""
        candidates = []
        for i, row in df.iterrows():
            try:
                c = Candidate(
                    rank=int(row.get("rank", i + 1)),
                    symbol=str(row.get("symbol", "")),
                    name=str(row.get("name", "")),
                    current_price=float(row.get("current_price", 0)),
                    open=float(row.get("open", 0)),
                    high=float(row.get("high", 0)),
                    low=float(row.get("low", 0)),
                    previous_close=float(row.get("previous_close", 0)),
                    gap_rate=float(row.get("gap_rate", 0)),
                    open_to_current_rate=float(row.get("open_to_current_rate", 0)),
                    trade_value=float(row.get("trade_value", 0)),
                    ml_score=float(row.get("ml_score", 0)),
                    rule_score=float(row.get("rule_score", 0)),
                    final_score=float(row.get("final_score", 0)),
                    selected_reason=str(row.get("selected_reason", "")),
                    risk_comment=str(row.get("risk_comment", "")),
                    exclude_reason=str(row.get("exclude_reason", "")),
                )
                candidates.append(c)
            except Exception as e:
                logger.warning(f"[Top15] 행 변환 오류 (row={i}): {e}")
        return candidates
