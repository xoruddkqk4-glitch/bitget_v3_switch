# 양방향 헤지 동적 자금 관리 및 상대 전략 매수 시 미완성 더미 리셋 구현 계획서

- **작성 일시:** 2026-10-11 11:54 KST (12:05 KST 추가 반영)
- **문서 위치:** `docs/20261011_1154_dynamic_unit_implementation_plan.md`

본 문서는 사용자의 요청에 따라:
1. **양방향 포지션 동시 보유 시 가용 잔고(Free Margin) 기준 1/10 분할 매수를 적용하고, 단일 포지션 보유 시 총 평가 잔고(Total Balance) 기준 1/10 분할 매수를 적용하는 스마트 동적 자금 관리**
2. **어느 한쪽 전략의 실제 매수(`BUY_UNIT`) 체결 시, 반대편 전략의 미완성 더미(`executed_units == 0 and dummy_count > 0`)를 즉시 0/2로 초기화하는 유령 더미 제거 메커니즘**

위 2가지 핵심 전략을 시스템에 통합 구현하기 위한 상세 설계 및 실행 계획서입니다.

> [!IMPORTANT]
> **모드 정책 준수 안내 (`/ask` 전용)**
> 본 문서는 `/ask` 규칙에 따라 작성된 계획서이며, **프로젝트 소스 코드를 아직 수정하지 않았습니다**.  
> 계획서 검토 후 사용자가 **`/apply`** 명령어를 입력하시면 본 계획서에 정의된 단계에 따라 실제 소스 코드 반영 및 터미널 정적 검증(`python -m py_compile`)이 즉시 집행됩니다.

---

## 1. 구현 배경 및 목표

1. **마진 부족(`Insufficient Margin`) 에러 원천 방지**:
   - 롱 포지션이 5~6차수 이상 깊게 물려 시드의 50~60% 이상이 묶여 있을 때, 숏 실전 진입이 총 평가 잔고 기준으로 주문을 발송하면 가용 잔고 부족으로 거래소 주문이 실패하는 현상을 완벽히 차단합니다.
2. **양방향 헤지 시 가용 잔고 연속 10분할 보장**:
   - 롱이 이미 잡혀 있는 상태에서 숏이 진입할 때는 **남아 있는 가용 잔고(Free Margin)의 1/10**만 진입하므로, 첫 회차에 잔고를 탕진하지 않고 끝까지 안정적인 다차수 분할 매수(DCA)를 지속합니다.
3. **한쪽 청산 시 자동 마틴게일 평단가 희석 극대화**:
   - 롱이 전량 익절 청산되어 현금이 전액 환원되면, 단일 포지션으로 전환되어 숏의 다음 유닛이 총 잔고 기준(1/10)으로 커지며 숏 평단가를 단번에 현재가 수준으로 끌어올리는 유리한 마틴게일 효과를 누립니다.
4. **최소 주문 한도(Min Notional) 안전 가드**:
   - 가용 잔고가 극도로 적어져 주문 금액이 거래소 최소 주문 금액(Bitget 기준 5 USDT) 미만일 경우 에러를 내지 않고 안전하게 스킵 처리합니다.
5. **[신규] 유령 더미(Ghost Dummy) 제거 및 국면 전환 반영**:
   - 과거의 단발성 하락/상승 노이즈로 인해 쌓여 있던 미완성 더미 카운트가 추세 반전 시 살아남아 방어벽을 무력화하는 현상을 방지합니다.
   - 상대편 전략이 실제 매수(`BUY_UNIT`)를 시작했다는 것은 시장이 새로운 추세 국면에 진입했다는 확실한 신호이므로, 반대편의 미완성 더미를 0/2로 깨끗이 리프레시하여 다음 진입 시 온전한 2회 더미 검증을 거치도록 보장합니다.

---

## 2. 세부 알고리즘 명세

### (1) 동적 자금 계산 수식

진입하려는 전략(A 또는 B)의 실전 매수 주문 직전, **상대편(Opposite) 전략의 포지션 보유 여부**를 판별합니다:

$$\text{Unit Base USD} = 
\begin{cases} 
\cfrac{\text{Free Margin (실시간 가용 잔고)}}{10.0}, & \text{if 상대편 포지션 보유 중 } (\text{opposite\_qty} > 0) \\[10pt]
\cfrac{\text{Total Balance (계좌 총 평가 잔고)}}{10.0}, & \text{if 상대편 포지션 없음 } (\text{opposite\_qty} = 0)
\end{cases}$$

$$\text{Target USD} = \text{Unit Base USD} \times \text{Weight}$$
* **가중치(Weight):** 실제 1~4회차 $1.0\times$, 5회차 이상 $1.25\times$
* **최소 주문 금액 가드 (`MIN_ORDER_USD = 5.0`):**
  $$\text{if Target USD} < 5.0\text{ USDT} \implies \text{주문 스킵(Skip) 및 알림 기록}$$

---

### (2) 미완성 더미 카운트 초기화(Reset) 메커니즘

