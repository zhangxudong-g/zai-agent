#!/bin/bash
# 完全卸载 zai-agent，包括用户数据目录

set -e

echo "正在卸载 zai-agent..."

# 卸载 pip 包
pip uninstall zai-agent -y 2>/dev/null || true

# 删除用户数据目录
if [ -d "$HOME/.zai" ]; then
    echo "删除用户数据目录: $HOME/.zai"
    rm -rf "$HOME/.zai"
fi

echo "卸载完成！"
