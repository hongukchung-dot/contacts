#!/bin/sh
# 매일 지정한 시각에 pg_dump 를 떠서 /backups 에 남기고, 보관 기간이 지난 파일을 지운다.
# postgres:16-alpine (busybox sh) 에서 도는 것을 전제로 한다 — `date -d "tomorrow"` 같은
# GNU 확장은 쓰지 않는다.
set -eu

: "${POSTGRES_USER:=contacts}"
: "${POSTGRES_DB:=contacts}"
: "${BACKUP_HOUR:=3}"        # 0~23 (컨테이너 TZ 기준)
: "${BACKUP_KEEP_DAYS:=14}"
: "${DB_HOST:=db}"

log() { echo "[backup] $(date '+%Y-%m-%d %H:%M:%S') $*"; }

seconds_until_target() {
  # 다음 BACKUP_HOUR 정각까지 남은 초. 이미 지났으면 다음 날로 넘긴다.
  now=$(( $(date +%-H) * 3600 + $(date +%-M) * 60 + $(date +%-S) ))
  target=$(( BACKUP_HOUR * 3600 ))
  delta=$(( (target - now + 86400) % 86400 ))
  [ "$delta" -eq 0 ] && delta=86400
  echo "$delta"
}

run_backup() {
  out="/backups/contacts_$(date +%Y%m%d_%H%M).dump"
  if pg_dump -h "$DB_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f "$out"; then
    log "덤프 완료: $out ($(du -h "$out" | cut -f1))"
  else
    log "덤프 실패 — 다음 주기에 다시 시도합니다"
    rm -f "$out"
    return 0
  fi
  deleted=$(find /backups -name 'contacts_*.dump' -mtime "+${BACKUP_KEEP_DAYS}" -print -delete | wc -l)
  [ "$deleted" -gt 0 ] && log "${BACKUP_KEEP_DAYS}일 지난 덤프 ${deleted}개 삭제"
  return 0
}

if [ "${1:-}" = "--once" ]; then
  run_backup
  exit 0
fi

log "백업 대기 시작 — 매일 ${BACKUP_HOUR}시, ${BACKUP_KEEP_DAYS}일 보관"
while true; do
  wait_for=$(seconds_until_target)
  log "다음 백업까지 $(( wait_for / 3600 ))시간 $(( wait_for % 3600 / 60 ))분"
  sleep "$wait_for"
  run_backup
done