1. **초기화 대상 조건 (미완성 더미):**
   * 해당 전략의 상태가 **`executed_units == 0 and dummy_count > 0`** (실제 매수가 아직 1건도 체결되지 않은 상태에서 더미가 1/2 또는 2/2로 대기 중인 경우)
2. **트리거 시점:**
   * 상대편 전략의 **실제 매수(`BUY_UNIT`) 체결 완료 직후**
   * 예: 전략 A(롱) 실제 1회차 매수 체결 $\rightarrow$ 전략 B(숏)의 미완성 더미를 즉시 `0 / 2`로 초기화
3. **핵심 예외 규칙:**
   * **익절 청산(`SELL_CLEAR`) 발생 시:** 반대편 더미를 초기화하지 않고 그대로 유지
   * **매수 체결 '이후' 신호 발생 시:** 상대 전략이 매수를 체결한 당일 또는 그 이후에 새롭게 발생하는 신호는 정상적으로 더미 1차부터 누적 반영

---

## 3. 모듈별 변경 상세 계획

### 1) [config.py](file:///c:/Users/user/Desktop/trading_buddy/06비트겟자동매매-4기/week_00-bitget_v3_switch/config.py)
* 거래소 최소 주문 금액 상수 정의:
  ```python
  MIN_ORDER_USD = 5.0  # Bitget 선물 최소 주문 가능 금액 ($5.0)
  ```

### 2) [api/bitget_client.py](file:///c:/Users/user/Desktop/trading_buddy/06비트겟자동매매-4기/week_00-bitget_v3_switch/api/bitget_client.py)
* `fetch_account_balance()` 메서드 구현 (총 평가 잔고 및 실시간 가용 잔고 동시 반환):
  ```python
  def fetch_account_balance(self) -> Tuple[float, float]:
      """(총 평가 잔고, 실시간 가용 잔고)를 반환합니다."""
      if self.paper_trading or not BITGET_API_KEY:
          return MOCK_BALANCE, MOCK_BALANCE

      try:
          balance = self.exchange.fetch_balance({'type': 'swap'})
          usdt_info = balance.get('USDT', {})
          total_equity = float(usdt_info.get('total', 0.0))
          free_margin = float(usdt_info.get('free', 0.0))
          return total_equity, free_margin
      except Exception as e:
          logger.error(f"[BitgetClient] 잔고 조회 실패: {e}")
          return MOCK_BALANCE, MOCK_BALANCE
  ```

### 3) [manager/cycle_manager.py](file:///c:/Users/user/Desktop/trading_buddy/06비트겟자동매매-4기/week_00-bitget_v3_switch/manager/cycle_manager.py)
* 미완성 더미 초기화 전용 메서드 `reset_incomplete_dummy()` 추가:
  ```python
  def reset_incomplete_dummy(self, strategy_name: str, trigger_source: str, date_str: str) -> bool:
      """
      상대 전략의 실전 매수 체결 시, 아직 실전 매수에 진입하지 않은 상태(executed_units == 0)의
      미완성 더미(dummy_count > 0)를 0/2로 초기화합니다.
      """
      s = self.state.get(strategy_name, {})
      if s.get("executed_units", 0) == 0 and s.get("dummy_count", 0) > 0:
          old_dummy = s["dummy_count"]
          s["dummy_count"] = 0
          s["last_action"] = "RESET_DUMMY_ON_OPPOSITE_BUY"
          s["last_action_date"] = date_str
          self._save_state(self.state)
          logger.info(f"[CycleManager] [{strategy_name}] 상대 전략({trigger_source}) 실제 매수 체결로 미완성 더미({old_dummy}/2) -> 0/2 초기화 완료")
          return True
      return False
  ```

### 4) [manager/trader.py](file:///c:/Users/user/Desktop/trading_buddy/06비트겟자동매매-4기/week_00-bitget_v3_switch/manager/trader.py)
* `process_daily_cycle` 시작 시 총 잔고 및 가용 잔고 수집:
  ```python
  total_balance, free_margin = self.client.fetch_account_balance()
  self.last_total_balance = total_balance
  self.last_free_margin = free_margin
  ```
* **전략 A (롱) 실전 매수 진입부 (`line 232~`):**
  ```python
  opposite_qty = state_b.get("total_qty", 0.0)
  if opposite_qty > 0:
      unit_base_usd = free_margin / UNIT_DIVISOR
      basis_label = "가용 잔고(헤지)"
  else:
      unit_base_usd = total_balance / UNIT_DIVISOR
      basis_label = "총 평가 잔고(단일)"

  target_usd = unit_base_usd * weight

  if target_usd < MIN_ORDER_USD:
      msg = f"⚠️ [롱 주문 스킵] {unit_label}: 주문금액(${target_usd:,.2f})이 최소 한도(${MIN_ORDER_USD}) 미만입니다."
      logger.warning(f"[Trader] {msg}")
      execution_logs.append(msg)
  else:
      # 실전 주문 집행 및 체결 기록
      ...
      # 체결 직후: 상대 전략(B 숏)의 미완성 더미 초기화 트리거
      if self.cycles.reset_incomplete_dummy("strategy_B", trigger_source="strategy_A", date_str=date_str):
          bar_reset = format_progress_single_track(0, 0, include_counts=False)
          execution_logs.append(f"🔄 [숏 더미 초기화] 롱 실전 매수 체결로 숏 미완성 더미 리셋: {bar_reset} (더미 0/2)")
          self.excel.append_record(
              date_str=date_str,
              strategy_name="strategy_B",
              event_type="RESET_DUMMY",
              price=None,
              qty=None,
              unit_label="더미 초기화 (0/2)",
              dummy_status="0 / 2",
              total_balance=total_balance,
              note="롱 실전 매수 체결에 따른 숏 미완성 더미 0/2 초기화"
          )
  ```
