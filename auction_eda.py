"""브로커 토폴로지 이벤트 드리븐 온라인 경매 시스템 (표준 라이브러리 asyncio만 사용)."""
import asyncio
import json
import os
import random
import sys
import time
from dataclasses import dataclass, asdict, field

MAX_RETRY = 3          # 처리 실패 시 최대 재시도 횟수
SEED = 20261025        # 난수 고정
T0 = time.perf_counter()
LOG: list[str] = []


def log(who: str, msg: str) -> None:
    """시각(경과 ms)·컴포넌트·내용을 한 줄로 출력하고 보관한다."""
    line = f"[{(time.perf_counter() - T0) * 1000:8.1f}ms] {who:<18}| {msg}"
    LOG.append(line)
    print(line)


def now_ms() -> float:
    return (time.perf_counter() - T0) * 1000


# ───────────────────────── 커넥터: 이벤트 봉투 + JSON 어댑터 ─────────────────────────
@dataclass
class Envelope:
    """이벤트 봉투. 과거형 type, 토픽, 발생 시각(ms), 유효기간(ttl, ms), 내용(payload)."""
    event_id: str
    type: str
    topic: str
    timestamp: float
    ttl: float
    payload: dict = field(default_factory=dict)

    def dumps(self) -> str:      # 어댑터: 객체 -> JSON 문자열
        return json.dumps(asdict(self), ensure_ascii=False)

    @staticmethod
    def loads(raw: str) -> "Envelope":   # 어댑터: JSON 문자열 -> 객체
        return Envelope(**json.loads(raw))

    def expired(self) -> bool:
        return now_ms() - self.timestamp >= self.ttl


# ───────────────────────── 이벤트 브로커 ─────────────────────────
class Subscription:
    """구독 하나 = 이름 + 자기 큐 + 통계. 같은 이름으로 붙은 워커들이 큐를 나눠 소비한다."""
    def __init__(self, name: str, topic: str):
        self.name, self.topic = name, topic
        self.queue: asyncio.Queue = asyncio.Queue()
        self.processed = self.retried = self.dlq = self.expired = 0


class EventBroker:
    """이벤트를 전달만 한다. 비즈니스 로직 없음. 토픽별 구독 큐에 JSON 문자열을 넣는다."""
    TOPICS = ["bid.placed", "bid.highest_changed", "auction.closed",
              "auction.settled", "notification.sent", "deadletter"]

    def __init__(self):
        self.subs: dict[str, list[Subscription]] = {t: [] for t in self.TOPICS}
        self.published = {t: 0 for t in self.TOPICS}
        self.seq = 0
        self.types_seen: set[str] = set()

    def subscribe(self, topic: str, name: str) -> Subscription:
        sub = Subscription(name, topic)
        self.subs[topic].append(sub)
        return sub

    def publish(self, topic: str, type_: str, payload: dict, ttl: float = 5000) -> None:
        """기다리지 않고 즉시 반환(put_nowait). 누가 구독하는지는 모른다."""
        self.seq += 1
        env = Envelope(f"evt-{self.seq:04d}", type_, topic, now_ms(), ttl, payload)
        raw = env.dumps()
        self.published[topic] += 1
        self.types_seen.add(type_)
        for sub in self.subs[topic]:
            sub.queue.put_nowait(raw)

    async def drain(self) -> None:
        """(시연 보조) 모든 큐가 비고 더 이상 새 발행이 없을 때까지 대기."""
        while True:
            before = self.seq
            await asyncio.gather(*(s.queue.join() for ss in self.subs.values() for s in ss))
            await asyncio.sleep(0)
            if self.seq == before:
                return

    def close(self, workers_per_sub: dict[str, int]) -> None:
        """워커 종료 신호(None)를 구독별 워커 수만큼 넣는다."""
        for ss in self.subs.values():
            for s in ss:
                for _ in range(workers_per_sub.get(s.name, 1)):
                    s.queue.put_nowait(None)


