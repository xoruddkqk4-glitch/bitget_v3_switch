# week_00-bitget_v3_switch

비트겟 선물 V3 API 기반 **SOXL/USDT 무손절 롱/숏 DCA 헤지모드 자동매매 시스템**

---

## 📌 1. 시스템 개요 (System Overview)

* **대상 거래소 및 종목:** Bitget USDT 선물 마켓 — `SOXL/USDT:USDT`
* **거래 환경 설정:**
  * **레버리지:** 1X (1배율 저위험 운용)
  * **마진 모드:** Cross (교차 마진)
  * **포지션 모드:** Hedge Mode (롱/숏 독립 동시 보유)
* **타임프레임:** 일봉 (Daily Candle, 매일 00:00 UTC / 09:00 KST 일봉 마감 시점 1회 스케줄링 실행)
* **핵심 운용 컨셉:**
  * **A 전략 (Long DCA):** 음봉/급락 시 2회 더미 소진 후 동적 분할 매수 $\rightarrow$ 60일선 조건부 독립 익절
  * **B 전략 (Short DCA):** 양봉/급등 시 2회 더미 소진 후 동적 분할 숏 진입 $\rightarrow$ 60일선 대칭 조건부 독립 익절
  * **Hedge Mode 무손절 독립 운용:** 강제 스위칭 손절 없이, A/B 각 전략이 자신의 청산 목표가에 도달할 때까지 독립적으로 버텨서 익절 탈출

---

## 🛡️ 2. 자금 관리 및 더미(Dummy) 메커니즘

1. **동적 1.0 Unit 산출 공식:**
   $$\text{Current Unit Size (\$)} = \frac{\text{현재 계좌 총 평가 잔고 (Free Margin + Position Margin)}}{10}$$
   * 매 진입 시점마다 실시간 총 평가 잔고를 수집하여 1 Unit의 기본 $ 금액을 동적으로 재계산합니다.

2. **유닛 가중치 (실제 매수 회차 기준):**
   * **더미 1~2회차:** $\$0$ (0원 매수, 더미 카운트만 소진)
   * **실제 매수 1~4회차 (`executed_units`: 0~3):** $1.0 \times \text{Unit Size}$ (기본 1 유닛)
   * **실제 매수 5회차 이상 (`executed_units` $\ge 4$):** $1.25 \times \text{Unit Size}$ (비중 확대 유닛 적용)

3. **더미(Dummy) 방어 시스템:**
   * 새 싸이클 시작 후 진입 조건이 충족되더라도 **1~2회차 신호는 0원 매수(더미 소진)** 처리합니다.
   * **3회차 신호부터 실제 1.0 Unit 주문**을 집행하여, 초입 물리거나 뇌동매매되는 현상을 구조적으로 방지합니다.

4. **매도 우선 원칙:**
   * 당일 매도(청산)와 매수 신호가 동시 발생할 경우 **매도를 우선 실행**합니다.
   * 청산 완료 당일 발생한 매수 신호는 **신규 싸이클의 1차 더미**로 이관 처리합니다.

---

## 📈 3. 전략별 세부 매매 조건

### **A 전략: Long DCA (`strategy/strategy_a_long.py`)**
1. **진입 조건 (OR):**
   * 당일 음봉 $\le -1.5\%$ ($\text{종가}/\text{시가} - 1 \le -0.015$)
   * 전일 대비 등락률 $\le -3.0\%$ ($\text{종가}/\text{전일종가} - 1 \le -0.030$)
2. **2단계 스마트 트레일링 청산 규칙:**
   * **1단계 확인 (09:10 ~ 09:15 KST, 일봉 마감 기준):**
     * **60일선 상승 중:** Target = $SMA_5 \times 1.02$ 이상 다다를 때 $\rightarrow$ **2단계 감시 모드 돌입** (`trailing_mode = True`, 보존선 설정)
     * **60일선 하락 & 완만한 하락 ($\text{낙폭률} > -30\%$):** Target = $SMA_5 \times 0.99$ 이하 다다를 때 $\rightarrow$ **즉시 시장가 전량 청산 (빠른 본탈/약손절)**
     * **60일선 하락 & 과낙폭 ($\text{낙폭률} \le -30\%$):** Target = $SMA_5 \times 1.03$ 이상 다다를 때 $\rightarrow$ **2단계 감시 모드 돌입** (과낙폭 반등 추세 감시)
   * **2단계 5분 주기 감시 (Crontab `*/5 * * * *`):**
     * **추세 꺾임:** 현재가 $<$ 4시간봉 $5MA$ 하향 이탈 **OR**
     * **이익 방어:** 현재가 $\le$ 최소 이익 보존선 하회 시 $\rightarrow$ **롱 전량 익절 청산!**

