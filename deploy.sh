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

get_env() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true; }

# 이 포트를 (도커가 아닌) 다른 프로세스가 이미 쓰고 있는가
port_busy() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${port}\$"
  elif command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
  else
    return 1
  fi
}

who_has_port() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    sudo -n ss -lptn "sport = :${port}" 2>/dev/null | tail -n +2 \
      || ss -ltn "sport = :${port}" 2>/dev/null | tail -n +2
  fi
}

# 시작 포트부터 위로 올라가며 비어 있는 포트를 찾는다
first_free_port() {
  local port="$1"
  while port_busy "$port"; do port=$((port + 1)); done
  echo "$port"
}

compose_project() {
  # compose 는 디렉터리명을 소문자로 바꾸고 허용되지 않는 문자를 지워 프로젝트명을 만든다.
  echo "${COMPOSE_PROJECT_NAME:-$(basename "$PWD" | tr '[:upper:]' '[:lower:]' | tr -cd '[:alnum:]_-')}"
}

db_volume_exists() {
  [ -n "$(docker volume ls -q \
      --filter "label=com.docker.compose.project=$(compose_project)" \
      --filter "label=com.docker.compose.volume=pgdata" 2>/dev/null)" ]
}

if [ ! -f .env ]; then
  # PostgreSQL 은 데이터 볼륨이 처음 만들어질 때만 계정/비밀번호를 잡는다.
  # .env 만 새로 만들면 새 비밀번호와 기존 볼륨의 옛 비밀번호가 어긋나 인증이 실패한다.
  if db_volume_exists; then
    fail "$(cat <<'MSG'
.env 는 없는데 DB 데이터 볼륨은 이미 있습니다.

PostgreSQL 비밀번호는 볼륨을 처음 만들 때 한 번만 정해집니다.
지금 .env 를 새로 만들면 비밀번호가 어긋나 접속이 실패합니다. 둘 중 하나를 고르세요.

  1) DB를 비우고 처음부터 (아직 자료를 안 넣었다면 이쪽)
       docker compose down -v
       ./deploy.sh

  2) 자료를 지키고 싶다면 — 먼저 백업하고 비밀번호를 맞춥니다
       docker compose exec backup /scripts/backup.sh --once   # ./backups 에 덤프 생성
       그다음 예전 .env 의 POSTGRES_PASSWORD / DATABASE_URL 값을 되살리세요.
MSG
)"
  fi
  info ".env 가 없어 새로 만듭니다."
  cp .env.example .env
  SECRET=$(openssl rand -hex 32)
  DBPW=$(openssl rand -hex 16)
  replace_line SECRET_KEY "$SECRET" .env
  replace_line POSTGRES_PASSWORD "$DBPW" .env
  replace_line DATABASE_URL "postgresql+psycopg://contacts:${DBPW}@db:5432/contacts" .env

  # 서버가 이미 80·443 을 쓰고 있으면 비어 있는 포트로 자동 회피한다.
  if port_busy 80 || port_busy 443; then
    NEW_HTTP=$(first_free_port 8080)
    NEW_HTTPS=$(first_free_port 8443)
    replace_line HTTP_PORT "$NEW_HTTP" .env
    replace_line HTTPS_PORT "$NEW_HTTPS" .env
    warn "이 서버는 이미 80/443 포트를 쓰고 있어 ${NEW_HTTP}/${NEW_HTTPS} 로 잡았습니다."
    warn "80·443 을 쓰고 있는 프로세스:"
    who_has_port 80 | sed 's/^/      /'
    who_has_port 443 | sed 's/^/      /'
  fi
  chmod 600 .env
  info "SECRET_KEY / DB 비밀번호를 자동 생성했습니다."
  info "도메인을 쓰려면 .env 의 SITE_ADDRESS 를 고친 뒤 ./deploy.sh 를 다시 실행하세요."
fi

# .env 가 이미 있는 경우에도 포트 충돌은 미리 잡아 준다.
HTTP_PORT=$(get_env HTTP_PORT); HTTP_PORT=${HTTP_PORT:-80}
HTTPS_PORT=$(get_env HTTPS_PORT); HTTPS_PORT=${HTTPS_PORT:-443}
for spec in "HTTP_PORT:$HTTP_PORT" "HTTPS_PORT:$HTTPS_PORT"; do
  key=${spec%%:*}; value=${spec##*:}
  # 이미 우리 컨테이너가 물고 있는 경우는 충돌이 아니다 (재배포).
  if port_busy "$value" && ! docker compose ps --format '{{.Publishers}}' 2>/dev/null | grep -q ":${value}->"; then
    fail "$(printf '%s\n' \
      "${value} 포트를 이미 다른 프로세스가 쓰고 있습니다." \
      "" \
      "$(who_has_port "$value")" \
      "" \
      ".env 의 ${key} 를 비어 있는 포트로 바꾼 뒤 다시 실행하세요. 예)" \
      "    sed -i 's/^${key}=.*/${key}=$(first_free_port 8080)/' .env" \
      "    ./deploy.sh")"
  fi
