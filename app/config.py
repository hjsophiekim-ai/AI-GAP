import os
from pathlib import Path
import yaml
from dotenv import load_dotenv

_ROOT = Path(__file__).parent.parent
load_dotenv(_ROOT / ".env")

_CONFIG_PATH = _ROOT / "config.yaml"


def _load_yaml() -> dict:
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
        return self.safety.get("enable_real_trading", False)

    def require_real_confirm(self) -> bool:
        return self.safety.get("require_real_confirm", True)

    def real_confirm_text(self) -> str:
        return self.safety.get("real_confirm_text", "I_UNDERSTAND_REAL_TRADING_RISK")


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
    account_no = os.getenv(account_no_env, "")
    product_code = os.getenv(product_code_env, "01")

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