---

### **B 전략: Short DCA (`strategy/strategy_b_short.py`)**
1. **진입 조건 (OR):**
   * 당일 양봉 $\ge +1.5\%$ ($\text{종가}/\text{시가} - 1 \ge +0.015$)
   * 전일 대비 등락률 $\ge +3.0\%$ ($\text{종가}/\text{전일종가} - 1 \ge +0.030$)
2. **2단계 스마트 트레일링 청산 규칙 (대칭 구조):**
   * **1단계 확인 (09:10 ~ 09:15 KST, 일봉 마감 기준):**
     * **60일선 상승 중 (단기 눌림):** Target = $SMA_5 \times 0.98$ 이하 다다를 때 $\rightarrow$ **2단계 감시 모드 돌입** (`trailing_mode = True`, 보존선 설정)
     * **60일선 하락 & 완만한 반등 ($\text{반등폭} < +30\%$):** Target = $SMA_5 \times 1.01$ 이하 다다를 때 $\rightarrow$ **즉시 시장가 전량 청산 (빠른 본탈/약손절)**
     * **60일선 하락 & 과반등 후 깊은 눌림 ($\text{반등폭} \ge +30\%$):** Target = $SMA_5 \times 0.97$ 이하 다다를 때 $\rightarrow$ **2단계 감시 모드 돌입** (눌림 추세 감시)
   * **2단계 5분 주기 감시 (Crontab `*/5 * * * *`):**
     * **추세 반등:** 현재가 $>$ 4시간봉 $5MA$ 상향 돌파 **OR**
     * **이익 방어:** 현재가 $\ge$ 최소 이익 보존 상한선 상회 시 $\rightarrow$ **숏 전량 익절 청산!**


---

## 🗂️ 4. 프로젝트 패키지 구조 (Package Structure)

```text
week_00-bitget_v3_switch/
├── docs/                                  # 계획서 및 명세서 전용 격리 폴더
│   ├── prd/
│   │   └── bitget_v3_switch_prd.md         # 요구사항 명세서 (PRD)
│   └── implementation/
│       └── implementation_plan.md         # 아키텍처 및 상세 구현 계획서
│
├── .agents/                               # 에이전트 실행 규칙 및 특화 스킬 세트
│   ├── rules/
│   │   └── rules.md                       # 터미널 고속 검증 및 커밋 정책
│   └── skills/
│       ├── ask/SKILL.md                   # /ask (코드 수정 없는 질의/계획 모드)
│       ├── apply/SKILL.md                 # /apply (계획서 소스 코드 즉시 반영 모드)
│       ├── git-commit/SKILL.md            # /git-commit (README 이력 갱신 및 커밋/푸시)
│       └── scratchpad/SKILL.md            # /scratchpad (브라우저 시각 검증 모드)
│
├── api/                                   # 거래소 API 연동 계층
│   ├── __init__.py
│   └── bitget_client.py                   # ccxt 선물 래퍼 (Hedge Mode, 잔고, OHLCV, 모의매매)
│
├── strategy/                              # 지표 계산 및 A/B 전략 로직
│   ├── __init__.py
│   ├── base.py                            # 전략 기본 추상 클래스 (BaseStrategy)
│   ├── indicator.py                       # SMA5, SMA60, 낙폭률/반등폭 산출 모듈
│   ├── strategy_a_long.py                 # Strategy A (Long DCA 조건 판단)
│   └── strategy_b_short.py                # Strategy B (Short DCA 조건 판단)
│
├── manager/                               # 사이클 영속성 및 주문 제어 계층
│   ├── __init__.py
│   ├── cycle_manager.py                   # 더미 및 가중 유닛, 원자적 state.json 제어
│   └── trader.py                          # 매도 우선 원칙 및 주문 집행 통합 오케스트레이터
│
├── utils/                                 # 공용 유틸리티 계층
│   ├── __init__.py
│   ├── logger.py                          # 10MB 자동 로테이팅 및 콘솔 로거
│   ├── excel_logger.py                    # trade_history.xlsx 누적 기록기
│   └── notifier.py                        # 텔레그램 일일 보고 및 에러 알림기
│
├── config.py                              # 시스템 전역 파라미터 및 환경 설정
├── state.json                             # A/B 전략 사이클, 더미, 평단가 영속 파일
├── trade_history.xlsx                     # 거래/더미 이력 엑셀 파일 (자동 생성)
├── main.py                                # 일봉 마감 실행 파이프라인 엔트리 포인트
├── AGENTS.md                              # 프로젝트 루트 상시 규칙 파일
├── .env.example                           # 환경변수 템플릿
├── .gitignore                             # 보안 파일 및 캐시 무시 설정
└── README.md                              # 프로젝트 종합 문서 및 누적 업데이트 이력
```

