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

1. **양방향 헤지 동적 1.0 Unit 산출 공식:**
   $$\text{Current Unit Size (\$)} = 
   \begin{cases} 
   \cfrac{\text{Free Margin (실시간 가용 잔고)}}{10.0}, & \text{if 상대편 포지션 보유 중 (양방향 헤지, opposite\_qty > 0)} \\[10pt]
   \cfrac{\text{Total Balance (총 평가 잔고)}}{10.0}, & \text{if 상대편 포지션 없음 (단일 포지션, opposite\_qty = 0)}
   \end{cases}$$
   * 매 진입 시점마다 실시간 잔고를 수집하여 상대편 포지션 유무에 따라 1 Unit 기본 금액을 동적으로 재계산합니다.
   * **최소 주문금액 가드:** 주문금액이 최소 한도(`MIN_ORDER_USD = 5.0 USDT`) 미만일 경우 안전 스킵(Skip)합니다.

2. **유닛 가중치 (실제 매수 회차 기준):**
   * **더미 1~2회차:** $\$0$ (0원 매수, 더미 카운트만 소진)
   * **실제 매수 1~4회차 (`executed_units`: 0~3):** $1.0 \times \text{Unit Size}$ (기본 1 유닛)
   * **실제 매수 5회차 이상 (`executed_units` $\ge 4$):** $1.25 \times \text{Unit Size}$ (비중 확대 유닛 적용)

3. **더미(Dummy) 방어 시스템 및 미완성 더미 리셋:**
   * 새 싸이클 시작 후 진입 조건이 충족되더라도 **1~2회차 신호는 0원 매수(더미 소진)** 처리합니다.
   * **3회차 신호부터 실제 1.0 Unit 주문**을 집행하여, 초입 물리거나 뇌동매매되는 현상을 구조적으로 방지합니다.
   * **상대 전략 실제 매수(`BUY_UNIT`) 체결 시 미완성 더미 리셋:**
     * 어느 한쪽 전략이 실제 매수를 체결하면, 반대편 전략의 미완성 더미(`executed_units == 0 and dummy_count > 0`)를 즉시 **`0 / 2`로 초기화**하여 유령 더미를 제거합니다.
     * 단, 익절 청산(`SELL_CLEAR`) 시에는 반대편 더미를 초기화하지 않고 유지하며, 매수 체결 이후 발생한 신호는 정상 더미로 누적합니다.

4. **매도 우선 원칙:**
   * 당일 매도(청산)와 매수 신호가 동시 발생할 경우 **매도를 우선 실행**합니다.
   * 청산 완료 당일 발생한 매수 신호는 **신규 싸이클의 1차 더미**로 이관 처리합니다.

5. **더미(2차) 및 실제 매수(10차) 단일 트랙 시각 인디케이터:**
   * 텔레그램 일일 보고서 및 주문 집행 로그에서 현재 진행 상태를 직관적으로 파악할 수 있도록 단일 트랙 바 제공:
     * `• 매수 차수: [더미: 🟨⬛ ┆ 실제: ⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛] (더미 1/2, 실제 0/10)`
   * 기본적으로 선명한 노란색(`🟨`)과 검정색(`⬛`) 고대비 사각 블록을 적용하며, 설정(`config.py`)에 따라 체크 박스(`☑️`, `⬜`)로도 손쉽게 교체 가능

---

## 📈 3. 전략별 세부 매매 조건

### **A 전략: Long DCA (`strategy/strategy_a_long.py`)**
1. **진입 조건 (OR, 전일 마감 완성봉 기준):**
   * 전일 음봉 $\le -1.5\%$ ($\text{종가}/\text{시가} - 1 \le -0.015$)
   * 전일 대비 등락률 $\le -3.0\%$ ($\text{전일종가}/\text{전전일종가} - 1 \le -0.030$)