# ───────────────────────── 컴포넌트 공통 ─────────────────────────
class Component:
    """모든 컴포넌트의 기반. 브로커만 알고 다른 컴포넌트는 모른다."""
    def __init__(self, name: str, broker: EventBroker):
        self.name = name
        self.broker = broker
        self.subs: list[Subscription] = []
        self.tasks: list[asyncio.Task] = []
        self.workers: dict[str, int] = {}

    def listen(self, topic: str, handler, workers: int = 1, sub_name: str | None = None) -> None:
        """토픽을 구독하고 워커를 띄운다. 워커가 여러 개면 한 큐를 나눠 소비(경쟁 소비자)."""
        sub = self.broker.subscribe(topic, sub_name or self.name)
        self.subs.append(sub)
        self.workers[sub.name] = workers
        for i in range(1, workers + 1):
            wid = f"{self.name}#{i}" if workers > 1 else self.name
            self.tasks.append(asyncio.create_task(self._worker(sub, handler, wid)))

    async def _worker(self, sub: Subscription, handler, wid: str) -> None:
        """큐에서 꺼내 역직렬화 -> TTL 검사 -> 처리 -> ack. 실패 시 재시도, 초과 시 deadletter."""
        while True:
            raw = await sub.queue.get()
            if raw is None:
                sub.queue.task_done()
                return
            env = Envelope.loads(raw)
            if env.expired():                       # 만료 이벤트는 처리 없이 버리고 로그
                sub.expired += 1
                log(wid, f"만료로 폐기 {env.event_id} {env.type} (ttl={env.ttl:g}ms)")
                sub.queue.task_done()
                continue
            for attempt in range(MAX_RETRY + 1):
                try:
                    await handler(env, wid)
                    sub.processed += 1              # ack
                    break
                except Exception as e:
                    if attempt < MAX_RETRY:
                        sub.retried += 1
                        log(wid, f"실패({e}) -> 재시도 {attempt + 1}/{MAX_RETRY} {env.event_id}")
                        await asyncio.sleep(0.006)
                    else:
                        sub.dlq += 1
                        log(wid, f"재시도 초과 -> deadletter 전송 {env.event_id}")
                        self.broker.publish("deadletter", "EventDeadLettered",
                                            {"subscription": sub.name, "error": str(e), "event": raw})
            sub.queue.task_done()