---

## 🚀 5. 실행 방법 (Usage)

### 1) 환경 설정 (`.env`)
`.env.example` 파일을 복사하여 `.env`를 생성하고 실제 API 키와 텔레그램 토큰을 설정합니다:
```bash
cp .env.example .env
```
*(※ `PAPER_TRADING=true` 설정 시 실제 주문 없이 가상 잔고로 안전하게 파이프라인을 테스트할 수 있습니다.)*

### 2) 단일 즉시 실행 (일봉 1차 테스트용)
```bash
python main.py --run-once
```

### 3) Crontab 5분 주기 실행 (권장: 1차 평가 + 2차 감시 자동화)
리눅스/서버 환경의 `crontab -e`에 5분 주기(`*/5 * * * *`)로 등록합니다:
```bash
*/5 * * * * cd /path/to/week_00-bitget_v3_switch && /path/to/python main.py --cron >> logs/cron.log 2>&1
```
* **09:10 ~ 09:15 KST:** 일봉 1차 마감 평가 및 매수 진입 파이프라인 1회 가동
* **그 외 시간대:** 2단계 트레일링 활성 시에만 4시간봉 감시 수행, 비활성 시 **0.1초 즉시 무부하 종료**

### 4) 상시 백그라운드 스케줄러 실행 (데몬 모드)
```bash
python main.py --loop
```
* 프로세스를 계속 띄워두고 5분마다 자동으로 틱을 감시합니다.

### 5) 엑셀 손익차트(종합 / V3+ / V3-) 수동 갱신
```bash
python main.py --update-chart
```
* `trade_history.xlsx` 내 매매 기록을 기반으로 상단 통합 KPI 카드 및 3종 콤보 차트(종합/V3+/V3-)를 즉시 재빌드하여 갱신합니다.

---

## 🌐 6. AWS EC2 실전 배포 및 Crontab 설정 가이드

AWS Ubuntu 서버(예: `t3.micro` 프리티어)에서 24시간 365일 무중단으로 자동매매를 구동하는 상세 가이드입니다.

### 1) 서버 생성 및 타임존(Asia/Seoul) 설정
```bash
# 1. 서버 SSH 접속
ssh -i your-key.pem ubuntu@<EC2_공인_IP>

# 2. 서버 타임존을 서울 시각(KST)으로 변경
sudo timedatectl set-timezone Asia/Seoul
date  # KST 확인
```

### 2) 필수 패키지 설치 및 가상환경 구성
```bash
# 시스템 업데이트 및 도구 설치
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-venv git

# 프로젝트 클론 및 가상환경 생성
git clone https://github.com/xoruddkqk4-glitch/bitget_v3_switch.git
cd bitget_v3_switch
python3 -m venv venv
source venv/bin/activate

# 의존성 패키지 설치
pip install --upgrade pip
pip install -r requirements.txt
```

### 3) 실전 환경변수 설정 (`.env`)
```bash
cp .env.example .env
nano .env
```
* `BITGET_API_KEY`, `BITGET_SECRET`, `BITGET_PASSPHRASE` 입력
* `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 입력
* **`PAPER_TRADING=false`** (실제 주문 집행을 위해 반드시 `false`로 변경!)

### 4) Crontab 5분 주기 등록
```bash
crontab -e
```
맨 하단에 다음 1줄을 등록합니다 (경로는 `pwd` 및 `which python` 확인값 기준):
```cron
*/5 * * * * cd /home/ubuntu/bitget_v3_switch && /home/ubuntu/bitget_v3_switch/venv/bin/python main.py --cron >> /home/ubuntu/bitget_v3_switch/logs/cron.log 2>&1
```

### 5) 로그 실시간 모니터링
```bash
# 크론탭 실행 틱 로그 확인
tail -f /home/ubuntu/bitget_v3_switch/logs/cron.log

