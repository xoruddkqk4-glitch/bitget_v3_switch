# v3-switch 일봉 롱·숏 동시 진입 방지 (1안: 전일비 방향 필터) 구현 계획서

> **작성일시:** 2026-11-24 12:55 KST  
> **대상 폴더:** `week_00-bitget_v3_switch` (EC2 배포 경로 `/var/autobot/bitget_v3_switch`)  
> **실행 방식:** `/apply` 명령 수신 시 본 계획서 기준으로 소스 코드 반영 (자동 커밋/푸시 금지)

---

## 1. 배경 및 원인 분석

### 1-1. 실제 발생 사례 (2026-11-24 05:58 KST / 미국 거래일 `2026-11-23`)
미국 SOXL 현물 일봉(`DAILY_SIGNAL_SOURCE=ls`) 기준 장마감 직전 평가에서 아래와 같이 시가 갭상승 후 음봉 마감이 발생하여 롱·숏 진입 신호가 동시에 충족되었습니다.

* **기준 수치 (`2026-11-23` ET):**
  * 전일 종가(`prev_close`): `$30.88`
  * 당일 시가(`eval_open`): `$44.82` (**+45.14% 갭상승**)
  * 당일 종가(`eval_close`): `$43.37`
  * 캔들 등락률(`candle_change`): **`-3.24%`** (`<= -1.5%` 충족 → 전략 A 롱 실전 5차 체결 `2.74 Qty @ $43.47`, 평단 `$42.14 → $42.46` 상승)
  * 전일비 등락률(`daily_change`): **`+40.45%`** (`>= +3.0%` 충족 → 전략 B 숏 더미 1회차 `1/2` 진입)

### 1-2. 구조적 원인
1. **서로 다른 두 지표의 `OR` 조건 구조:**
   * 전략 A (`strategy/strategy_a_long.py`): `candle_change <= -1.5%` **OR** `daily_change <= -3.0%`
   * 전략 B (`strategy/strategy_b_short.py`): `candle_change >= +1.5%` **OR** `daily_change >= +3.0%`
2. **상호 배타 가드 부재:**
   * `manager/trader.py`의 진입 평가 루프는 `strategy_A`와 `strategy_B`를 독립적으로 평가·집행하므로 동일 봉에서 양쪽 신호가 모두 참이면 둘 다 진입 처리합니다.
3. **미국 현물 일봉 전환에 따른 갭(Gap) 영향:**
   * 기존 Bitget 24시간 선물 일봉에서는 `당일 시가 ≈ 전일 종가`였으나, 미국 현물(LS) 일봉에서는 프리/애프터마켓 변동이 시가 갭으로 반영되면서 `candle_change`와 `daily_change`의 부호가 반대가 되는 경우가 발생합니다.

---

## 2. 채택 방안 (1안: 전일비 `daily_change` 방향 필터 추가)

동일 일봉에서 롱과 숏이 동시에 진입하지 않도록 각 전략의 진입 조건에 **전일비(`daily_change`) 방향 게이트**를 추가합니다.

* **롱 진입 조건:**
  `daily_change < 0` **AND** (`candle_change <= -1.5%` **OR** `daily_change <= -3.0%`)
* **숏 진입 조건:**
  `daily_change > 0` **AND** (`candle_change >= +1.5%` **OR** `daily_change >= +3.0%`)

### 기대 효과
1. **동일 일봉 내 롱·숏 동시 진입 100% 차단:** `daily_change < 0`과 `daily_change > 0`은 완전 배타 집합이므로 양방향 동시 진입이 수학적으로 불가능합니다 (`daily_change == 0.0`일 때는 양쪽 모두 관망).
2. **역방향 추격 진입 방지:** 갭상승 후 음봉(전일비 플러스)에서의 롱 추격 물타기, 갭하락 후 양봉(전일비 마이너스)에서의 숏 추격 진입을 함께 차단합니다.
3. **기존 전략 감도 및 핵심 로직 보존:** 기존 `-1.5% / -3.0%`, `+1.5% / +3.0%` 임계치 및 청산·2단계 트레일링·유닛·더미 로직은 일체 변경하지 않습니다.

---

## 3. 수정 대상 파일 및 상세 변경 계획

### 3-1. `config.py`
* 환경변수로 즉시 토글/롤백할 수 있도록 방향 필터 플래그를 추가합니다.

```python
ENTRY_DAILY_DIRECTION_FILTER = os.getenv("ENTRY_DAILY_DIRECTION_FILTER", "true").lower() in ("true", "1", "yes")
```

### 3-2. `strategy/strategy_a_long.py` (`StrategyALong.check_entry_signal`, L32~L55)
* `config`에서 `ENTRY_DAILY_DIRECTION_FILTER`를 import합니다.
* docstring에 전일비 방향 필터(`daily_change < 0`) 조건을 명시합니다.
* `is_bearish = candle_change <= LONG_BEARISH_CANDLE_PCT`, `is_daily_drop = daily_change <= LONG_DAILY_DROP_PCT` 계산 후 아래 가드를 추가합니다.

```python
if ENTRY_DAILY_DIRECTION_FILTER and daily_change >= 0.0:
    if is_bearish:
        return (
            False,
            f"전일비 상승/보합으로 롱 진입 차단(전일비 {daily_change*100:+.2f}% >= 0.0%, 음봉 {candle_change*100:+.2f}% 무시)",
        )
    return (
        False,
        f"음봉 하락 미달({candle_change*100:+.2f}% > {LONG_BEARISH_CANDLE_PCT*100:.1f}%) & "
        f"전일비 급락 미달({daily_change*100:+.2f}% > {LONG_DAILY_DROP_PCT*100:.1f}%)",
    )
```