* **전략 B (숏) 실전 진입부 (`line 309~`):**
  ```python
  opposite_qty = state_a.get("total_qty", 0.0)
  if opposite_qty > 0:
      unit_base_usd = free_margin / UNIT_DIVISOR
      basis_label = "가용 잔고(헤지)"
  else:
      unit_base_usd = total_balance / UNIT_DIVISOR
      basis_label = "총 평가 잔고(단일)"

  target_usd = unit_base_usd * weight

  if target_usd < MIN_ORDER_USD:
      msg = f"⚠️ [숏 주문 스킵] {unit_label}: 주문금액(${target_usd:,.2f})이 최소 한도(${MIN_ORDER_USD}) 미만입니다."
      logger.warning(f"[Trader] {msg}")
      execution_logs.append(msg)
  else:
      # 실전 주문 집행 및 체결 기록
      ...
      # 체결 직후: 상대 전략(A 롱)의 미완성 더미 초기화 트리거
      if self.cycles.reset_incomplete_dummy("strategy_A", trigger_source="strategy_B", date_str=date_str):
          bar_reset = format_progress_single_track(0, 0, include_counts=False)
          execution_logs.append(f"🔄 [롱 더미 초기화] 숏 실전 매수 체결로 롱 미완성 더미 리셋: {bar_reset} (더미 0/2)")
          self.excel.append_record(
              date_str=date_str,
              strategy_name="strategy_A",
              event_type="RESET_DUMMY",
              price=None,
              qty=None,
              unit_label="더미 초기화 (0/2)",
              dummy_status="0 / 2",
              total_balance=total_balance,
              note="숏 실전 매수 체결에 따른 롱 미완성 더미 0/2 초기화"
          )
  ```

### 5) [utils/excel_logger.py](file:///c:/Users/user/Desktop/trading_buddy/06비트겟자동매매-4기/week_00-bitget_v3_switch/utils/excel_logger.py)
* `RESET_DUMMY` 이벤트 스타일(연한 회색/오렌지색 배경 등) 추가 매핑

### 6) [main.py](file:///c:/Users/user/Desktop/trading_buddy/06비트겟자동매매-4기/week_00-bitget_v3_switch/main.py)
* 일봉 마감 텔레그램 보고서 상단에 총 평가 잔고와 함께 **실시간 가용 잔고(Free Margin)** 명시:
  ```text
  • 계좌 총 평가 잔고: $10,000.00 (가용 잔고: $4,250.00)
  • 1.0 Unit 기본 금액: $1,000.00 (단일) / $425.00 (양방향 헤지)
  ```

### 7) [README.md](file:///c:/Users/user/Desktop/trading_buddy/06비트겟자동매매-4기/week_00-bitget_v3_switch/README.md) 및 [docs/prd/bitget_v3_switch_prd.md](file:///c:/Users/user/Desktop/trading_buddy/06비트겟자동매매-4기/week_00-bitget_v3_switch/docs/prd/bitget_v3_switch_prd.md)
* 섹션 2 '자금 관리 및 더미 메커니즘'에 양방향 헤지 시 가용 잔고 동적 분할 산출 공식, 최소 주문금액 가드 규칙, 및 상대 매수 체결 시 미완성 더미 초기화 규칙 누적 갱신.

---

## 4. 검증 및 테스트 계획

1. **정적 문법 및 타입 컴파일 검증 (Rule 2)**:
   ```powershell
   python -m py_compile config.py api/bitget_client.py manager/cycle_manager.py manager/trader.py utils/excel_logger.py main.py
   ```
2. **모의 파이프라인 1회 실행 테스트 (`--run-once`)**:
   ```powershell
   python main.py --run-once
   ```
   - 총 잔고 및 가용 잔고 정상 출력 확인
   - 단일 포지션 상태에서 `Total Balance / 10` 기준 산출 검증
   - 미완성 더미 리셋 메서드 단위 테스트 검증
   - 텔레그램 메시지 포맷 정상 빌드 검증

---

## 5. 승인 및 적용 안내

본 계획서는 `/ask` 모드 원칙에 따라 작성되었으며, 소스 코드 수정은 일체 발생하지 않았습니다.  
본 계획서의 내용대로 소스 코드에 즉시 반영하려면 채팅창에 **`/apply`** 명령어를 입력해 주시기 바랍니다.