# 트레이딩 체결 상세 로그 확인
tail -f /home/ubuntu/bitget_v3_switch/logs/trading.log
```

---

## 📜 변경 및 업데이트 이력 (Cumulative Update History)
- 본 섹션은 `.agents/rules/rules.md` (Rule 4)에 따라 `/git-commit` 실행 시마다 최하단에 누적 기록됩니다.

## [2026-10-07 00:12] 업데이트 이력 (Commit ID: b1a2f34)
- **수정 내용**: 
  - `.agents` 에이전트 실행 규칙 및 커스텀 스킬(`apply`, `ask`, `git-commit`, `scratchpad`) 프로젝트 적용
  - 프로젝트 전역 실행 규칙 파일(`AGENTS.md`) 및 환경 보호 설정(`.gitignore`) 생성
  - 비트겟 V3 스위치 자동매매 시스템 요구사항 명세서(`bitget_v3_switch_prd.md`) 등록
  - GitHub 원격 저장소(`https://github.com/xoruddkqk4-glitch/bitget_v3_switch`) 연동 및 Git 초기화
- **검증 결과**:
  - Git remote 및 `main` 브랜치 설정 정상 검증 완료
  - `.gitignore` API 키 및 환경 파일 격리 규칙 검증 완료
  - 프로젝트 규칙 및 스킬 인식 상태 정상 확인

## [2026-10-07 00:30] 업데이트 이력 (Commit ID: 5d754bf)
- **수정 내용**: 
  - 계획서 파일 격리 보관 정책 수립 및 문서 재배치 (`docs/prd/bitget_v3_switch_prd.md`, `docs/implementation/implementation_plan.md`)
  - 에이전트 실행 규칙(`AGENTS.md`, `rules.md`, `ask/SKILL.md`, `apply/SKILL.md`)에 `docs/` 계획서 격리 원칙 반영
  - 비트겟 V3 스위치 일봉 헤지모드 무손절 DCA 자동매매 시스템 전체 모듈 구현:
    - `config.py` & `.env.example`: 10분할 동적 유닛, 2회 더미 방어, 1.25x 가중치, A/B 전략 파라미터, 모의매매 플래그
    - `utils/`: 일자별 파일 로테이션 로깅(`logger.py`), PRD 규격 엑셀 누적 기록기(`excel_logger.py`), 텔레그램 알림기(`notifier.py`)
    - `api/bitget_client.py`: ccxt 선물 래퍼 (Hedge Mode, Cross Margin, 1X 레버리지, 실시간 잔고/일봉 수집 및 모의 매매 지원)
    - `strategy/`: 추상 클래스(`base.py`), 지표 계산기(`indicator.py` - SMA5, SMA60 방향, 낙폭률/반등폭), Strategy A Long(`strategy_a_long.py`), Strategy B Short(`strategy_b_short.py`)
    - `manager/`: 원자적 상태 파일(`state.json`), 사이클 및 더미 제어(`cycle_manager.py`), 매도 우선 원칙 오케스트레이터(`trader.py`)
    - `main.py`: 일봉 마감(09:00 KST / 00:00 UTC) 단일 실행(`--run-once`) 및 상시 스케줄링(`--loop`) 엔트리 포인트
- **검증 결과**:
  - `python -m compileall -q .` 정적 구문 컴파일 검증 통과 (에러 0건)
  - `python main.py --run-once` 실전 모의 파이프라인 정상 구동 검증 (Bitget 실제 일봉 90개 수집, 지표 산출, 포지션 평가, 엑셀 및 상태 파일 기록 정상 확인)

## [2026-10-07 00:33] 업데이트 이력 (Commit ID: 1621b0d)
- **수정 내용**: 
  - `README.md` 문서 전면 개편: 계획서(PRD 및 구현 계획서) 기반 시스템 개요, 자금 관리 및 더미 방어 메커니즘, A/B 전략 매매 조건, 상세 패키지 디렉터리 구조 및 실행 가이드 추가
- **검증 결과**:
  - 문서 마크다운 렌더링 및 디렉터리/파일 경로 일치 검증 완료

