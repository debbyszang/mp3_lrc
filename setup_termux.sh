#!/bin/bash
# ============================================================
#  Termux 一键配置脚本 - mp3_lrc (Android)
#  适用: Pura 70 Pro / arm64-v8a / HarmonyOS (Android 14+)
#  
#  用法:
#    bash setup.sh
#  
#  功能:
#    1. 自动安装 Python / ffmpeg / edge-tts
#    2. 创建工作目录 ~/mp3_lrc/
#    3. 提示放入文件后即可运行
# ============================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo ""
echo -e "${CYAN}============================================${NC}"
echo -e "${CYAN}   Termux 环境配置 - mp3_lrc 字幕生成工具${NC}"
echo -e "${CYAN}============================================${NC}"
echo ""

# ---------- Step 1: 更新包管理器 ----------
echo -e "${YELLOW}[1/4]${NC} 更新包管理器..."
pkg update -y 2>/dev/null || true
pkg upgrade -y 2>/dev/null || true

# ---------- Step 2: 安装依赖 ----------
echo -e "${YELLOW}[2/4]${NC} 安装 Python + ffmpeg..."
pkg install -y python ffmpeg 2>/dev/null | tail -1

# ---------- Step 3: 安装 Python 包 ----------
echo -e "${YELLOW}[3/4]${NC} 安装 edge-tts (TTS语音合成)..."
pip install --upgrade pip -q 2>/dev/null
pip install edge-tts -q 2>/dev/null

# ---------- Step 4: 创建工作目录 ----------
WORK_DIR="$HOME/mp3_lrc"
mkdir -p "$WORK_DIR"

echo ""
echo -e "${GREEN}✓ 环境安装完成！${NC}"
echo ""

# 检查是否已有文件
SCRIPT_COUNT=$(find "$WORK_DIR" -name "text_to_lrc.py" 2>/dev/null | wc -l)
TXT_COUNT=$(find "$WORK_DIR" -maxdepth 1 -name "*.txt" 2>/dev/null | wc -l)

if [ "$SCRIPT_COUNT" -eq 0 ]; then
    echo -e "${RED}⚠  下一步：请将以下文件复制到手机${NC}"
    echo -e "     工作目录: ${CYAN}$WORK_DIR${NC}"
    echo ""
    echo -e "     需要的文件:"
    echo -e "       ① ${CYAN}text_to_lrc.py${NC}   (主脚本)"
    echo -e "       ② ${CYAN}你的文本.txt${NC}       (要朗读的文本)"
    echo ""
    echo -e "     传输方式 (任选其一):"
    echo -e "       • 微信/QQ 发送文件 → 长按保存到 ${CYAN}$WORK_DIR${NC}"
    echo -e "       • USB 数据线连接电脑 → 复制到 ${CYAN}$WORK_DIR${NC}"
    echo -e "       • 用 Termux 的 ${CYAN}termux-share${NC} 接收"
else
    echo -e "${GREEN}✓ 已检测到脚本文件${NC}"
fi

echo ""
echo -e "${CYAN}--------------------------------------------${NC}"
echo -e "${YELLOW}  文件放好后，运行:${NC}"
echo ""
echo -e "  cd $WORK_DIR"
echo -e "  python3 text_to_lrc.py -t 你的文本名"
echo ""
echo -e "  示例: python3 text_to_lrc.py -t test_en"
echo -e "${CYAN}--------------------------------------------${NC}"

# 显示当前工作目录内容（如果有文件的话）
if [ "$(ls -A "$WORK_DIR" 2>/dev/null)" ]; then
    echo ""
    echo -e "${YELLOW}当前工作目录内容:${NC}"
    ls -la "$WORK_DIR"
fi

echo ""