2. **2단계 스마트 트레일링 청산 규칙:**
   * **1단계 확인 (09:10 ~ 09:15 KST, 일봉 마감 기준):**
     * **60일선 상승 중:** Target = $SMA_5 \times 1.02$ 이상 다다를 때 $\rightarrow$ **2단계 감시 모드 돌입** (`trailing_mode = True`, 보존선 설정)
     * **60일선 하락 & 완만한 하락 ($\text{낙폭률} > -30\%$):** 현재가가 Target = $SMA_5 \times 0.99$ 이상으로 회복할 때 $\rightarrow$ **즉시 시장가 전량 청산 (반등 시 빠른 본탈)**
     * **60일선 하락 & 과낙폭 ($\text{낙폭률} \le -30\%$):** Target = $SMA_5 \times 1.03$ 이상 다다를 때 $\rightarrow$ **2단계 감시 모드 돌입** (과낙폭 반등 추세 감시)
   * **2단계 5분 주기 감시 (Crontab `*/5 * * * *`):**
     * **추세 꺾임:** 트레일링 돌입 이후 마감된 **4시간봉 완성봉 종가** $<$ 완성봉 $5MA$ **OR**
     * **이익 방어:** 실시간 현재가 $\le$ 최소 이익 보존선 하회 시 $\rightarrow$ **롱 전량 익절 청산!**
     * **보존선 래칫:** 감시 중 일봉 재평가 목표가가 더 높으면 보존선을 상향 (하향 없음)

---

### **B 전략: Short DCA (`strategy/strategy_b_short.py`)**
1. **진입 조건 (OR, 전일 마감 완성봉 기준):**
   * 전일 양봉 $\ge +1.5\%$ ($\text{종가}/\text{시가} - 1 \ge +0.015$)
   * 전일 대비 등락률 $\ge +3.0\%$ ($\text{전일종가}/\text{전전일종가} - 1 \ge +0.030$)
2. **2단계 스마트 트레일링 청산 규칙 (대칭 구조):**
   * **1단계 확인 (09:10 ~ 09:15 KST, 일봉 마감 기준):**
   * **60일선 상승 중 (단기 눌림):** Target = $SMA_5 \times 0.98$ 이하 다다를 때 $\rightarrow$ **2단계 감시 모드 돌입** (`trailing_mode = True`, 보존선 설정)
   * **60일선 하락 & 완만한 반등 ($\text{반등폭} < +30\%$):** Target = $SMA_5 \times 1.01$ 이하 다다를 때 $\rightarrow$ **즉시 시장가 전량 청산 (빠른 본탈/약손절)**
   * **60일선 하락 & 과반등 후 깊은 눌림 ($\text{반등폭} \ge +30\%$):** Target = $SMA_5 \times 0.97$ 이하 다다를 때 $\rightarrow$ **2단계 감시 모드 돌입** (눌림 추세 감시)
   * **2단계 5분 주기 감시 (Crontab `*/5 * * * *`):**
     * **추세 반등:** 트레일링 돌입 이후 마감된 **4시간봉 완성봉 종가** $>$ 완성봉 $5MA$ **OR**
     * **이익 방어:** 실시간 현재가 $\ge$ 최소 이익 보존 상한선 상회 시 $\rightarrow$ **숏 전량 익절 청산!**
     * **보존선 래칫:** 감시 중 일봉 재평가 목표가가 더 낮으면 보존선을 하향 (상향 없음)


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

## [2026-10-09 16:55] 업데이트 이력 (Commit ID: 4ea1e1d)
- **수정 내용**: 
  - **일봉 마감 신호 평가 기준점 정상화 (당일 미완성봉 -> 전일 마감 완성봉)**:
    - 문제 원인: 기존 코드가 당일 아침 막 열린 미완성 일봉(`df.iloc[-1]`)을 기준으로 등락률을 산출하여, 전일 발생한 대폭락(-11.13%)이 누락되고 매수/더미 신호가 발생하지 않던 결함 확인
    - `strategy/indicator.py`:
      * `candle_change`(음봉/양봉 여부) 및 `daily_change`(전일비 등락률) 산출 기준을 방금 09:00 마감 완료된 **전일 완성봉(`df.iloc[-2]`)** 및 전전일 종가(`df.iloc[-3]`)로 변경
      * 일봉 5일선(`sma5`), 60일선(`sma60`) 및 60일선 상승 추세 판정도 전일 마감 완성봉 기준으로 일원화
      * 신호 발생 후 주문 집행 및 평가손익 계산에 사용되는 `current_price`는 오늘 실시간 현재가(`df.iloc[-1]`)를 유지
      * 지표 딕셔너리의 `candle_date`에 평가 대상 완성봉 일시(`df['datetime'].iloc[-2]`) 명시
    - `main.py`:
      * 텔레그램 일일 보고서 템플릿에 `일봉 기준일: YYYY-MM-DD (전일 마감 완성봉)` 및 `실시간 현재가 / 기준봉 종가`를 분리하여 명확하게 표기하도록 개선
    - `README.md`: 섹션 3의 A/B 전략 진입 조건 설명에 전일 마감 완성봉 기준 명시
    - `docs/implementation/implementation_plan.md`: 전일 완성봉 기준점 정상화 상세 분석 및 구현 계획서 작성
