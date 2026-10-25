# JBNU 소프트웨어아키텍처설계 과제2 — 이벤트 드리븐 아키텍처 스타일 시스템 구현

202110914 김대진

온라인 경매 시스템을 **브로커 토폴로지 이벤트 드리븐 스타일**로 구현했습니다. Python 3 표준 라이브러리(asyncio)만 사용합니다.

- 제출 보고서: [`과제2-보고서.pdf`](과제2-보고서.pdf) — 사용한 프롬프트, 구성 컴포넌트와 동작 흐름, 실행 결과 캡처, 기타 설명

## 실행

```bash
python auction_eda.py
```

약 1초 뒤 끝납니다. 콘솔 출력은 `run_log.txt`에도 저장됩니다.

## 구성

| 구분 | 구현 |
|---|---|
| 이벤트 발행자 | `BidProducer`(입찰 → BidPlaced), `AuctionClock`(마감 → AuctionClosed) |
| 이벤트 브로커 | `EventBroker` — 토픽 6개, 구독별 큐, 전달만 함 |
| 이벤트 구독자 | `BidCapture`, `BidTracker`, `BidAnalytics`(워커 2개), `Notifier`, `Settlement`, `DeadLetterMonitor` |
| 커넥터 | 토픽(이벤트 채널), `publish`/`subscribe`, JSON 직렬화 어댑터(`Envelope`) |
| 제약조건 | C1 직접 참조 없음 · C2 비동기 발행 · C3 과거형 이벤트 · C4 발행자 익명성 — 실행 시 자동 검사 |

## 파일

| 파일 | 내용 |
|---|---|
| `auction_eda.py` | 시스템 전체 |
| `run_log.txt` | 실행 출력 |
| `prompt.md` | 사용한 프롬프트 |
| `build_report.py` | 보고서 HTML·캡처·PDF 생성 (`python build_report.py`, Windows Edge 필요) |
| `report.html`, `capture/` | 보고서 원본과 실행 화면 캡처 |
