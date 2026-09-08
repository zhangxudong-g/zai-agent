"""
Strands Agent Demo - 简化版
"""

# 加载项目根 .env（OTEL/Langfuse/Ollama 等配置都在里面）
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import os

# 无干扰模式配置
os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")  # 跳过确认

import logging

from strands import Agent

# --- OpenTelemetry trace（endpoint/auth 走 .env；不改代码就能在 Jaeger / Langfuse 之间切换） ---
from strands.telemetry import StrandsTelemetry

StrandsTelemetry().setup_otlp_exporter()  # 从 OTEL_EXPORTER_OTLP_* 环境变量读配置
# 或者不要后端，span 直接打终端（二选一，注释上面那行换这行）：
# StrandsTelemetry().setup_console_exporter()
# 让 OTel SDK 自己的 debug 日志安静一点（你下面全局开了 DEBUG/INFO）
logging.getLogger("opentelemetry").setLevel(logging.WARNING)

from strands.models.ollama import OllamaModel
from strands_tools import editor, file_read, file_write, http_request

# 设置日志记录
logging.getLogger("strands").setLevel(logging.INFO)  # INFO 级别以上才打印
# 设置日志记录格式和处理器
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)


# 创建 Ollama 模型
ollama_model = OllamaModel(
    host="http://localhost:11434",
    model_id="qwen3.8:27b",
)

# 可选工具
Tools = [file_read, file_write, editor, http_request]
# 创建简单的 Agent
agent = Agent(
    model=ollama_model,
    system_prompt="你是一个有用的助手。工具使用说明：\n"
    "1. file_read: 读取文件内容，注意编码 \n"
    "2. file_write: 写入文件内容，注意编码\n"
    "3. editor: 编辑文件内容\n"
    "4. http_request: 发送 HTTP 请求\n"
    "5. Shift-JIS: 处理 Shift-JIS 编码\n",
    tools=Tools,
    # callback_handler=None, # 如果需要回调处理器，可以在这里传入
    # callback_handler=callback_handler,
)

# 运行 Agent
# agent("读取当前目录下的文件列表，并将结果写入 output.txt 文件。")

# BatchSpanProcessor 默认攒着批量发，等 1 秒让后台线程把 span 刷到 Jaeger 再退出
import time

time.sleep(1)

# print("\n")

agent("大连明天的天气预报。")

# print("\n")
# agent(
#     "读取 GKBPA00010B.SQL 的并且生成一个技术wiki文档，要求内容详细，结构清晰，分点说明。"
# )

print("\n")