- **검증 결과**:
  - `python -m py_compile strategy/indicator.py main.py manager/trader.py` 구문 검사 오류 0건 통과
  - Bitget 실제 `SOXL/USDT:USDT` 90일 일봉 데이터 주입 단위 테스트 수행:
    * 기준 완성봉 일시: `2026-10-08 09:00:00 (KST)`
    * 캔들 등락률: `-11.13%`, 전일 대비 등락률: `-11.13%` (어제 폭락 정상 포착)
    * 롱 진입 신호 판정: `Entry signal met: True` (음봉 하락 및 전일비 급락 동시 충족으로 정상 발생 확인)

## [2026-10-10 14:05] 업데이트 이력 (Commit ID: 9b52c7c)
- **수정 내용**: 
  - **더미(2차) 및 실제 매수(10차) 단일 트랙 하이브리드 시각 인디케이터 구현**:
    - `utils/formatters.py` (신규):
      * `format_progress_single_track(dummy_count, executed_units, max_dummy=2, max_units=10)` 함수 구현
      * 하나의 일체형 대괄호 트랙 안에 더미 2칸과 실제 매수 10칸을 구분선(`┆`)으로 연결하고, 우측에 수치 카운트(`(더미 X/2, 실제 Y/10)`)를 병기하는 단일 트랙 하이브리드 인디케이터 바 구현
    - `config.py`:
      * `PROGRESS_FILLED_ICON`: 체결 완료/소진 차수 아이콘 (기본값: 노란색 사각 블록 `🟨`)
      * `PROGRESS_EMPTY_ICON`: 미체결/빈 슬롯 차수 아이콘 (기본값: 검정색 사각 블록 `⬛`)
      * 사용자 선호에 따라 체크 박스(`☑️`, `✅`) 및 빈 네모(`⬜`)로 즉시 스위칭 가능하도록 설정 분리
    - `utils/__init__.py`:
      * `format_progress_single_track` 외부 export 추가
    - `main.py`:
      * 텔레그램 일봉 마감 보고서(`summary_msg`)의 전략 A(Long DCA) 및 전략 B(Short DCA) 블록에 `• 매수 차수:` 시각화 바 라인 추가
    - `manager/trader.py`:
      * 일일 주문 집행 로그(`execution_logs`)의 더미 소진 완료, 1차 더미 이관 및 실전 매수 체결 시 실시간 차수 인디케이터 연동
    - `docs/implementation/implementation_plan.md`:
      * 단일 트랙 인디케이터 설계 및 진행 단계별 텔레그램 메시지 예시 문서화
    - `README.md`:
      * 섹션 2 '자금 관리 및 더미 메커니즘'에 단일 트랙 시각 인디케이터 안내 명시
- **검증 결과**:
  - `python -m py_compile config.py utils/formatters.py utils/__init__.py manager/trader.py main.py` 전 파일 구문 컴파일 오류 0건 통과
  - 0단계 초기 상태부터 12단계 풀매수 완료까지 단계별 인디케이터 문자열 생성 및 정렬 정상 동작 검증 완료

