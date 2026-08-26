import logging

from strands import Agent
from strands.models.ollama import OllamaModel

# 设置日志记录
logging.getLogger("strands").setLevel(logging.INFO)
# 设置日志记录格式和处理器
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
# 创建 Ollama 模型
ollama_model = OllamaModel(
    host="http://swiftechie.aa0.netvolante.jp:51434",
    model_id="qwen3.8:27b",
)


agent = Agent(system_prompt="You are a helpful assistant", model=ollama_model)
agent("1+8?")

print("\n")
# Take a snapshot
snapshot = agent.take_snapshot(preset="session")

# Continue the conversation
agent("Tell me a joke")
print("\n")
agent("Tell me another one")
print("\n")

# Restore to the earlier state
print("================")
agent.load_snapshot(snapshot)
print("================")

# The agent is back to the state after "Hello!"
print((agent.messages))  # Only the messages from before the jokes