### 3-3. `strategy/strategy_b_short.py` (`StrategyBShort.check_entry_signal`, L32~L55)
* `config`에서 `ENTRY_DAILY_DIRECTION_FILTER`를 import합니다.
* docstring에 전일비 방향 필터(`daily_change > 0`) 조건을 명시합니다.
* `is_bullish = candle_change >= SHORT_BULLISH_CANDLE_PCT`, `is_daily_pump = daily_change >= SHORT_DAILY_PUMP_PCT` 계산 후 롱과 대칭되는 가드를 추가합니다.

```python
if ENTRY_DAILY_DIRECTION_FILTER and daily_change <= 0.0:
    if is_bullish:
        return (
            False,
            f"전일비 하락/보합으로 숏 진입 차단(전일비 {daily_change*100:+.2f}% <= 0.0%, 양봉 {candle_change*100:+.2f}% 무시)",
        )
    return (
        False,
        f"양봉 상승 미달({candle_change*100:+.2f}% < {SHORT_BULLISH_CANDLE_PCT*100:+.1f}%) & "
        f"전일비 급등 미달({daily_change*100:+.2f}% < {SHORT_DAILY_PUMP_PCT*100:+.1f}%)",
    )
```

### 3-4. `main.py` (L398, L407, L517 — 텔레그램 일봉 보고서 가독성 개선)
* 기존에 `candle_change`만 `일봉 변동률`로 표기해 갭 발생 시 전일비 상승/하락과 혼동될 수 있던 부분을 `daily_change`와 `candle_change` 병기로 개선합니다.

```python
candle_change = indicators.get('candle_change', 0.0) * 100.0
daily_change = indicators.get('daily_change', 0.0) * 100.0
...
change_icon = "🔻" if daily_change < 0 else "🔺"
...
f"• <b>일봉 변동률:</b> {change_icon} <b>전일비 {daily_change:+.2f}%</b> / 캔들 {candle_change:+.2f}% (종가 ${eval_close:,.2f}){candle_basis}\n"
```

### 3-5. 문서 동기화 (`/apply` 반영 시)
* `docs/prd/bitget_v3_switch_prd.md`: 섹션 3의 A 전략 및 B 전략 진입 조건에 전일비 방향 필터 조건 반영
* `README.md`: 섹션 3의 A/B 전략 진입 조건 설명 갱신 *(※ 누적 커밋 이력은 `/git-commit` 수신 시점에만 추가)*

---

## 4. 케이스별 기대 동작 검증 매트릭스

| # | 시장 상황 | `daily_change` | `candle_change` | 전략 A (롱) 결과 | 전략 B (숏) 결과 |
|---:|---|---:|---:|---|---|
| 1 | 대폭 갭상승 후 음봉 (오늘 아침 사례) | `+40.45%` | `-3.24%` | `False` (전일비 상승 차단) | `True` (전일비 급등 충족) |
| 2 | 소폭 갭상승 후 음봉 | `+1.20%` | `-2.00%` | `False` (전일비 상승 차단) | `False` (미달) |
| 3 | 대폭 갭하락 후 양봉 | `-4.50%` | `+2.10%` | `True` (전일비 급락 충족) | `False` (전일비 하락 차단) |
| 4 | 소폭 갭하락 후 양봉 | `-1.20%` | `+2.00%` | `False` (미달) | `False` (전일비 하락 차단) |
| 5 | 일반 하락 음봉 | `-2.10%` | `-1.80%` | `True` (음봉 하락 충족) | `False` (미달) |
| 6 | 일반 상승 양봉 | `+2.20%` | `+1.90%` | `False` (미달) | `True` (양봉 상승 충족) |
| 7 | 전일비 정확히 보합 음봉 | `0.00%` | `-2.00%` | `False` (전일비 보합 차단) | `False` (미달) |

---

## 5. 검증 계획 (Verification Plan)

코드 반영(`/apply`) 후 아래 터미널 정적 검증 및 단위 검증을 수행합니다.

1. **구문 컴파일 검증:**
   ```bash
   python -m py_compile config.py strategy/strategy_a_long.py strategy/strategy_b_short.py main.py
   ```
2. **단위 테스트 (Python 원라이너):**
   * 위 7개 시나리오에 대해 `StrategyALong.check_entry_signal()` 및 `StrategyBShort.check_entry_signal()`의 `(bool, reason)` 결과 일치 여부 확인
   * `daily_change`, `candle_change` 광범위 그리드 조합에서 항상 `not (long_met and short_met)` 불변 조건 성립 확인
   * `ENTRY_DAILY_DIRECTION_FILTER=false` 설정 시 기존 `OR` 동작으로 롤백되는지 확인

---

## 6. 운영 참고 및 롤백 방안

* **즉시 롤백:** `.env`에 `ENTRY_DAILY_DIRECTION_FILTER=false`를 설정하면 코드 수정 없이 기존(`OR`) 동작으로 즉시 복귀할 수 있습니다.
* **기존 포지션 처리:** 2026-11-24 아침 이미 체결된 실서버의 롱 5차 포지션과 숏 더미 `1/2` 상태는 소급 변경되지 않으며, 본 변경 배포 이후 도래하는 다음 일봉 마감 평가부터 새 필터가 적용됩니다.
* **실행 절차:** 사용자의 `/apply` 명령 시 로컬 소스 코드에 반영하고, EC2 실서버 반영 및 Git 커밋·푸시는 `/git-commit` 명령 수신 시 수행합니다.
