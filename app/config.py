import os
from pathlib import Path
import yaml
from dotenv import load_dotenv

_ROOT = Path(__file__).parent.parent
load_dotenv(_ROOT / ".env")

_CONFIG_PATH = _ROOT / "config.yaml"

_DEFAULT_CONFIG = {
    "mode": "dry_run",
    "trading": {
        "total_budget": 10000000,
        "max_positions": 15,
        "max_shares_per_stock": 2,
        "min_gap_rate": 1.0,
        "max_gap_rate": 20.0,
        "min_trade_value": 300000000,
        "buy_start_time": "09:05",
        "buy_end_time": "09:10",
        "first_take_profit_rate": 3.0,
        "second_take_profit_rate": 5.0,
        "stop_loss_rate": -1.5,
        "bulk_sell_1150_time": "11:50",
        "force_sell_time": "13:00",
        "emergency_sell_time": "15:10",
        "order_type": "limit",
        "allow_market_order": False,
        "min_price": 1000,
    },
    "filters": {
        "exclude_etf": True,
        "exclude_etn": True,
        "exclude_preferred_stock": True,
        "exclude_spac": True,
        "exclude_reit": True,
        "exclude_warning_stock": True,
        "exclude_halt": True,
        "min_price": 1000,
        "max_spread_rate": 1.0,
    },
    "data_source": {
        "pre_market_primary": "naver",
        "regular_market_primary": "kis",
        "secondary": "naver",
        "use_naver_gap_tab": True,
        "use_naver_volume_tab": True,
        "market_open_time": "09:00",
    },
    "naver": {"sise_url": "https://finance.naver.com/sise/"},
    "kis": {
        "real": {
            "enabled": False,
            "app_key_env": "KIS_REAL_APP_KEY",
            "app_secret_env": "KIS_REAL_APP_SECRET",
            "account_no_env": "KIS_ACCOUNT_NO",
            "account_product_code_env": "KIS_ACCOUNT_PRODUCT_CODE",
            "product_code_env": "KIS_ACCOUNT_PRODUCT_CODE",
            "base_url": "https://openapi.koreainvestment.com:9443",
        },
        "mock": {
            "enabled": True,
            "app_key_env": "KIS_MOCK_APP_KEY",
            "app_secret_env": "KIS_MOCK_APP_SECRET",
            "account_no_env": "KIS_MOCK_ACCOUNT_NO",
            "account_product_code_env": "KIS_MOCK_ACCOUNT_PRODUCT_CODE",
            "product_code_env": "KIS_MOCK_ACCOUNT_PRODUCT_CODE",
            "base_url": "https://openapivts.koreainvestment.com:29443",
        },
    },
    "dart": {
        "enabled": True,
        "api_key_env": "DART_API_KEY",
        "lookback_days": 7,
        "use_disclosure_score": True,
        "disclosure_score_weight": 0.10,
        "max_positive_bonus": 10,
        "max_negative_penalty": -20,
        "exclude_severe_risk_disclosure": True,
    },
    "ml": {
        "use_model": True,
        "fallback_to_rule_score": True,
        "model_path": "models/gap_model.pkl",
        "feature_importance_path": "models/feature_importance.csv",
        "min_training_rows": 500,
        "ml_weight": 0.6,
        "rule_weight": 0.4,
    },
    "safety": {
        "enable_real_trading": False,
        "enable_real_buy": False,
        "enable_real_sell": False,
        "require_real_order_confirm_text": True,
        "real_order_confirm_text": os.getenv("REAL_ORDER_CONFIRM_TEXT", "REAL_ORDER_CONFIRMED"),
        "max_order_amount": 1000000,
        "max_daily_order_amount": 3000000,
        "max_daily_loss_rate": -5.0,
        "require_real_confirm": True,
        "real_confirm_text": os.getenv("REAL_ORDER_CONFIRM_TEXT", "REAL_ORDER_CONFIRMED"),
        "max_real_order_amount": 1000000,
        "max_real_daily_budget": 3000000,
    },
    "volume_spike": {
        "enabled": True,
        "source_url": "https://finance.naver.com/sise/sise_quant_high.naver",
        "target_top_n": 10,
        "min_price": 20000,
        "min_change_rate": 3.0,
        "max_change_rate": 18.0,
        "min_trading_value": 3000000000,
        "fallback_min_trading_value": 1000000000,
        "fallback_min_price": 10000,
        "exclude_etf": True,
        "exclude_etn": True,
        "exclude_preferred": True,
        "exclude_spac": True,
        "exclude_reit": True,
        "exclude_suspended": True,
        "quality_stock_preference": True,
        "max_candidates_to_score": 80,
    },
    "logging": {"save_csv": True, "save_db": False, "level": "INFO", "log_dir": "logs"},
    "auto_sell": {
        "enabled": False,
        "check_interval_seconds": int(os.getenv("AUTO_SELL_CHECK_INTERVAL_SECONDS", 10)),
        "market_start": "09:00",
        "market_end": "15:20",
        "first_take_profit_rate": float(os.getenv("AUTO_SELL_FIRST_TP_RATE", 3.0)),
        "first_take_profit_sell_ratio": float(os.getenv("AUTO_SELL_FIRST_TP_RATIO", 0.5)),
        "final_take_profit_rate": float(os.getenv("AUTO_SELL_FINAL_TP_RATE", 5.0)),
        "final_take_profit_sell_ratio": float(os.getenv("AUTO_SELL_FINAL_TP_RATIO", 1.0)),
        "order_type": "market",
        "prevent_duplicate_orders": True,
        "require_real_mode": True,
        "save_state": True,
        "state_file": "data/state/auto_sell_state.json",
        "log_file": "data/logs/auto_sell_orders.csv",
    },
    "candidate_quality_filters": {
        "enabled": True,
        "speed_mode": True,
        "relaxed_mode": True,
        "target_min_candidates": 10,
        "target_top_n": 15,
        "min_price": 1000,
        "absolute_min_trading_value": 300000000,
        "min_trading_value_general": 700000000,
        "min_trading_value_0920": 1000000000,
        "healthy_gap_min": 1.0,
        "healthy_gap_max": 9.0,
        "caution_gap_max": 15.0,
        "hard_exclude_gap_rate": 20.0,
        "caution_gap_rate": 7.0,
        "max_open_gap_rate": 12.0,
        "max_3d_return": 25.0,
        "max_5d_return": 35.0,
        "max_intraday_drop_from_high": 4.0,
        "max_ma20_extension_rate": 15.0,
        "max_same_theme_in_top15": 5,
        "max_same_subtheme_in_top15": 4,
        "max_candidates_for_heavy_filters": 30,
    },
}


