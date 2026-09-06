# 음식 사진 열량 기록 앱 — 구현 스펙

Claude Code에 그대로 넘겨 구현을 시작하기 위한 문서. 앱(Android)과 프록시 서버(Oracle Cloud) 두 부분으로 나뉜다.

---

## 1. 개요

- 목적: 먹은 음식 사진을 찍어두면 Claude 비전 모델이 메뉴와 섭취 열량을 추정해주고, 일·주·월 추이를 보여주는 개인용 식단 기록 앱
- 사용자: 본인 + 소수 지인 (지인 배포 대비해 API 키는 서버에만 보관)
- 핵심 원칙
  1. 사진은 찍는 즉시 저장, 분석은 나중에 해도 된다 (오프라인 촬영 → 온라인 분석)
  2. "끼니(Meal)"가 기록 단위. 한 끼니에 사진 여러 장, 음식 항목 여러 개
  3. AI 추정은 초안이며 사용자가 항목별로 확인·수정한다
  4. 외부 API 키(Anthropic, 식약처 등)는 전부 서버에 둔다. 앱은 서버만 호출

## 2. 기술 스택

### 앱
- Kotlin, Jetpack Compose, Material 3 (samsung-news-monitor와 같은 구조를 따른다)
- Room (로컬 DB), DataStore (설정)
- CameraX (촬영), Coil (이미지 표시)
- ML Kit Barcode Scanning (온디바이스, 무료)
- OkHttp (서버 호출), kotlinx.serialization (JSON)
- Vico (차트)
- minSdk 26, targetSdk 최신
- 빌드 플레이버: `admin` / `friend` — 지난 앱과 동일. 다만 이번엔 두 플레이버 모두 서버 URL과 앱 토큰만 갖고, Anthropic 키는 없다

### 서버
- 기존 Oracle 인스턴스(Ubuntu 24.04, VM.Standard.E2.1.Micro) 재사용
- Python 3 + FastAPI + uvicorn, systemd 서비스
- 키·토큰은 `/etc/foodlog.env`
- 외부 노출은 기존과 같이 Tailscale Funnel (기존 newsmonitor와 포트 또는 경로로 분리)

## 3. 데이터 모델 (Room)

```kotlin
@Entity
data class Photo(
    @PrimaryKey val id: String,          // UUID
    val filePath: String,                // 앱 전용 저장소 내 경로
    val takenAt: Long,                   // epoch millis (EXIF 우선, 없으면 파일 생성 시각)
    val mealId: String? = null,          // null이면 "미분석 사진함"에 표시
    val createdAt: Long
)

@Entity
data class Meal(
    @PrimaryKey val id: String,
    val eatenAt: Long,                   // 끼니 시각 (사진 중 가장 이른 takenAt 기본값)
    val mealType: MealType,              // BREAKFAST, LUNCH, DINNER, SNACK — 시간대로 자동 제안
    val peopleCount: Int = 1,            // 나눠 먹은 인원 (공유 항목에만 적용)
    val status: MealStatus,              // DRAFT, ANALYZING, REVIEWED
    val note: String? = null,
    val createdAt: Long,
    val updatedAt: Long
)

@Entity
data class FoodItem(
    @PrimaryKey val id: String,
    val mealId: String,
    val name: String,                    // "김치찌개", "삼겹살 3인분"
    val source: ItemSource,              // PHOTO_AI, BARCODE, LABEL_AI, MANUAL
    val portionDesc: String?,            // "1인분(약 300g)", "1봉지(90g)"
    val quantity: Double = 1.0,          // 사용자가 조정하는 배수
    val kcalPerUnit: Double,             // quantity=1 기준 열량
    val carbsG: Double? = null,
    val proteinG: Double? = null,
    val fatG: Double? = null,
    val isShared: Boolean = false,       // true면 meal.peopleCount로 나눔
    val confidence: Float? = null,       // AI 추정 신뢰도 0~1
    val barcode: String? = null,
    val sortOrder: Int
)

@Entity
data class Product(                      // 바코드 조회 캐시
    @PrimaryKey val barcode: String,
    val name: String,
    val brand: String?,
    val servingDesc: String?,
    val kcalPerServing: Double,
    val carbsG: Double?, val proteinG: Double?, val fatG: Double?,
    val source: String,                  // "MFDS", "OFF", "LABEL_AI", "MANUAL"
    val fetchedAt: Long
)
```

