# NPS 13F Tracker

국민연금(National Pension Service, CIK: `1608046`)의 미국 13F 포트폴리오를 sec-api.io로 조회하고, 최신 분기와 직전 분기의 보유 수량 변화를 비교하는 GitHub Actions 테스트 프로젝트입니다.

## 주요 기능

- 국민연금 최근 13F 포트폴리오 조회
- 최신 분기 vs 직전 분기 비교
- 신규 편입, 보유수량 증가, 보유수량 감소, 전량매도/보고제외 분류
- CSV 결과 파일 생성
- GitHub Actions Summary에 Markdown 리포트 출력
- Actions Artifact로 결과 다운로드

## 생성 파일

실행 후 `out/` 폴더에 다음 파일이 생성됩니다.

```text
nps_13f_report.md
nps_13f_diff_직전분기_to_최신분기.csv
nps_13f_holdings_최신분기.csv
nps_13f_holdings_직전분기.csv
```

## GitHub Secret 설정

GitHub 저장소에서 다음 경로로 이동합니다.

```text
Settings
→ Secrets and variables
→ Actions
→ New repository secret
```

다음 secret을 등록하세요.

```text
Name: SEC_API_KEY
Value: sec-api.io에서 발급받은 API 키
```

## 실행 방법

GitHub 저장소에서:

```text
Actions
→ NPS 13F Portfolio Diff
→ Run workflow
```

옵션:

- `top_n`: 리포트에 표시할 상위 종목 수
- `query_size`: 최근 13F 검색 개수
- `include_options`: 13F 옵션 포지션 포함 여부, 기본값 `false`

## 로컬 실행

```bash
pip install -r requirements.txt
export SEC_API_KEY="your_sec_api_key"
python scripts/nps_13f_diff.py
```

Windows PowerShell:

```powershell
pip install -r requirements.txt
$env:SEC_API_KEY="your_sec_api_key"
python scripts/nps_13f_diff.py
```

## 주의사항

13F는 실시간 매매 데이터가 아니라 분기말 보유 현황입니다.

따라서 리포트의 “신규편입”, “보유수량증가”, “보유수량감소”는 실제 체결일 기준 매수/매도가 아니라, 최신 분기말 보유수량과 직전 분기말 보유수량의 차이입니다.

또한 13F는 국민연금의 전체 글로벌 포트폴리오가 아니라 미국 SEC 13F 대상 증권 보유분만 포함합니다.
