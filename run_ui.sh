#!/bin/bash

# CoE-Backend UI 실행 스크립트
# .venv 가상환경을 활성화하고 Streamlit UI를 실행합니다.

set -e  # 에러 발생 시 스크립트 중단

VENV_DIR="./.venv"
ENV_FILE="./.env"
REQUIREMENTS_FILE="requirements.in"
INSTALLED_MARKER="$VENV_DIR/.installed"
REQUIREMENTS_HASH_FILE="$VENV_DIR/.requirements_hash"

echo "🎨 CoE-Backend Streamlit UI 시작 준비 중..."

# .env 파일 존재 확인
if [ ! -f "$ENV_FILE" ]; then
    echo "❌ .env 파일이 존재하지 않습니다."
    echo "📝 .env.example 파일을 복사하여 .env 파일을 생성하세요:"
    echo "   cp .env.example .env"
    echo "   nano .env  # 또는 원하는 에디터로 편집"
    exit 1
fi

# 가상환경 존재 확인 및 생성
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 가상환경이 존재하지 않습니다. 새로 생성합니다..."
    python3 -m venv "$VENV_DIR"
    echo "✅ 가상환경 생성 완료"
fi

# 가상환경 활성화
echo "🔄 가상환경 활성화 중..."
source "$VENV_DIR/bin/activate"

# 의존성 설치/업데이트 (uv 사용)
CURRENT_HASH=$(shasum "$REQUIREMENTS_FILE" 2>/dev/null | awk '{print $1}')
PREVIOUS_HASH=""
if [ -f "$REQUIREMENTS_HASH_FILE" ]; then
    PREVIOUS_HASH=$(cat "$REQUIREMENTS_HASH_FILE")
fi

if [ ! -f "$INSTALLED_MARKER" ] || [ "$CURRENT_HASH" != "$PREVIOUS_HASH" ]; then
    echo "📚 의존성 설치/업데이트 중 (uv 사용)..."
    pip install uv
    HNSWLIB_NO_NATIVE=1 uv pip install -r "$REQUIREMENTS_FILE"
    touch "$INSTALLED_MARKER"
    echo "$CURRENT_HASH" > "$REQUIREMENTS_HASH_FILE"
    echo "✅ 의존성 설치/업데이트 완료"
else
    echo "✅ 의존성 이미 설치됨 (requirements.in 변경 없음)"
fi

# 환경변수 로드 및 기본값 설정
echo "🌍 환경변수 로드: .env"
export $(grep -v '^#' "$ENV_FILE" | xargs)
UI_PORT=${UI_PORT:-8501}
UI_ADDRESS=${UI_ADDRESS:-0.0.0.0}

# Streamlit UI 실행
echo "🚀 Streamlit UI 실행 중..."
echo "📍 접속 주소: http://${UI_ADDRESS}:${UI_PORT}"
echo "⏹️  종료: Ctrl+C"

streamlit run ui.py --server.address "$UI_ADDRESS" --server.port "$UI_PORT"
