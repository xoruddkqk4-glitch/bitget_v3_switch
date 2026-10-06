# week_00-bitget_v3_switch

비트겟 자동매매 4기 - Week 00 Bitget V3 Switch 프로젝트

---

## 📌 프로젝트 소개
- 비트겟 거래소 V3 API 및 자동매매 전략 스위칭 시스템 (SOXL/USDT 헤지 모드 무손절 DCA 시스템)

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
