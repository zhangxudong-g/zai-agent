"""OpenTelemetry 接入（自动从 .env 启用）。

设计原则：
- **幂等**：多次调用 setup() 不会创建多个 exporter。
- **零依赖默认**：环境变量不配就不做任何事（不影响现有运行）。
- **统一入口**：所有使用 strands_poc.agent 的脚本（main / tests / cli）都会自动获得遥测。

激活方式（任一即可，按优先级）：
  1. ``OTEL_EXPORTER_OTLP_ENDPOINT=http://host:4318`` → OTLP HTTP 导出
     （Langfuse：``http://localhost:3000/api/public/otel`` + ``Basic`` 前缀）
  2. ``STRANDS_TELEMETRY=console``  →  span 直接打终端（零依赖调试）
  3. 都不设 → no-op（保持向后兼容）
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["exporter_kind", "is_active", "setup"]

_active: bool = False
_kind: str = ""


def _project_root() -> Path:
    """Return the project root directory (containing ``pyproject.toml``).

    Walks up from this file until it finds the marker. Falls back to
    ``Path.cwd()`` if not found (e.g. when the package is vendored).
    """
    here = Path(__file__).resolve().parent
    for p in (here, *here.parents):
        if (p / "pyproject.toml").is_file():
            return p
    return Path.cwd()


def _ensure_dotenv_loaded() -> None:
    """Load project-root .env (idempotent — load_dotenv won't overwrite existing vars)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return  # 没装 dotenv 也不致命；用户可能用别的机制设环境变量
    env_path = _project_root() / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=False)


def setup(force: bool = False) -> str:
    """根据环境变量配置 OpenTelemetry 导出。

    Args:
        force: 若为 True，即使已激活也重跑（用于测试场景）。

    Returns:
        exporter 类型（"otlp" / "console" / ""）。空串表示 no-op。
    """
    global _active, _kind

    if _active and not force:
        return _kind

    _ensure_dotenv_loaded()

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    mode = os.getenv("STRANDS_TELEMETRY", "").strip().lower()

    try:
        from strands.telemetry import StrandsTelemetry
    except ImportError:
        logger.debug("strands.telemetry 不可用，跳过 OTel 接入")
        return _kind

    if endpoint:
        try:
            StrandsTelemetry().setup_otlp_exporter()
            _kind = "otlp"
            _active = True
            logger.info(
                "OTel 已启用 -> OTLP %s (service=%s)",
                endpoint,
                os.getenv("OTEL_SERVICE_NAME", "strands-agents"),
            )
        except Exception as e:
            logger.warning("OTel OTLP exporter 初始化失败: %s", e)
        return _kind

    if mode == "console":
        try:
            StrandsTelemetry().setup_console_exporter()
            _kind = "console"
            _active = True
            logger.info("OTel 已启用 -> console exporter")
        except Exception as e:
            logger.warning("OTel console exporter 初始化失败: %s", e)
        return _kind

    # 不配就不做任何事（向后兼容）
    return _kind


def is_active() -> bool:
    """是否已经激活遥测（任意类型）。"""
    return _active


def exporter_kind() -> str:
    """返回当前激活的 exporter 类型（"otlp" / "console" / ""）。"""
    return _kind