## [2026-10-11 12:10] 업데이트 이력 (Commit ID: a76c82c)
- **수정 내용**: 
  - **양방향 헤지 동적 자금 관리 (Free Margin 1/10 & Total Balance 1/10) 구현**:
    - 상대편 포지션 보유 중(`opposite_qty > 0`, 양방향 헤지 상태): 남은 **가용 잔고(Free Margin)의 1/10**을 기본 유닛으로 계산하여 가용 잔고 안에서 안정적인 10차수 DCA 분할 매수 지속
    - 상대편 포지션 미보유(`opposite_qty == 0`, 단일 포지션 상태): **총 평가 잔고(Total Balance)의 1/10**을 기본 유닛으로 계산
    - 한쪽 포지션 익절 청산 완료 시 잔여 포지션의 후속 차수는 단일 포지션 기준으로 자동 복귀되어 **마틴게일식 대형 유닛이 투입되는 평단가 희석 효과** 달성
    - `config.py`: Bitget 선물 최소 주문 한도(`MIN_ORDER_USD = 5.0`) 상수 추가
    - `api/bitget_client.py`: `fetch_account_balance()` 메서드 구현 (총 평가 잔고 및 실시간 가용 잔고 동시 반환, 모의 매매 시 실감형 가용 잔고 산출)
    - `manager/trader.py`:
      * 롱/숏 실전 매수 진입부에서 상대편 포지션 보유 여부 검사 및 `unit_base_usd` 동적 분기
      * 주문 금액이 5 USDT 미만일 경우 `Insufficient Margin` 방지 및 안전 스킵(`MIN_ORDER_USD` 가드)
    - `main.py`: 일봉 마감 텔레그램 보고서에 실시간 가용 잔고 및 단일/헤지 기준 1.0 Unit 기본 금액 동시 표기
  - **상대 전략 실제 매수(`BUY_UNIT`) 체결 시 미완성 더미 0/2 즉시 초기화 메커니즘 구축**:
    - 과거 단발성 노이즈로 누적된 유령 더미(Ghost Dummy)가 추세 반전 시 방어벽을 조기 소진하는 결함 원천 차단
    - 어느 한쪽 전략이 실제 매수를 체결하면, 반대편 전략의 미완성 더미(`executed_units == 0 and dummy_count > 0`)를 즉시 **`0 / 2`로 리셋**하여 시장 국면 전환 반영
    - 익절 청산(`SELL_CLEAR`) 시에는 반대편 더미를 유지하며, 상대 매수 체결 이후 발생한 신호는 정상 더미로 누적
    - `manager/cycle_manager.py`: `reset_incomplete_dummy()` 메서드 구현 및 상태 파일(`state.json`) 원자적 저장
    - `manager/trader.py`: 실전 매수 체결 직후 상대 전략 미완성 더미 초기화 호출 및 텔레그램 로그/엑셀 이력 기록 연동
    - `utils/excel_logger.py`: `RESET_DUMMY` 이벤트 스타일(연노랑 배경) 추가
  - **문서 체계 정비**:
    - `docs/` 디렉터리에 작성 일시(KST)가 포함된 타임스탬프 형식으로 계획서 이관:
      * `docs/20261007_1846_pnl_chart_implementation_plan.md`
      * `docs/20261010_1402_indicator_implementation_plan.md`
      * `docs/20261011_1154_dynamic_unit_implementation_plan.md`
    - 빈 `docs/implementation/` 디렉터리 정리 및 `/apply`, `/ask` 스킬 참조 경로 최신화
    - `docs/prd/bitget_v3_switch_prd.md` & `README.md`: 섹션 2 자금 관리 및 더미 메커니즘 수식/명세 갱신
- **검증 결과**:
  - `python -m py_compile config.py api/bitget_client.py manager/cycle_manager.py manager/trader.py utils/excel_logger.py main.py` 구문 컴파일 오류 0건 통과
  - `unittest` 기반 잔고 산출 및 미완성 더미 리셋(`executed_units == 0` 대상만 정확히 초기화) 단위 검증 완료
  - `python main.py --run-once` 일봉 마감 파이프라인 단일 실행 테스트: 실시간 잔고 조회, 단일/헤지 1.0 Unit 산출, 텔레그램 메시지 발송, 엑셀 갱신 전 과정 정상 동작 검증 완료