열량 계산 규칙:

```
itemKcal      = kcalPerUnit * quantity
myItemKcal    = isShared ? itemKcal / meal.peopleCount : itemKcal
mealKcal      = Σ myItemKcal
dayKcal       = Σ mealKcal (eatenAt 기준 날짜, 기기 로컬 타임존)
```

집계용 뷰/쿼리: 날짜별 합계, 주별·월별 합계와 평균, 끼니 유형별 합계. `status = REVIEWED`인 끼니만 집계에 포함 (DRAFT는 홈에 "확인 필요"로만 표시).

설정(DataStore): `serverBaseUrl`, `appToken`, `dailyGoalKcal`, `imageMaxEdgePx`(기본 1280), `lastBackupAt`.

## 4. 화면 구성

### 4.1 홈 (오늘)
- 오늘 총 섭취 열량 / 목표 대비 진행 바
- 끼니 카드 목록 (아침·점심·저녁·간식), 각 카드에 대표 사진 썸네일·항목 요약·열량
- 상단에 "미분석 사진 N장" 배지 → 미분석 사진함으로
- FAB: 카메라 / 바코드 / 직접 입력 세 가지

### 4.2 빠른 촬영
- CameraX로 연속 촬영. 찍으면 즉시 저장하고 계속 찍을 수 있다 (코스 요리 대응)
- 저장 후 아무 것도 묻지 않는다. 분석은 나중
- 갤러리에서 가져오기(사진 선택기)도 지원 — takenAt은 EXIF에서

### 4.3 미분석 사진함
- mealId가 null인 사진을 촬영 시각 순으로 표시
- 시간 간격 기준(기본 90분 이내)으로 자동 그룹 제안 → 사용자가 체크박스로 조정
- "이 사진들로 끼니 만들기" → 끼니 편집 화면으로

### 4.4 끼니 편집 (핵심 화면)
- 상단: 사진 스트립 (추가/제거 가능), 끼니 시각, 끼니 유형, 인원수 스테퍼
- "AI 분석" 버튼 → 서버 호출 → 결과가 항목 목록으로 채워짐 (기존 항목이 있으면 병합할지 교체할지 묻기)
- 항목 행: 이름 / 분량 설명 / 배수(0.5, 1, 1.5 …) / 열량 / 공유 토글 / 신뢰도 낮으면 표시
- 항목 탭 → 상세 수정 시트 (이름, 열량 직접 입력, 탄단지)
- 항목 추가: 바코드 스캔 / 직접 입력 / 영양성분표 사진 분석
- 하단 합계: 끼니 전체 열량 → 내 몫 열량 (공유 항목은 1/n 적용 표시)
- "저장" → status=REVIEWED

### 4.5 바코드 스캔
- ML Kit 실시간 스캔. EAN-13, EAN-8, UPC-A/E, Code 128
- 인식되면 로컬 Product 캐시 → 없으면 서버 `/v1/barcode/{code}` 조회
- 결과 확인 시트: 제품명·1회 제공량·열량 → 수량 입력 → 항목으로 추가
- 조회 실패 시: "영양성분표를 찍어서 읽기" 또는 "직접 입력" 제안. 직접 입력한 제품도 Product에 저장해 다음에 재사용

### 4.6 통계
- 탭: 일 / 주 / 월
- 일: 최근 14일 막대 (오늘 강조), 7일 이동평균 선, 목표선
- 주: 최근 12주 주간 평균 막대
- 월: 최근 12개월 월간 평균 막대
- 각 탭 하단 요약: 기간 평균, 최고/최저, 목표 달성 일수, 끼니 유형별 비중
- 날짜 탭하면 그날 끼니 목록으로

