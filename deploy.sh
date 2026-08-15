#!/usr/bin/env bash
# 서버에서 한 번에 띄우는 스크립트.
#
#   ./deploy.sh                   첫 배포 또는 갱신
#   ./deploy.sh --install-docker  도커가 없으면 자동 설치까지 (리눅스 전용)
#
set -euo pipefail
cd "$(dirname "$0")"

info() { printf '\033[1;34m▸\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

INSTALL_DOCKER=0
for arg in "$@"; do
  [ "$arg" = "--install-docker" ] && INSTALL_DOCKER=1
done

# ── 0. 도커 준비 ────────────────────────────────────────────────────────────

install_docker_linux() {
  info "도커를 설치합니다 (공식 설치 스크립트) …"
  curl -fsSL https://get.docker.com | sh
  if command -v systemctl >/dev/null 2>&1; then
    sudo systemctl enable --now docker || true
  fi
  if ! groups "$USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
    sudo usermod -aG docker "$USER" || true
    warn "현재 사용자를 docker 그룹에 넣었습니다."
    warn "이 셸에는 아직 적용되지 않았으니 아래 중 하나를 하고 ./deploy.sh 를 다시 실행하세요."
    echo "     newgrp docker        (지금 셸에 바로 적용)"
    echo "     또는 로그아웃 후 다시 접속"
    exit 0
  fi
}

docker_missing_help() {
  case "$(uname -s)" in
    Darwin)
      fail "$(cat <<'MSG'
이 스크립트는 서버(리눅스)에서 실행하는 것입니다. 지금은 macOS 에서 실행됐습니다.

  · 우분투 서버에 배포하려면:   ssh ubuntu@<서버주소>  로 접속한 뒤 서버에서 실행하세요.
  · 맥에서 그냥 시험해 보려면:  Docker Desktop 을 먼저 설치하세요.
      https://www.docker.com/products/docker-desktop/
      (또는 brew install --cask docker 후 Docker.app 을 한 번 실행)
MSG
)"
      ;;
    *)
      fail "$(cat <<'MSG'
도커가 설치돼 있지 않습니다. 둘 중 하나로 진행하세요.

  1) 이 스크립트가 대신 설치하게 하기
       ./deploy.sh --install-docker

  2) 직접 설치하기
       curl -fsSL https://get.docker.com | sh
       sudo usermod -aG docker $USER && newgrp docker
       ./deploy.sh
MSG
)"
      ;;
  esac
}

if ! command -v docker >/dev/null 2>&1; then
  if [ "$INSTALL_DOCKER" = "1" ] && [ "$(uname -s)" = "Linux" ]; then
    install_docker_linux
  else
    docker_missing_help
  fi
fi

if ! docker info >/dev/null 2>&1; then
  if [ "$(uname -s)" = "Darwin" ]; then
    fail "도커 데몬이 떠 있지 않습니다. Docker Desktop 을 실행한 뒤 다시 시도하세요."
  fi
  if sudo -n docker info >/dev/null 2>&1; then
    fail "$(cat <<'MSG'
도커는 설치돼 있지만 현재 사용자에게 권한이 없습니다.

    sudo usermod -aG docker $USER && newgrp docker
    ./deploy.sh
MSG
)"
  fi
  fail "도커 데몬에 접근할 수 없습니다. 'sudo systemctl start docker' 후 다시 시도하세요."
fi

docker compose version >/dev/null 2>&1 || fail "docker compose v2 가 필요합니다. 도커를 최신 버전으로 설치해 주세요."

command -v openssl >/dev/null 2>&1 || fail "openssl 이 필요합니다: sudo apt-get install -y openssl"

# ── 1. 설정 파일 ────────────────────────────────────────────────────────────

# BSD sed(macOS) 와 GNU sed(리눅스) 모두에서 동작하도록 임시 파일을 쓴다.
replace_line() {
  local key="$1" value="$2" file="$3"
  awk -v k="$key" -v v="$value" \
    'BEGIN{FS=OFS="="} $0 ~ "^"k"=" {print k "=" v; next} {print}' \
    "$file" > "$file.tmp" && mv "$file.tmp" "$file"
}

if [ ! -f .env ]; then
  info ".env 가 없어 새로 만듭니다."
  cp .env.example .env
  SECRET=$(openssl rand -hex 32)
  DBPW=$(openssl rand -hex 16)
  replace_line SECRET_KEY "$SECRET" .env
  replace_line POSTGRES_PASSWORD "$DBPW" .env
  replace_line DATABASE_URL "postgresql+psycopg://contacts:${DBPW}@db:5432/contacts" .env
  chmod 600 .env
  info "SECRET_KEY / DB 비밀번호를 자동 생성했습니다."
  info "도메인을 쓰려면 .env 의 SITE_ADDRESS 를 고친 뒤 ./deploy.sh 를 다시 실행하세요."
fi

mkdir -p backups
chmod 700 backups

# ── 2. 기동 ─────────────────────────────────────────────────────────────────

info "이미지 빌드 …"
docker compose build

info "컨테이너 기동 …"
docker compose up -d

info "DB 준비 대기 …"
ready=0
for _ in $(seq 1 40); do
  if docker compose exec -T db pg_isready -q 2>/dev/null; then ready=1; break; fi
  sleep 2
done
[ "$ready" = "1" ] || fail "DB가 시간 안에 준비되지 않았습니다. 'docker compose logs db' 를 확인하세요."

info "앱 준비 대기 …"
for _ in $(seq 1 30); do
  docker compose exec -T app python -c "import app.main" >/dev/null 2>&1 && break
  sleep 2
done

info "스키마 초기화 및 매체 사전 시드 …"
docker compose exec -T app python tools/init_db.py

# ── 3. 안내 ─────────────────────────────────────────────────────────────────

SITE=$(grep -E '^SITE_ADDRESS=' .env | cut -d= -f2- || true)
SITE=${SITE:-:8080}
case "$SITE" in
  :*) URL="http://$(hostname -I 2>/dev/null | awk '{print $1}')${SITE}" ;;
  *)  URL="https://${SITE}" ;;
esac

echo
info "완료. 접속 주소: ${URL}"
echo
info "다음 순서로 진행하세요"
echo "   1) 위 관리자 계정으로 로그인 → 비밀번호 변경"
echo "   2) '계정 관리' 에서 사용자별 계정 생성"
echo "   3) 실제 파일을 넣기 전에 파싱부터 확인 (DB 변경 없음)"
echo "        docker compose cp '파일.xlsx' app:/tmp/f.xlsx"
echo "        docker compose exec app python tools/inspect_file.py /tmp/f.xlsx --full"
echo "   4) '파일 업로드' 에서 기준일이 오래된 파일부터 순서대로"
echo "        (26-0609) 출입기자 → (26-0609) 데스크(그룹) → (26-0731) 데스크 → (26-0803) 통합"
echo
case "$SITE" in
  :*) warn "지금은 HTTP 입니다. 개인정보가 담긴 자료이므로 사내망/VPN 안에서만 쓰거나," ;;
esac
case "$SITE" in
  :*) warn ".env 의 SITE_ADDRESS 에 도메인을 넣어 HTTPS 로 전환하세요." ;;
esac