## [2026-10-11 12:28] 업데이트 이력 (Commit ID: 61986df)
- **수정 내용**: 
  - **텔레그램 일봉 마감 보고서 명칭 정비 ('1차' 삭제)**:
    - 텔레그램 일일 보고서 헤더 명칭을 `📊 [Bitget V3 Switch 일봉 1차 마감 보고]`에서 `📊 [Bitget V3 Switch 일봉 마감 보고]`로 변경하여 직관성 및 명확성 제고
    - `main.py` 및 `config.py`의 '일봉 1차 평가/실행' 관련 주석 및 로그 표기를 '일봉 마감 평가/실행'으로 일관되게 정돈
  - **전략별 매수/미매수 판정 사유 상세화 및 집행 내역 블록 집중 표기**:
    - `strategy/strategy_a_long.py` & `strategy/strategy_b_short.py`: `check_entry_signal()`에서 단순 미충족 문구 대신 실제 일봉 등락률과 기준 임계값을 비교한 구체적 수치 사유 반환 (예: `음봉 하락 미달(+0.59% > -1.5%) & 전일비 급락 미달(+0.59% > -3.0%)`, `양봉 상승 미달(+0.59% < +1.5%) & 전일비 급등 미달(+0.59% < +3.0%)`)
    - `manager/trader.py`: 일봉 마감 싸이클 내 전략 A(롱) 및 전략 B(숏)의 판정 상태(조건 미충족, 트레일링 감시 스킵, 더미 소진/이관, 실전 매수 체결, 최소주문금액 미달) 및 상세 사유를 `last_eval_a`, `last_eval_b`에 보관
    - `main.py`: 전략 A/B 블록의 중복 문구를 제거하여 포지션/수익률 현황에만 집중하고, `[금일 집행 내역]` 블록에 전략별 당일 매수/미매수 사유를 단일 집중 표기
  - **텔레그램 HTML 엔티티 이스케이프 및 안전 전송 폴백 구축**:
    - `main.py`: 부등호(`<`, `>`) 및 `&` 기호가 텔레그램 HTML 파서와 충돌하지 않도록 `escape_tg()`를 적용해 안전하게 렌더링
    - `utils/notifier.py`: Telegram API에서 400 Bad Request(HTML 파싱 오류) 발생 시 태그를 자동 제거하고 일반 텍스트 모드로 즉시 재전송하는 Fallback 안전장치 추가
- **검증 결과**:
  - `python -m py_compile config.py strategy/strategy_a_long.py strategy/strategy_b_short.py manager/trader.py main.py utils/notifier.py` 구문 컴파일 오류 0건 통과
  - `python main.py --run-once` 실제 파이프라인 단일 실행을 통한 텔레그램 메시지 생성 및 전송 검증 완료 (`[Notifier] 텔레그램 메시지 전송 성공`)
  - 전략별 매수 미달 사유 및 수치 비교값의 정상 출력 확인 완료

