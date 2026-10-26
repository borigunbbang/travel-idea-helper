#!/bin/zsh
# 실행 화면 캡처: ./submission/shoot.sh <저장 이름> <주소 해시> [폭] [높이]
# 예: ./submission/shoot.sh 01-review "p_663fc14a2e" 1280 1800
name=$1; hash=$2; w=${3:-1280}; h=${4:-1800}
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars \
  --window-size=$w,$h --virtual-time-budget=10000 \
  --screenshot="$(dirname $0)/screens/$name.png" "http://127.0.0.1:8765/#$hash" 2>/dev/null
echo "$(dirname $0)/screens/$name.png"
