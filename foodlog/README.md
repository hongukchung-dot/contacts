# 푸드로그 — 음식 사진 열량 기록 앱

먹은 음식 사진을 찍어두면 Claude 비전 모델이 메뉴와 섭취 열량을 추정해주고,
일·주·월 추이를 보여주는 개인용 식단 기록 앱. `docs/` 스펙 문서
(음식 사진 열량 기록 앱 — 구현 스펙) 기반 구현.

```
foodlog/
├── android/   Kotlin + Jetpack Compose 앱 (admin/friend 플레이버)
└── server/    FastAPI 프록시 (Oracle 인스턴스, Anthropic·식약처·OFF 키 보관)
```

## 핵심 원칙 (스펙 1절)

1. 사진은 찍는 즉시 저장, 분석은 나중 (오프라인 촬영 → 온라인 분석)
2. "끼니(Meal)"가 기록 단위 — 한 끼니에 사진 여러 장, 음식 항목 여러 개
3. AI 추정은 초안, 사용자가 항목별 확인·수정 (저장 시 REVIEWED)
4. 외부 API 키는 전부 서버에만. 앱은 `X-App-Token`으로 서버만 호출

## 앱 빌드

```bash
cd foodlog/android
# 선택: 플레이버 기본값 주입 (없으면 앱 설정 화면에서 입력)
cat >> local.properties <<EOF
FOODLOG_SERVER_URL=https://<host>.ts.net/foodlog
FOODLOG_ADMIN_TOKEN=...
FOODLOG_FRIEND_TOKEN=...
EOF
./gradlew assembleAdminDebug     # 본인용
./gradlew assembleFriendDebug    # 지인 배포용 (서버 설정 편집 불가, 앱ID .friend)
```

GitHub Actions(`.github/workflows/foodlog.yml`)가 푸시마다 두 플레이버 APK를
빌드해 아티팩트로 올린다.

### 구현된 화면 (스펙 4절)

| 화면 | 내용 |
|---|---|
| 홈(오늘) | 목표 대비 진행 바, 끼니 카드, 미분석 배지, FAB(카메라/바코드/직접입력) |
| 빠른 촬영 | CameraX 연속 촬영(묻지 않고 저장), 갤러리 가져오기(EXIF 시각) |
| 미분석 사진함 | 90분 간격 자동 그룹 제안 → 체크박스 조정 → 끼니 만들기 |
| 끼니 편집 | 사진 스트립, 유형/시각/인원수, AI 분석(병합·교체), 배수·공유 토글, 신뢰도<0.5 주황 표시, 상세 수정 시트, 내 몫 합계 |
| 바코드 | ML Kit(번들형) 실시간 스캔 → 로컬 캐시 → 서버 조회 → 실패 시 직접 입력(재사용 저장) 또는 성분표 판독 |
| 통계 | 일(14일 막대+7일 이동평균+목표선)/주(12주)/월(12개월) Vico 차트, 요약, 유형별 비중, 날짜 탭 → 그날 끼니 |
| 설정 | 서버 주소·토큰(admin만), 목표 열량, 백업/복원(SAF zip), 저장 용량·원본 정리 |

열량 계산: `내몫 = kcalPerUnit × quantity ÷ (공유 시 peopleCount)`,
집계는 `status=REVIEWED`만 포함(로컬 타임존 기준 날짜).

## 서버 배치

[`server/DEPLOY.md`](server/DEPLOY.md) 참고. 요약: `/opt/foodlog` + venv,
`/etc/foodlog.env`(ANTHROPIC_API_KEY, MFDS_API_KEY, APP_TOKENS, DAILY_LIMIT),
systemd `foodlog.service`(포트 8090), Tailscale Funnel `--set-path /foodlog`.

- `POST /v1/analyze` — 이미지들을 Anthropic Messages 한 요청으로 전송,
  모델 `claude-sonnet-5`, max_tokens 2000, 시스템 프롬프트 prompt caching.
  JSON 파싱 실패 시 1회 재시도 후 502. 토큰별 일일 상한(기본 200회) 초과 시 429
- `GET /v1/barcode/{code}` — 식약처(C005→I2790) → Open Food Facts → 서버 SQLite 캐시
- `GET /v1/health`

테스트: `python -m pytest -q foodlog/server` (Anthropic 호출은 목)

## 확인·검증 필요 (스펙 10절)

- **식약처 Open API**: C005(바코드연계)·I2790(영양성분) 서비스명/필드명을 공식 문서로
  확인 후 `server.py`의 `_lookup_mfds()` 조정. 실패해도 OFF로 자동 폴백
- **Vico 2.1.3 API**: Compose 버전 호환 — CI 빌드로 확인
- **ML Kit 바코드**: 번들형 선택(오프라인 안정, APK 약 +3MB)

## 구현 순서 대비 현황 (스펙 9절)

1. ✅ 뼈대 (Room, 네비게이션, 촬영, 미분석함, 수동 입력)
2. ✅ 서버 (analyze/health, systemd, Funnel 문서)
3. ✅ AI 분석 연결 (분석 버튼, 병합/교체, 신뢰도 표시, 1280px 리사이즈)
4. ✅ 1/n (인원수·공유 토글·내 몫)
5. ✅ 바코드 (ML Kit, /v1/barcode, Product 캐시, 라벨 판독 폴백)
6. ✅ 통계 (집계 쿼리, 차트 3종, 요약)
7. ✅ 백업/복원 방식 A (SAF zip, 병합/교체)
8. ✅ 마무리 (friend 토큰 분리, 사용량 상한, 원본 정리 옵션)

각 단계는 실제 기기에서 확인 필요 — 특히 CameraX·바코드 스캔·SAF 백업.