## [2026-10-07 16:42] 업데이트 이력 (Commit ID: 04df543)
- **수정 내용**: 
  - V3+ (Long DCA) 및 V3- (Short DCA) 전략에 **2단계 스마트 트레일링 매도(청산) 시스템** 구현 및 연동:
    - **1단계 확인 (09:10~09:15 KST):** 상승장 익절 및 과낙폭/과반등 익절 시 조기 청산 대신 `trailing_mode` 활성화 및 최소 이익 보존선 설정, 완만한 하락/반등 본탈은 즉시 청산
    - **2단계 5분 주기 감시:** 롱은 4시간봉 5MA 하향 이탈 or 보존선 하회 시 전량 매도, 숏은 4시간봉 5MA 상향 돌파 or 보존선 상회 시 전량 청산
  - `config.py`: 4시간봉 타임프레임(`TIMEFRAME_4H`, `CANDLE_LIMIT_4H`, `SMA_4H_PERIOD`), 일봉 1차 평가 시간 윈도우(09:10~09:15 KST), 슬리피지 버퍼(0.2%) 설정 추가
  - `strategy/indicator.py`: 4시간봉 5MA 산출 및 롱 하향 이탈/숏 상향 돌파 감지 메서드(`calculate_4h`) 구현
  - `strategy/strategy_a_long.py` & `strategy/strategy_b_short.py`: 1차 평가(`check_stage1_exit_signal`) 및 2차 평가(`check_stage2_exit_signal`) 분리 구현
  - `manager/cycle_manager.py` & `state.json`: 트레일링 모드 및 보존선 영속 필드 추가, 청산 리셋 연동
  - `manager/trader.py`: 트레일링 활성 전략 신규 매수 진입 방지 및 5분 주기 전용 감시 파이프라인(`process_stage2_monitoring`) 구현
  - `main.py`: Crontab(`*/5 * * * *`) 최적화 초경량 틱 모드(`--cron`), 5분 상시 루프(`--loop`), 2단계 감시 테스트(`--stage2`) 추가
- **검증 결과**:
  - `python -m py_compile` 전 파일 구문 컴파일 오류 0건 통과
  - `python main.py --cron` 5분 틱 모드 검증 완료 (트레일링 미활성 시 0.1초 만에 무부하 정상 종료)
  - `python main.py --run-once` 실제 Bitget SOXL 캔들 수집 및 일봉 1차 파이프라인 정상 가동 확인
  - `python main.py --stage2` 4시간봉 2단계 트레일링 파이프라인 구동 검증 완료

## [2026-10-07 17:52] 업데이트 이력 (Commit ID: 802d580)
- **수정 내용**: 
  - `requirements.txt` 패키지 의존성 파일 신규 생성 (`ccxt`, `pandas`, `python-dotenv`, `openpyxl`, `requests`)
  - `README.md`에 AWS EC2 실전 배포 및 Crontab 5분 주기 등록 상세 가이드(섹션 6) 추가
- **검증 결과**:
  - `requirements.txt` 라이브러리 정합성 확인
  - 마크다운 문서 렌더링 및 명령어 유효성 검증 완료

## [2026-10-07 18:20] 업데이트 이력 (Commit ID: 2a206aa)
- **수정 내용**: 
  - **계좌 총 평가 잔고 및 1.0 Unit 기본 금액 표기**:
    - 거래소 실시간 계좌 총 평가 잔고(`total_balance`)와 분할 계산된 1.0 Unit 기본 금액(`unit_base_usd = total_balance / UNIT_DIVISOR`)을 일봉 마감 보고 및 청산 보고 텔레그램 상단에 명시
  - **한국 서울 기준 시간(KST, UTC+9) 일원화**:
    - `api/bitget_client.py`: 일봉 캔들 타임스탬프(UTC)를 서울 시간(`Asia/Seoul`, `UTC+9`)으로 변환하여 `candle_date`에 저장
    - `main.py`: 보고 기준 일시, 일봉 기준일, 시스템 에러 발생 시각을 모두 `YYYY-MM-DD HH:MM:SS KST`로 일원화
    - `manager/trader.py`: 2단계 트레일링 청산 발생 시 알림 기준 일시를 KST로 일원화
    - `manager/cycle_manager.py`: `state.json` 내 `last_updated` 타임스탬프를 KST 시간으로 기록
  - **각 전략별 실시간 수익금(평가손익 및 누적 실현손익) 상세 표기**:
    - 전략 A(Long DCA) / 전략 B(Short DCA)별로 현재 보유 포지션의 미실현 평가손익(`+$XX.XX USD (+X.XX%)` 또는 `$0.00 USD (0.00%) [포지션 없음]`) 계산 및 출력
    - 청산 시 확정 손익을 영속 저장하기 위해 `manager/cycle_manager.py`에 `record_cycle_realized_pnl()` 메서드와 `cumulative_realized_pnl`, `last_realized_pnl`, `last_realized_pct` 필드 추가
    - 일봉 보고 메시지에 각 전략별 누적 실현손익 및 직전 청산 확정 수익 표기
    - 2단계 트레일링 청산 알림에도 확정 수익금 및 수익률 명시
