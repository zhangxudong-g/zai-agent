"""Verify telemetry auto-activates on strands_poc.agent import."""
import strands_poc.agent  # noqa: F401  -- 触发模块级 telemetry.setup()

from strands_poc import telemetry

print(f"telemetry active: {telemetry.is_active()}")
print(f"exporter kind:    {telemetry.exporter_kind()!r}")

import os
print(f"endpoint env:     {os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT', '<unset>')}")
print(f"service env:      {os.getenv('OTEL_SERVICE_NAME', '<unset>')}")