### 4.7 설정
- 서버 주소, 앱 토큰 (admin 플레이버만 편집 가능)
- 일일 목표 열량
- 백업/복원 (6절)
- 저장 공간 사용량, 오래된 사진 정리(원본 삭제하고 썸네일만 남기기 옵션)

## 5. 서버 (프록시)

### 5.1 엔드포인트

모든 요청에 `X-App-Token` 헤더 필수. 불일치 시 401.

```
POST /v1/analyze
  body: {
    "images": [ {"data": "<base64 jpeg>", "taken_at": "2026-09-06T12:31:00+09:00"} ],
    "hint": "선택. 사용자가 적은 힌트 (예: '회사 근처 백반집')",
    "mode": "meal" | "label"      // label = 영양성분표 판독
  }
  response: 6절의 JSON 스키마 그대로

GET /v1/barcode/{code}
  response: { "found": true, "product": {...Product 필드...}, "source": "MFDS"|"OFF" }
            { "found": false }

GET /v1/health
```

### 5.2 처리 흐름
- `/v1/analyze`: 이미지들을 하나의 Anthropic Messages 요청에 담아 전송. 모델 `claude-sonnet-5`, max_tokens 2000, 시스템 프롬프트(6절)는 prompt caching 적용. 응답 JSON 파싱 실패 시 1회 재시도 후 502
- `/v1/barcode`: ① 식약처 식품안전나라 Open API — 바코드연계 제품정보 → 품목보고번호로 가공식품 영양성분 조회 (API 항목명·파라미터는 구현 시 공식 문서에서 확인) → ② Open Food Facts `https://world.openfoodfacts.org/api/v2/product/{code}` → 둘 다 없으면 found=false. 결과는 서버에서도 SQLite에 캐시
- 사용량 로그: 요청 시각, 이미지 수, 입력/출력 토큰, 앱 토큰별. 일일 상한(예: 토큰당 200회) 넘으면 429

### 5.3 배치
- `/opt/foodlog/` 에 `server.py`, `requirements.txt`, `cache.db`
- `/etc/foodlog.env`: `ANTHROPIC_API_KEY`, `MFDS_API_KEY`, `APP_TOKENS`(쉼표 구분, admin/friend 별도 토큰)
- systemd 유닛 `foodlog.service`, 포트 8090
- Tailscale Funnel로 노출 (기존 newsmonitor 설정에 경로 또는 포트 추가)

## 6. Claude 분석 프롬프트 초안

### 시스템 프롬프트 (mode=meal)

```
당신은 한국 식단 기록 앱의 음식 인식 엔진입니다. 한 끼니에 해당하는 사진 여러 장을 받습니다.
같은 음식이 여러 사진에 겹쳐 나오면 하나로 합치고, 여러 사진에 걸친 코스 요리는 순서대로 모두 나열하세요.

각 음식에 대해 다음을 추정합니다.
- name: 한국어 메뉴명 (가능하면 구체적으로. "찌개"보다 "김치찌개")
- portion_desc: 사진에 보이는 전체 분량 (예: "1인분(약 300g)", "2인분 가량", "1조각")
- kcal: portion_desc 전체의 열량 (내 몫이 아니라 사진에 보이는 전체)
- carbs_g, protein_g, fat_g: 가능하면 추정, 모르면 null
- likely_shared: 여러 명이 나눠 먹는 형태로 보이면 true (전골, 고기 판, 큰 접시 안주 등), 개인 접시·개인 그릇이면 false
- confidence: 0~1. 메뉴 식별과 분량 추정 모두 반영

반드시 아래 JSON만 출력하고 다른 텍스트는 쓰지 마세요.
{
  "items": [
    {"name": "...", "portion_desc": "...", "kcal": 0, "carbs_g": null, "protein_g": null, "fat_g": null,
     "likely_shared": false, "confidence": 0.0, "photo_indices": [0]}
  ],
  "meal_type_guess": "BREAKFAST|LUNCH|DINNER|SNACK",
  "notes": "분량 판단 근거나 불확실한 점을 한두 문장으로"
}
```

### 시스템 프롬프트 (mode=label)