def _load_yaml() -> dict:
    if not _CONFIG_PATH.exists():
        import logging
        logging.getLogger(__name__).warning(
            "config.yaml not found at %s — using safe defaults.", _CONFIG_PATH
        )
        return _DEFAULT_CONFIG.copy()
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class Config:
    def __init__(self):
        self._raw = _load_yaml()

    def get(self, *keys, default=None):
        node = self._raw
        for k in keys:
            if not isinstance(node, dict):
                return default
            node = node.get(k, default)
            if node is None:
                return default
        return node

    @property
    def mode(self) -> str:
        return self._raw.get("mode", "dry_run")

    @property
    def trading(self) -> dict:
        return self._raw.get("trading", {})

    @property
    def filters(self) -> dict:
        return self._raw.get("filters", {})

    @property
    def data_source(self) -> dict:
        return self._raw.get("data_source", {})

    @property
    def ml(self) -> dict:
        return self._raw.get("ml", {})

    @property
    def safety(self) -> dict:
        return self._raw.get("safety", {})

    @property
    def logging_cfg(self) -> dict:
        return self._raw.get("logging", {})

    @property
    def dart(self) -> dict:
        return self._raw.get("dart", {})

    def real_trading_enabled(self) -> bool:
        return bool(self.safety.get("enable_real_trading", False))

    def real_buy_enabled(self) -> bool:
        return bool(self.safety.get("enable_real_buy", False))

    def real_sell_enabled(self) -> bool:
        return bool(self.safety.get("enable_real_sell", False))

    def require_real_confirm(self) -> bool:
        """새 키 우선, 구 키 fallback."""
        val = self.safety.get("require_real_order_confirm_text")
        if val is None:
            val = self.safety.get("require_real_confirm", True)
        return bool(val)

    def real_confirm_text(self) -> str:
        """새 키 우선, 구 키 fallback."""
        return (
            self.safety.get("real_order_confirm_text")
            or self.safety.get("real_confirm_text", "REAL_ORDER_CONFIRMED")
        )


def get_kis_account_config(mode: str) -> dict:
    """
    Returns KIS account credentials for the given mode ('mock' or 'real').
    Reads values from environment variables — never returns raw key values in logs.
    Raises ValueError with a descriptive message (not the key values) if required vars are missing.
    """
    cfg = get_config()
    kis_cfg = cfg._raw.get("kis", {})

    if mode == "mock":
        section = kis_cfg.get("mock", {})
    elif mode == "real":
        section = kis_cfg.get("real", {})
    else:
        raise ValueError(f"Unknown KIS mode: {mode}. Use 'mock' or 'real'.")

    app_key_env = section.get("app_key_env", "")
    app_secret_env = section.get("app_secret_env", "")
    account_no_env = section.get("account_no_env", "")
    product_code_env = section.get("product_code_env", "")

    app_key = os.getenv(app_key_env, "")
    app_secret = os.getenv(app_secret_env, "")
    account_no = os.getenv(account_no_env, "").strip()
    product_code = os.getenv(product_code_env, "").strip()

    # 계좌번호 정규화: KIS API는 CANO=8자리, ACNT_PRDT_CD=2자리 분리 요구
    # 사용자가 "12345678-01" 또는 "1234567801" 로 입력한 경우 자동 분리
    if account_no and "-" in account_no:
        parts = account_no.split("-", 1)
        account_no = parts[0].strip()
        if len(parts) > 1 and not product_code:
            product_code = parts[1].strip().zfill(2)
    elif account_no and len(account_no) == 10 and account_no.isdigit():
        if not product_code:
            product_code = account_no[8:]
        account_no = account_no[:8]

    if not product_code:
        product_code = "01"

    missing = []
    if not app_key:
        missing.append(app_key_env)
    if not app_secret:
        missing.append(app_secret_env)
    if not account_no:
        missing.append(account_no_env)

    if missing:
        raise ValueError(f"필수 환경변수 누락: {', '.join(missing)}")

    return {
        "app_key": app_key,
        "app_secret": app_secret,
        "account_no": account_no,
        "product_code": product_code or "01",
        "base_url": section.get("base_url", ""),
        "mode": mode,
        "enabled": section.get("enabled", False),
    }


def get_dart_api_key() -> str:
    """Returns DART API key from environment. Returns empty string if not set."""
    cfg = get_config()
    key_env = cfg.dart.get("api_key_env", "DART_API_KEY")
    return os.getenv(key_env, "")


_instance: "Config | None" = None


def get_config() -> Config:
    global _instance
    if _instance is None:
        _instance = Config()
    return _instance


def reload_config() -> Config:
    global _instance
    _instance = Config()
    return _instance