done

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
if ! docker compose exec -T app python tools/init_db.py 2> >(tee /tmp/contacts_init_err.log >&2); then
  if grep -q "password authentication failed" /tmp/contacts_init_err.log 2>/dev/null; then
    fail "$(cat <<'MSG'
DB 비밀번호가 맞지 않습니다.

.env 의 비밀번호와 DB 볼륨이 처음 만들어질 때 정해진 비밀번호가 다릅니다.
아직 자료를 넣지 않았다면 볼륨을 지우고 다시 만드는 게 가장 빠릅니다.

    docker compose down -v
    ./deploy.sh
MSG
)"
  fi
  fail "초기화에 실패했습니다. 'docker compose logs app' 과 위 오류를 확인하세요."
fi

# ── 3. 안내 ─────────────────────────────────────────────────────────────────

SITE=$(get_env SITE_ADDRESS); SITE=${SITE:-:80}
PORT_SUFFIX=""
[ "$HTTP_PORT" != "80" ] && PORT_SUFFIX=":${HTTP_PORT}"

echo
info "완료. 접속이 되는지 먼저 서버 안에서 확인합니다 …"
if curl -fsS -o /dev/null "http://127.0.0.1:${HTTP_PORT}/login" 2>/dev/null; then
  echo "      ✓ 앱 응답 정상 (http://127.0.0.1:${HTTP_PORT})"
else
  warn "앱이 아직 응답하지 않습니다. 'docker compose logs app caddy' 를 확인하세요."
fi

echo
case "$SITE" in
  :*)
    info "브라우저에서 아래 주소 중 접속 가능한 것으로 여세요."
    # Tailscale 주소가 있으면 가장 먼저 권한다 — 사설망이라 개인정보 노출 위험이 낮다.
    if command -v tailscale >/dev/null 2>&1; then
      TS_IP=$(tailscale ip -4 2>/dev/null | head -1 || true)
      [ -n "$TS_IP" ] && echo "      http://${TS_IP}${PORT_SUFFIX}   ← Tailscale (같은 tailnet 기기에서, 권장)"
    fi
    for ip in $(hostname -I 2>/dev/null); do
      case "$ip" in
        100.*|127.*) continue ;;                       # tailscale/loopback 은 위에서 처리
        10.*|192.168.*|172.1[6-9].*|172.2*.*|172.3[01].*)
          echo "      http://${ip}${PORT_SUFFIX}   ← 사설망 (같은 네트워크 안에서만)" ;;
        *)
          echo "      http://${ip}${PORT_SUFFIX}   ← 이 서버의 IP" ;;
      esac
    done
    PUBLIC_IP=$(curl -fsS --max-time 3 https://api.ipify.org 2>/dev/null || true)
    [ -n "$PUBLIC_IP" ] && echo "      http://${PUBLIC_IP}${PORT_SUFFIX}   ← 공인 IP (방화벽에서 ${HTTP_PORT} 를 열어야 함)"
    echo
    echo "      공인 IP로 열려면:  sudo ufw allow ${HTTP_PORT}/tcp"
    echo "      (클라우드라면 콘솔의 보안 그룹에서도 ${HTTP_PORT} 인바운드를 열어야 합니다)"
    ;;
  *)
    if [ "$HTTPS_PORT" = "443" ]; then
      info "접속 주소: https://${SITE}"
    else
      info "접속 주소: https://${SITE}:${HTTPS_PORT}"
    fi
    ;;
esac
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
  :*)
    warn "지금은 HTTP 입니다. 실명·휴대전화번호가 담긴 자료이므로,"
    warn "사내망/VPN 안에서만 쓰거나 .env 의 SITE_ADDRESS 에 도메인을 넣어 HTTPS 로 전환하세요."
    warn "이미 다른 웹서버(nginx 등)가 80·443 을 쓰고 있다면, 그쪽에 아래 프록시를 추가하는 방법도 있습니다."
    echo "      location / { proxy_pass http://127.0.0.1:${HTTP_PORT}; proxy_set_header Host \$host; }"
    ;;
esac