# ───────────────────────── 이벤트 발행자(시작 이벤트) ─────────────────────────
class BidProducer(Component):
    """입찰자의 입찰을 BidPlaced 이벤트로 발행만 한다. 응답을 기다리지 않는다."""
    BIDDERS = ["민수", "지영", "철수", "영희", "하준"]

    def __init__(self, broker):
        super().__init__("BidProducer", broker)
        self.rng = random.Random(SEED)

    def produce(self, auction_id: str, count: int, short_ttl_idx=()) -> float:
        """입찰 count건을 모두 발행하고 걸린 시간(ms)을 돌려준다."""
        amounts = [self.rng.randrange(100, 400) for _ in range(count)]
        for i in (6, 15):                             # 13의 배수 입찰을 반드시 포함(분석 실패 시연)
            amounts[i] = (amounts[i] // 13 + 1) * 13
        t = time.perf_counter()
        for i, amt in enumerate(amounts):
            bidder = self.rng.choice(self.BIDDERS)
            # 일부러 ttl=0 을 줘서 만료 폐기를 시연한다(발행 즉시 만료로 간주).
            ttl = 0 if i in short_ttl_idx else 5000
            self.broker.publish("bid.placed", "BidPlaced",
                                {"auction_id": auction_id, "bidder": bidder, "amount": amt}, ttl)
        elapsed = (time.perf_counter() - t) * 1000
        log(self.name, f"입찰 {count}건 발행 완료 ({elapsed:.3f}ms, 처리 결과는 기다리지 않음)")
        return elapsed


class AuctionClock(Component):
    """마감 시각이 되면 AuctionClosed 를 발행한다."""
    def close(self, auction_id: str) -> None:
        log(self.name, f"{auction_id} 경매 마감 -> AuctionClosed 발행")
        self.broker.publish("auction.closed", "AuctionClosed", {"auction_id": auction_id})


# ───────────────────────── 이벤트 구독자(프로세서) ─────────────────────────
class BidCapture(Component):
    """모든 입찰을 기록한다."""
    def __init__(self, broker):
        super().__init__("BidCapture", broker)
        self.records: list[dict] = []
        self.listen("bid.placed", self.handle)

    async def handle(self, env, wid):
        self.records.append(env.payload)
        log(wid, f"입찰 기록 {env.payload['bidder']} {env.payload['amount']}원")


class BidTracker(Component):
    """최고가를 추적하고 바뀌면 HighestBidChanged 를 발행한다."""
    def __init__(self, broker):
        super().__init__("BidTracker", broker)
        self.highest: dict[str, dict] = {}
        self.listen("bid.placed", self.handle)

    async def handle(self, env, wid):
        p = env.payload
        cur = self.highest.get(p["auction_id"])
        if cur is None or p["amount"] > cur["amount"]:
            self.highest[p["auction_id"]] = p
            log(wid, f"최고가 갱신 {p['bidder']} {p['amount']}원 -> HighestBidChanged 발행")
            self.broker.publish("bid.highest_changed", "HighestBidChanged", dict(p))


class BidAnalytics(Component):
    """느린 통계(입찰 수·평균가). 13의 배수 금액에서 일부러 실패한다. 워커 2개로 확장."""
    def __init__(self, broker):
        super().__init__("BidAnalytics", broker)
        self.count = 0
        self.total = 0
        self.first_start = self.last_end = None
        self.worker_use: dict[str, int] = {}
        self.listen("bid.placed", self.handle, workers=2)

    async def handle(self, env, wid):
        if self.first_start is None:
            self.first_start = time.perf_counter()
        await asyncio.sleep(0.02)                     # 느린 처리 흉내
        amt = env.payload["amount"]
        if amt % 13 == 0:                             # 내고장성 시연: 의도적 실패
            raise ValueError(f"{amt}원은 13의 배수")
        self.count += 1
        self.total += amt
        self.worker_use[wid] = self.worker_use.get(wid, 0) + 1
        self.last_end = time.perf_counter()
        log(wid, f"통계 갱신 입찰수={self.count} 평균={self.total / self.count:.1f}원")


class Notifier(Component):
    """최고가 변경·낙찰 이벤트를 받아 알림을 보내고 NotificationSent 를 발행한다."""
    def __init__(self, broker):
        super().__init__("Notifier", broker)
        self.sent = 0
        self.listen("bid.highest_changed", self.on_highest, sub_name="Notifier/highest")
        self.listen("auction.settled", self.on_settled, sub_name="Notifier/settled")

    def _send(self, wid, text):
        self.sent += 1
        log(wid, f"알림 발송: {text}")
        self.broker.publish("notification.sent", "NotificationSent", {"text": text})

    async def on_highest(self, env, wid):
        self._send(wid, f"새 최고가 {env.payload['amount']}원 ({env.payload['bidder']})")

    async def on_settled(self, env, wid):
        p = env.payload
        self._send(wid, f"낙찰 결과: {p['winner']} 님 {p['price']}원")


class Settlement(Component):
    """마감 시 낙찰자를 확정한다. 최고가는 구독한 이벤트로 스스로 유지한다(BidTracker 호출 없음)."""
    def __init__(self, broker):
        super().__init__("Settlement", broker)
        self.my_highest: dict[str, dict] = {}
        self.result = None
        self.listen("bid.highest_changed", self.on_highest, sub_name="Settlement/highest")
        self.listen("auction.closed", self.on_closed, sub_name="Settlement/closed")

    async def on_highest(self, env, wid):
        self.my_highest[env.payload["auction_id"]] = env.payload

    async def on_closed(self, env, wid):
        aid = env.payload["auction_id"]
        top = self.my_highest.get(aid)
        if top is None:
            log(wid, f"{aid} 입찰 없음 - 유찰")
            return
        self.result = {"auction_id": aid, "winner": top["bidder"], "price": top["amount"]}
        log(wid, f"낙찰 확정 {top['bidder']} {top['amount']}원 -> AuctionSettled 발행")
        self.broker.publish("auction.settled", "AuctionSettled", dict(self.result))


class DeadLetterMonitor(Component):
    """deadletter 토픽의 이벤트를 기록한다."""
    def __init__(self, broker):
        super().__init__("DeadLetterMonitor", broker)
        self.items: list[dict] = []
        self.listen("deadletter", self.handle)

    async def handle(self, env, wid):
        self.items.append(env.payload)
        log(wid, f"DLQ 수신: {env.payload['subscription']} / {env.payload['error']}")


# ───────────────────────── 제약 검사 ─────────────────────────
def find_component_refs(obj, depth=0) -> list:
    """객체 안에서 Component 참조(직접·컨테이너·바운드 메서드)를 찾는다."""
    if depth > 4:
        return []
    if isinstance(obj, Component):
        return [obj.name]
    if hasattr(obj, "__self__") and isinstance(getattr(obj, "__self__"), Component):
        return [obj.__self__.name]
    items = []
    if isinstance(obj, dict):
        items = list(obj.keys()) + list(obj.values())
    elif isinstance(obj, (list, tuple, set)):
        items = list(obj)
    out = []
    for it in items:
        out += find_component_refs(it, depth + 1)
    return out


def check_c1(components) -> bool:
    """C1: 컴포넌트 속성에 다른 컴포넌트 참조가 없고, 브로커만 참조함을 확인."""
    ok = True
    for c in components:
        bad, n = [], 0
        for k, v in vars(c).items():
            n += 1
            refs = [r for r in find_component_refs(v) if r != c.name]
            if refs:
                bad.append((k, refs))
        has_broker = isinstance(vars(c).get("broker"), EventBroker)
        status = "OK" if not bad and has_broker else f"위반 {bad}"
        ok &= status == "OK"
        log("제약검사C1", f"{c.name:<17} 속성 {n}개 검사, 타 컴포넌트 참조 {sum(len(r) for _, r in bad)}, 브로커 참조 {'있음' if has_broker else '없음'} -> {status}")
    # 자가검증: 검사기가 위반을 실제로 잡아내는지 확인(가짜 위반 객체 사용)
    a, b = Component("A", None), Component("B", None)
    a.peer = b
    caught = any(r != "A" for v in vars(a).values() for r in find_component_refs(v))
    log("제약검사C1", f"검사기 자가검증(A가 B를 참조하게 만든 가짜 객체) -> 위반 탐지 {'성공' if caught else '실패'}")
    ok &= caught
    log("제약검사C1", "결과: " + ("PASS" if ok else "FAIL"))
    return ok


def check_c3_c4(broker, producers) -> tuple[bool, bool]:
    """C3: 모든 이벤트 type 이 과거형(-ed). C4: 발행자는 구독/구독자 정보를 갖지 않음."""
    c3 = all(t.endswith(("ed", "Sent")) for t in broker.types_seen)  # Sent 도 과거형
    c4 = all(not any(isinstance(v, Subscription) for v in vars(p).values()) and not hasattr(p, "subs_of")
             for p in producers)
    log("제약검사C3", f"이벤트 type {sorted(broker.types_seen)} -> {'PASS' if c3 else 'FAIL'}")
    log("제약검사C4", f"발행자는 토픽 이름만 사용, 구독자 목록 미보유 -> {'PASS' if c4 else 'FAIL'}")
    return c3, c4


# ───────────────────────── 실행 ─────────────────────────
async def main() -> int:
    broker = EventBroker()
    producer, clock = BidProducer(broker), AuctionClock("AuctionClock", broker)
    capture, tracker = BidCapture(broker), BidTracker(broker)
    analytics, notifier = BidAnalytics(broker), Notifier(broker)
    settle, dlq_mon = Settlement(broker), DeadLetterMonitor(broker)
    subscribers = [capture, tracker, analytics, notifier, settle, dlq_mon]
    allc = [producer, clock] + subscribers
    log("시스템", "경매 시스템 시작")
    c1 = check_c1(allc)

    pub_ms = producer.produce("A-100", 26, short_ttl_idx=(3, 11, 20))
    await broker.drain()                         # 입찰 처리가 모두 끝나면 마감(시연 순서 보장)
    clock.close("A-100")
    await broker.drain()
    c2_total = (analytics.last_end - analytics.first_start) * 1000
    log("제약검사C2", f"입찰 전체 발행 {pub_ms:.3f}ms vs BidAnalytics 처리 {c2_total:.1f}ms "
                      f"(약 {c2_total / max(pub_ms, 1e-6):.0f}배) -> 발행자는 처리 완료를 기다리지 않음")
    c3, c4 = check_c3_c4(broker, [producer, clock])

    workers = {}
    for c in subscribers:
        workers.update(c.workers)
    broker.close(workers)
    await asyncio.gather(*(t for c in subscribers for t in c.tasks))

    # 요약 표
    out = ["", "=" * 70, "요약", "=" * 70, "[토픽별 발행 수]"]
    out += [f"  {t:<22}{n:>4}" for t, n in broker.published.items()]
    out += ["", "[구독별 처리/재시도/DLQ/만료]", f"  {'구독':<24}{'처리':>5}{'재시도':>6}{'DLQ':>5}{'만료':>5}"]
    for ss in broker.subs.values():
        out += [f"  {s.name:<24}{s.processed:>5}{s.retried:>7}{s.dlq:>5}{s.expired:>5}" for s in ss]
    out += [f"[BidAnalytics 워커별 성공 처리] {dict(sorted(analytics.worker_use.items()))}"]
    r = settle.result
    out += [f"[낙찰 결과] {r['auction_id']} 낙찰자={r['winner']} 낙찰가={r['price']}원 (알림 {notifier.sent}건 발송)" if r else "[낙찰 결과] 유찰"]
    out += [f"[BidTracker 최고가] {tracker.highest['A-100']['amount']}원 / Settlement 보유 {r['price'] if r else '-'}원 -> 일치 {r and r['price'] == tracker.highest['A-100']['amount']}"]
    out += ["", "[제약 검사]",
            f"  C1 직접 참조 없음   : {'PASS' if c1 else 'FAIL'}",
            f"  C2 비동기 발행      : 발행 {pub_ms:.3f}ms < 분석 {c2_total:.1f}ms -> {'PASS' if pub_ms < c2_total else 'FAIL'}",
            f"  C3 과거형 이벤트    : {'PASS' if c3 else 'FAIL'}",
            f"  C4 발행자 익명성    : {'PASS' if c4 else 'FAIL'}"]
    for line in out:
        LOG.append(line)
        print(line)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    code = asyncio.run(main())
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_log.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(LOG) + "\n")
    sys.exit(code)
