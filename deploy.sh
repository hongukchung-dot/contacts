#!/usr/bin/env bash
# 우분투 서버에서 한 번에 띄우는 스크립트.
#   ./deploy.sh          첫 배포 또는 갱신
#   ./deploy.sh --seed   초기화 후 관리자 계정 생성까지
set -euo pipefail
cd "$(dirname "$0")"

info() { printf '\033[1;34m▸\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || fail "docker 가 필요합니다: curl -fsSL https://get.docker.com | sh"
docker compose version >/dev/null 2>&1 || fail "docker compose v2 가 필요합니다."

if [ ! -f .env ]; then
  info ".env 가 없어 새로 만듭니다."
  cp .env.example .env
  SECRET=$(openssl rand -hex 32)
  DBPW=$(openssl rand -hex 16)
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${SECRET}|" .env
  sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${DBPW}|" .env
  sed -i "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+psycopg://contacts:${DBPW}@db:5432/contacts|" .env
  info "SECRET_KEY / DB 비밀번호를 자동 생성했습니다. 도메인을 쓰려면 .env 의 SITE_ADDRESS 를 고치세요."
fi

mkdir -p backups

info "이미지 빌드 …"
docker compose build

info "컨테이너 기동 …"
docker compose up -d

info "DB 준비 대기 …"
for _ in $(seq 1 30); do
  docker compose exec -T db pg_isready -q && break
  sleep 2
done

info "스키마 초기화 및 매체 사전 시드 …"
docker compose exec -T app python tools/init_db.py

SITE=$(grep -E '^SITE_ADDRESS=' .env | cut -d= -f2-)
echo
info "완료. 접속: ${SITE:-:8080}"
info "다음 순서로 진행하세요"
echo "   1) 관리자 계정으로 로그인 후 비밀번호 변경"
echo "   2) '계정 관리'에서 사용자별 계정 생성"
echo "   3) '파일 업로드'에서 주소록 4개 파일을 오래된 기준일 순서대로 올리기"
echo "      (26-0609) 출입기자 → (26-0609) 데스크(그룹) → (26-0731) 데스크 → (26-0803) 통합"
