"""Quick check: 加载 .env 后打印关键变量（密钥脱敏）"""
from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

for k in [
    "LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY",
    "OTEL_SERVICE_NAME", "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_HEADERS", "OTEL_SEMCONV_STABILITY_OPT_IN",
    "OLLAMA_BASE_URL", "OLLAMA_MODEL", "AGENT_WORKSPACE", "ALLOWED_TOOLS",
]:
    v = os.getenv(k, "<unset>")
    if "KEY" in k or "SECRET" in k or "HEADERS" in k:
        v = "***" + v[-12:] if v != "<unset>" else "<unset>"
    print(f"  {k:34s} = {v}")
