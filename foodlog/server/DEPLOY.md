# 서버 배치 (Oracle Ubuntu 24.04)

기존 newsmonitor 인스턴스를 재사용한다. 포트 **8090**.

```bash
# 1. 코드 올리기
sudo mkdir -p /opt/foodlog && sudo chown ubuntu:ubuntu /opt/foodlog
scp foodlog/server/server.py foodlog/server/requirements.txt ubuntu@<서버>:/opt/foodlog/

# 2. 가상환경
cd /opt/foodlog
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# 3. 환경 파일
sudo cp foodlog.env.example /etc/foodlog.env
sudo chmod 600 /etc/foodlog.env
sudo nano /etc/foodlog.env   # 키·토큰 채우기

# 4. systemd
sudo cp foodlog.service /etc/systemd/system/foodlog.service
sudo systemctl daemon-reload
sudo systemctl enable --now foodlog

# 5. Tailscale Funnel — 기존 newsmonitor 설정에 경로 추가
sudo tailscale funnel --bg --set-path /foodlog http://127.0.0.1:8090
# 앱의 서버 주소: https://<장비명>.<tailnet>.ts.net/foodlog
```

## curl 검증

```bash
BASE=https://<장비명>.<tailnet>.ts.net/foodlog
TOKEN=<APP_TOKENS 중 하나>

curl -s $BASE/v1/health

# 사진 분석
b64=$(base64 -w0 test.jpg)
curl -s -X POST $BASE/v1/analyze \
  -H "X-App-Token: $TOKEN" -H "Content-Type: application/json" \
  -d "{\"images\":[{\"data\":\"$b64\",\"taken_at\":\"2026-09-06T12:31:00+09:00\"}],\"mode\":\"meal\"}"

# 바코드
curl -s -H "X-App-Token: $TOKEN" $BASE/v1/barcode/8801043015337
```

## 확인 필요 (스펙 10절)

- 식약처 Open API의 **C005(바코드연계 제품정보) / I2790(영양성분)** 서비스명·필드명은
  공식 문서로 확인 후 필요 시 `_lookup_mfds()` 수정. 필드가 없으면 자동으로
  Open Food Facts 로 폴백하므로 동작 자체는 깨지지 않는다.