```
영양성분표 사진을 읽어 JSON으로 정리하세요. 총 내용량과 1회 제공량을 구분하고,
열량은 1회 제공량 기준으로 적으세요. 읽을 수 없는 값은 null.
{"product_name": "...", "serving_desc": "...", "total_servings": 0,
 "kcal_per_serving": 0, "carbs_g": null, "protein_g": null, "fat_g": null, "confidence": 0.0}
```

### 앱 측 처리
- 전송 전 이미지를 긴 변 1280px, JPEG 품질 80으로 리사이즈 (원본은 로컬 보관)
- `likely_shared`는 항목의 `isShared` 초기값으로만 쓰고 사용자가 토글로 확정
- `confidence < 0.5` 항목은 편집 화면에서 주황색 표시
- `meal_type_guess`는 사용자가 아직 유형을 안 골랐을 때만 반영

## 7. 백업 / 복원

기본은 로컬 보관. 백업은 사용자가 원할 때 수동 실행.

### 방식 A (1차 구현) — Storage Access Framework 내보내기
- "백업 파일 만들기" → `foodlog-backup-YYYYMMDD.zip` 생성
  - `db.json`: Meal, FoodItem, Product, Photo 메타데이터 전체
  - `photos/`: 원본 사진 (파일명 = Photo.id)
  - `manifest.json`: 앱 버전, 스키마 버전, 생성 시각
- `ACTION_CREATE_DOCUMENT`로 저장 위치를 사용자가 고른다 → Google Drive 앱이 설치되어 있으면 드라이브 폴더를 그대로 선택 가능. OAuth·Drive API 설정 없이 구글 드라이브 백업이 된다
- 복원: `ACTION_OPEN_DOCUMENT`로 zip 선택 → 병합(같은 id는 건너뜀) 또는 전체 교체 선택
- 사진 수가 많으면 월별 분할 zip 옵션

### 방식 B (선택, 나중) — Google Drive API 자동 백업
- Google Sign-In + Drive API `appDataFolder`에 증분 업로드, WorkManager로 주기 실행
- A가 충분하면 생략

## 8. 사진 저장 정책
- 원본: `context.filesDir/photos/` (앱 전용, 갤러리 미노출). 앱 삭제 시 함께 삭제되므로 설정 화면에 경고 문구
- 썸네일: 320px, `cacheDir` 아님 `filesDir/thumbs/`에 영구 저장
- 갤러리에서 가져온 사진은 앱 저장소로 복사 (원본 URI 의존 안 함)
- 설정에서 "N개월 지난 원본 삭제, 썸네일만 유지" 옵션

## 9. 구현 순서

1. **뼈대**: 프로젝트 생성, Room 스키마, 네비게이션, CameraX 빠른 촬영, 미분석 사진함, 끼니 수동 생성·항목 직접 입력. 이 단계만으로도 손으로 기록 가능
2. **서버**: FastAPI `/v1/analyze` + `/v1/health`, systemd, Funnel 노출. curl로 검증
3. **AI 분석 연결**: 끼니 편집 화면의 분석 버튼, 결과 병합 UI, 신뢰도 표시, 리사이즈
4. **1/n**: 인원수·공유 토글·내 몫 계산, 홈 화면 합계 반영
5. **바코드**: ML Kit 스캔, 서버 `/v1/barcode`, Product 캐시, 라벨 사진 판독 폴백
6. **통계**: 집계 쿼리, Vico 차트 3종, 요약 카드
7. **백업/복원**: 방식 A
8. **마무리**: friend 플레이버 토큰 분리, 사용량 상한, 저장 공간 정리 옵션

각 단계 끝에 실제 기기에서 확인 후 다음 단계로.

## 10. 확인·검증 필요 사항
- 식약처 Open API의 바코드 연계 서비스와 영양성분 DB의 정확한 서비스명·필드명 (구현 시 공식 문서 기준)
- Vico 최신 버전 API (Compose 버전과 호환 확인)
- ML Kit Barcode의 번들형/언번들형 중 선택 (번들형이 오프라인 안정적, APK 크기 증가)