- **검증 결과**:
  - `python -m py_compile` 전 파일 구문 컴파일 오류 0건 통과
  - `python main.py --run-once` 테스트 실행을 통해 텔레그램 실제 메시지 전송 및 KST 타임스탬프, 평가 잔고, 1 unit 기본 금액, 전략별 수익금 정보 표기 정상 검증 완료

## [2026-10-07 18:25] 업데이트 이력 (Commit ID: ea039b0)
- **수정 내용**: 
  - **런타임 상태 및 히스토리 데이터 Git 추적 제외**:
    - `.gitignore`에 `state.json`, `*.tmp`, `trade_history.xlsx` 등록
    - `git rm --cached`를 통해 실서버(AWS EC2) 런타임 데이터 파일들을 Git 추적 인덱스에서 안전하게 제거
    - AWS EC2 환경에서 봇 동작 중 `git pull origin main` 수행 시 로컬 상태 파일 충돌(conflict) 원천 차단 및 실서버 포지션/평단가 데이터 보호
  - **참조용 기본 상태 템플릿 생성**:
    - 신규 클론 환경 참고용 `state.example.json` 템플릿 파일 추가
- **검증 결과**:
  - `git rm --cached` 수행 후 로컬 물리 파일(`state.json`, `trade_history.xlsx`) 무결성 및 보존 확인 완료
  - `python -m py_compile` 전 파일 구문 컴파일 오류 0건 통과

## [2026-10-07 18:46] 업데이트 이력 (Commit ID: 5e91b2c)
- **수정 내용**: 
  - `떨사오팔봇` 엑셀 양식을 준용한 비트겟 V3 스위치 맞춤형 **'손익차트' 시트 및 3종 콤보 차트 대시보드** 구현:
    - `utils/excel_logger.py`:
      - `TradeHistory` 시트 스키마에 `실현손익 ($)`, `수익률 (%)` 수치 컬럼 추가 및 하위 호환 정규식 파싱(Fallback) 지원
      - `손익차트` 워크시트 자동 생성 및 상단 통합 KPI 요약 카드(누적 실현손익, 청산 완료 건수, 미청산 보유 현황, 기준시각 KST) 배치
      - 일자별 종합 / V3+(Long DCA) / V3-(Short DCA) 일일 손익 및 누적 손익 상세 집계 테이블(A8:G...) 자동 기록
      - `openpyxl` 콤보 차트(BarChart 일일손익 + LineChart 누적손익) 3종 생성 및 앵커링:
        * 차트 1: 종합 실현손익 추이 (`I2`, Royal Blue / Crimson Red)
        * 차트 2: V3+ 전략 (Long DCA) 실현손익 추이 (`I18`, Sky Blue / Navy Blue)
        * 차트 3: V3- 전략 (Short DCA) 실현손익 추이 (`I34`, Soft Orange / Deep Amber)
        * X축 날짜 세로 90도 회전(`rot="-5400000"`), Y축 천단위 구분(`#,##0`), 범례 우측 배치, 폰트 및 테두리 완비
    - `manager/trader.py`:
      - 일봉 1차 즉시 청산 및 4H 2단계 트레일링 청산 집행 시 `excel.append_record`에 확정 `realized_pnl`과 `pnl_pct` 수치 전달 연동
    - `main.py`:
      - 일봉 마감 파이프라인(`run_pipeline`) 완료 시 `excel_logger.update_pnl_chart()` 자동 갱신 연동
      - CLI 인자 `--update-chart` 추가로 언제든 손익차트 시트만 즉시 재빌드/갱신 지원
    - `docs/implementation/pnl_chart_implementation_plan.md`: 손익차트 상세 설계 및 구현 계획서 문서화
- **검증 결과**:
  - `python -m py_compile main.py manager/trader.py utils/excel_logger.py` 구문 컴파일 에러 0건 통과
  - `python main.py --update-chart` CLI 테스트 실행 및 `trade_history.xlsx` 내 '손익차트' 시트 정상 생성 검증 완료
