# BIGYO

A/B 카드를 스와이프로 고르고, 선택 결과와 포인트를 비교하는 웹 앱입니다.
HTML/CSS/JavaScript 화면과 FastAPI API, SQLite 데이터베이스로 구성했습니다.

## 주요 기능

- 1,000개 밸런스 게임 카드, 사용자별 무작위 순서, 패스와 선택 기록 유지
- 왼쪽 A / 오른쪽 B 스와이프와 버튼 선택, 2초 결과 표시 및 다음 카드 전환
- 카드당 20P 적립, 중복 적립 방지, 포인트 내역과 누적 적립 리더보드
- 초대 링크 복사: 처음 방문한 친구가 첫 카드를 고르면 초대한 사람에게 5분간 카드당 10P 추가
- 출금 신청 접수와 내역 저장, 동일 요청의 중복 차감 및 잔액 초과 차감 방지

**현재 현금 지급과 광고 정산은 연결되어 있지 않습니다.** 출금 신청의 상태는
`received`이며, 지급 완료를 의미하지 않습니다. 실제 계좌 정보는 수집하지 않습니다.
초대는 브라우저별 익명 세션을 기준으로 하므로 계정 인증·부정 가입 방지까지 구현한 상태는 아닙니다.

## 실행

Python 3.14 환경에서 검증했습니다.

```sh
git clone https://github.com/iamabluetiger/BIGYO.git
cd BIGYO
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
./start.sh
```

<http://127.0.0.1:8767/>에서 접속할 수 있습니다. 서버를 종료하려면 Ctrl+C를 누르세요.
`index.html`을 파일로 직접 열면 로컬 저장 모드로 실행되며, 서버의 집계나 초대 기능은 연결되지 않습니다.

## 외부 공유

FastAPI를 실행한 상태에서 다른 터미널을 열어 연결합니다.

```sh
brew install ngrok
.venv/bin/python tunnel.py
```

[ngrok 대시보드](https://dashboard.ngrok.com/get-started/your-authtoken)의 본인 인증 토큰을
숨김 입력란에 붙여 넣습니다. 토큰은 파일이나 명령줄 인수에 저장하지 않고 ngrok 프로세스에
환경 변수로 전달합니다. 접속 주소는 <http://127.0.0.1:4040>에서 확인합니다.

Mac과 FastAPI·ngrok 프로세스가 실행 중이어야 외부에서 접속할 수 있습니다.
상시 운영에는 별도 서버 호스팅이 필요합니다. [ngrok 공식 안내](https://ngrok.com/docs/start)

## DB와 사용자

기본 DB 경로는 `.data/bigyo.sqlite3`이며 `BIGYO_DB` 환경 변수로 바꿀 수 있습니다.
DB와 로그·인증 정보는 Git에 포함하지 않습니다. FastAPI는 명시된 화면과 API만 제공하며,
프로젝트 전체를 정적 파일 서버로 공개하지 않습니다.

| 테이블 | 내용 |
| --- | --- |
| questions | 1,000개 카드 |
| users | 익명 세션 해시, 카드 순서, 패스 기록, 보너스 만료 시각 |
| votes | A/B 선택, 적립액, 선택 시각 |
| ledger | 웰컴 포인트, 선택 적립, 출금 신청 차감 |
| withdrawals | 요청 번호별 출금 신청 내역 |
| referrals | 초대 관계와 친구의 첫 선택 완료 시각 |
| demo_users | 임의로 생성한 예시 참여자 구분 |

HttpOnly 쿠키로 사용자를 식별합니다. 같은 주소·브라우저의 쿠키가 남아 있으면 이어 할 수
있습니다. 쿠키를 지우거나 다른 기기를 사용하면 새 사용자로 시작합니다. localhost와 ngrok
주소도 서로 다른 사용자입니다. 로그인과 계정 복구는 아직 구현하지 않았습니다.

SQLite WAL 모드이므로 실행 중인 DB 백업에는 SQLite `.backup` 또는 Python의
`sqlite3.Connection.backup()`을 사용하세요. ‘내 기록 초기화’는 현재 사용자의 기록만
초기화하며, 초대 완료 내역은 보존해 같은 친구의 보너스가 재지급되지 않도록 합니다.

## 예시 데이터

빈 순위표와 100% 투표 결과를 피하고 화면을 살펴볼 수 있도록 예시 참여자 20명을 추가할 수 있습니다.
서버를 한 번 실행하여 DB를 만든 다음 실행하세요.

```sh
.venv/bin/python seed_demo.py
```

투표 11,038개가 생성되며, 각 카드에 6~16표와 A/B 양쪽 최소 2표가 들어갑니다.
누적 적립은 60~20,650P입니다. 결과에는 ‘예시 데이터 N표 포함’, 순위에는 ‘예시 참여자’를
표시하며 실제 사용자 통계와 구분합니다. 같은 명령을 반복해도 중복 생성되지 않습니다.
기존 사용자 기록은 바뀌지 않으며, 예시 참여자용 로그인 정보는 발급하지 않습니다.

## API

변경 요청에는 `X-Bigyo-Client: web` 헤더가 필요합니다. 브라우저는 동일 출처 쿠키를
사용합니다. CORS는 열지 않았으며 다른 출처의 변경 요청을 거부합니다.

| 경로 | 역할 |
| --- | --- |
| GET /api/health | 서버·DB 상태, 카드 수, 현금 지급 지원 여부 |
| POST /api/session?ref={userId} | 익명 세션 생성·복구, 새 사용자의 초대 관계 기록 |
| GET /api/state | 내 선택·잔액·신청 내역·카드 순서 |
| POST /api/votes | 선택 적립 및 초대받은 사용자의 첫 선택 확인 |
| GET /api/questions/{id}/result | 선택한 카드의 결과와 예시 투표 수 |
| POST /api/skip | 카드 패스 |
| GET /api/leaderboard | 상위 50명과 내 순위, 동점 공동 순위 |
| POST /api/withdrawals | 신청 접수, 요청 번호로 중복 차감 방지 |
| POST /api/reset | 내 선택과 신청 기록 초기화 |

기존 `/api/demo/withdrawals`와 `/api/demo/reset`은 호환 경로로 남아 있습니다.
직접 보너스를 지급하던 `/api/demo/boost`는 410을 반환하며 더 이상 적립하지 않습니다.
초대받은 친구의 첫 선택으로만 보너스가 시작되고, 진행 중인 보너스 시간은 중복 연장되지 않습니다.

## 검증

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

중복·동시 적립, 동시 출금 신청, 초대 자격과 중복 지급 방지, 보너스 만료,
서버 재실행 후 DB 유지, 사용자 분리, 출처 검사, 비공개 파일 접근 차단,
예시 데이터 구분과 재실행 안전성을 검사합니다.

실제 지급을 도입하려면 사용자 인증·계정 복구, 광고 정산 재원, 출금 심사 및 지급 사업자
연동, 부정 적립 방지, 운영 서버·백업 정책을 추가해야 합니다.