## [2026-10-12 21:05] 업데이트 이력 (Commit ID: ed5e504)
- **수정 내용**: 
  - **헤지모드 실주문 파라미터 적용 및 계정 검증 (`api/bitget_client.py`, `config.py`)**:
    - `hedged=True` 및 청산 시 `reduceOnly=True` 설정으로 Bitget Hedge Mode 시장가 주문(롱 진입 buy/롱 청산 sell, 숏 진입 sell/숏 청산 buy) 체결 보장
    - `_verify_account_mode()` 구현: `posMode='hedge_mode'`, `marginMode='cross'`, 레버리지 1x, 계약단위(`contractSize=1`)를 엄격 검증하여 거래소 환경 불일치 시 사전 차단
  - **실전 모드 가짜 데이터/모의 잔고 매매 원천 차단 (`api/exceptions.py`, `utils/mock_data.py`, `config.py`)**:
    - 실전 모드(`PAPER_TRADING=false`)에서 필수 API 설정 누락 검증(`validate_live_config()`) 실패 시 프로세스 즉시 종료 (Exit Code 2)
    - 시세/잔고/포지션 조회 실패 시 모의 데이터나 가상 잔고로 임의 대체하는 폴백을 전면 제거하고 `DataUnavailableError` 발생으로 안전 중단
  - **주문 → 체결 확인 → 상태/손익/엑셀 기록 순서 일원화 (`api/bitget_client.py`, `manager/trader.py`)**:
    - 시장가 주문 발송 후 `_confirm_order()`로 실제 체결 여부 및 실체결가·수량·수수료를 확인한 `OrderResult` 반환
    - 체결 확인된 수량과 금액에 대해서만 `state.json` 및 `trade_history.xlsx`에 반영하여 부분체결 및 미체결 시의 상태 왜곡 방지
  - **거래소 실제 포지션 대조(Reconciliation) 및 매매 차단 시스템 구축 (`manager/reconciler.py`, `main.py`)**:
    - 일봉 마감 및 2단계 트레일링 감시 시점에 거래소 실제 포지션과 `state.json`의 수량/평단을 자동 대조
    - 불일치(`MISMATCH`) 발생 시 해당 전략만 매매를 차단하고 텔레그램 경보 발송
    - `--reconcile` (대조 결과 출력) 및 `--adopt-exchange` (거래소 포지션 강제 채택 및 복구) CLI 명령어 지원
  - **롱 "완만한 하락장 본전탈출" 조건 개선 (`strategy/strategy_a_long.py`)**:
    - 기존 `<=`(하락 시 추가 청산)에서 `>=`(반등 시 본전 탈출)로 조건식 수정: `current_price >= target_price` (`SMA5 * 0.99` 이상으로 회복 시 즉시 전량 청산)
  - **동적 자금 분할 1/12 적용 및 최대 10회차 상한/가용잔고 상한 (`config.py`, `utils/formatters.py`, `manager/cycle_manager.py`, `manager/trader.py`)**:
    - `UNIT_DIVISOR = 12.0` 적용: 1~4회차(1.0x×4) + 5~10회차(1.25x×6) = 11.5 Unit 소요에 맞춰 0.5 Unit의 안전 버퍼 확보
    - `MAX_EXECUTED_UNITS = 10` 상한 설정으로 최대 10회차 도달 시 추가 매수 중단(`SKIPPED_MAX_UNITS`)
    - `MARGIN_SAFETY_RATIO = 0.95` 적용: 1회 주문금액이 `가용 잔고 × 레버리지 × 0.95`를 초과하지 않도록 자동 제한
    - 단일 트랙 진행바 인디케이터가 10칸(`MAX_EXECUTED_UNITS`)으로 안정적으로 렌더링되도록 수정
  - **2단계 트레일링 보존선 래칫(Ratchet) 메커니즘 구축 (`manager/cycle_manager.py`, `manager/trader.py`, `utils/excel_logger.py`)**:
    - 트레일링 감시 중 일봉 재평가 시 유리한 방향으로만 보존선 갱신(롱: 상향, 숏: 하향). 불리한 가격이나 `IMMEDIATE_EXIT` 결과는 무시하고 포지션 유지
    - 엑셀 기록 시 `TRAIL_RATCHET` 이벤트(연노랑 배경) 연동
  - **4시간봉 2단계 판정 기준 개선 (`strategy/indicator.py`, `strategy/strategy_a_long.py`, `strategy/strategy_b_short.py`)**:
    - `STAGE2_SIGNAL_MODE = "closed_candle"` 기본 적용: 5분마다 실시간가로 최소 보존선 이탈을 즉각 방어하면서, 4H 5MA 추세 이탈은 휩쏘(노이즈) 방지를 위해 마감된 4H 완성봉 종가 기준으로 판정
    - 트레일링 돌입 이후 마감된 4H 봉만 이탈 판정에 인정하도록 타임스탬프 필터링 적용
  - **상태 파일 원자적 저장 및 백업/보호 (`manager/cycle_manager.py`)**:
    - `state.json` 저장 시 임시 파일(`state.tmp`) 작성 후 원자적 교체(`replace`)
    - 저장 전 직전 정상본을 `state.bak.json`에 보관, 손상 발생 시 `state.corrupt_YYYYMMDD_HHMMSS.json`으로 백업하고 덮어쓰기 방지
  - **문서 동기화 및 계획서 보관 (`docs/prd/bitget_v3_switch_prd.md`, `README.md`, `docs/`)**:
    - PRD 및 README에 변경된 1/12 분할 수식, 완만한 하락 본탈 조건, 2단계 트레일링/래칫 규칙 최신화
    - LLM 실행용 Phase 1 및 Phase 2 계획서 보관
- **검증 결과**:
  - `python -m py_compile` 전 파일 컴파일 에러 0건 통과
  - 1/12 분할 계산 및 가용잔고 상한(`MARGIN_SAFETY_RATIO`) 주문 축소 검증 완료
  - 최대 10회차 도달 시 `SKIPPED_MAX_UNITS` 매수 중단 검증 완료
  - 트레일링 돌입 후 보존선 상향 래칫 및 일봉 즉시탈출 신호 무시 검증 완료
  - 4H 진행 중 캔들 노이즈 방어 및 마감 완성봉 이탈 시 정상 청산 검증 완료




