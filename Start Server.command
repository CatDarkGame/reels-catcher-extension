#!/bin/bash
# reels-catcher-extension 로컬 서버 실행 스크립트 (macOS)
# 이 파일이 있는 디렉토리를 기준으로 venv를 자동 탐색합니다.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Python venv 탐색 순서:
#   1) extension 내부 .venv (독립 설치)
#   2) 상위 디렉토리의 reels-catcher/.venv (레거시 공유 venv)
if [ -f "$SCRIPT_DIR/.venv/bin/python3" ]; then
    PYTHON="$SCRIPT_DIR/.venv/bin/python3"
    WORK_DIR="$SCRIPT_DIR"
elif [ -f "$SCRIPT_DIR/../reels-catcher/.venv/bin/python3" ]; then
    PYTHON="$SCRIPT_DIR/../reels-catcher/.venv/bin/python3"
    WORK_DIR="$SCRIPT_DIR/../reels-catcher"
else
    echo "❌ Python venv를 찾을 수 없습니다."
    echo "   README의 설치 방법을 참고해 가상환경을 만든 뒤 다시 실행하세요."
    read -rp "Press Enter to exit..."
    exit 1
fi

echo "🐍 Python: $PYTHON"
echo "📂 작업 디렉토리: $WORK_DIR"
cd "$WORK_DIR"
"$PYTHON" "$SCRIPT_DIR/local_server.py"
