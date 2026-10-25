"""과제2 보고서 생성: run_log.txt·prompt.md·auction_eda.py -> 캡처 PNG 3장 -> report.html -> 과제2-보고서.pdf
사용: python build_report.py   (먼저 python auction_eda.py 로 run_log.txt 를 만든다)"""
import html, os, re, subprocess, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
REPO = "https://github.com/Daejin8753/JBNU-SW_Architecture-HW2"
CAP = HERE / "capture"
CAP.mkdir(exist_ok=True)
esc = html.escape

log = (HERE / "run_log.txt").read_text(encoding="utf-8").rstrip("\n").split("\n")
prompt = (HERE / "prompt.md").read_text(encoding="utf-8")
code = (HERE / "auction_eda.py").read_text(encoding="utf-8")

p1 = "\n".join(l[2:] for l in prompt.split("## 프롬프트 2")[0].splitlines() if l.startswith("> "))
p2 = re.search(r"```text\n(.*?)```", prompt, re.S).group(1).rstrip()


# ── 1. 실행 화면 캡처 (콘솔 출력을 터미널 화면으로 렌더링 후 Edge 스크린샷) ──
def color(line: str) -> str:
    s = esc(line)
    for pat, cls in [(r"(PASS|OK|성공)", "ok"), (r"(실패\([^)]*\)|재시도 초과|deadletter 전송|DLQ 수신)", "bad"),
                     (r"(만료로 폐기)", "warn"), (r"(HighestBidChanged|AuctionClosed|AuctionSettled|BidPlaced)", "evt")]:
        s = re.sub(pat, rf'<span class="{cls}">\1</span>', s)
    s = re.sub(r"^(\[\s*[\d.]+ms\])", r'<span class="ts">\1</span>', s)
    return s


def capture(name: str, lines: list[str], first: bool) -> Path:
    body = ""
    if first:
        body += '<div><span class="pr">PS C:\\...\\과제2-이벤트드리븐&gt;</span> python auction_eda.py</div>'
    body += "".join(f"<div>{color(l) or '&nbsp;'}</div>" for l in lines)
    page = f"""<!doctype html><meta charset="utf-8"><style>
    html,body{{margin:0;background:#0c0c0c}}
    .win{{background:#0c0c0c;color:#cccccc;font:13px/1.45 'Cascadia Mono','D2Coding','Malgun Gothic',Consolas,monospace;padding:14px 16px;white-space:pre-wrap;overflow-wrap:anywhere}}
    .ts{{color:#6a9955}} .ok{{color:#4ec94e;font-weight:bold}} .bad{{color:#f14c4c;font-weight:bold}}
    .warn{{color:#e5c07b}} .evt{{color:#61afef}} .pr{{color:#e5e510}}
    .bar{{background:#2d2d2d;color:#ddd;font:12px 'Malgun Gothic',sans-serif;padding:6px 12px}}
    </style><div class="bar">python auction_eda.py — 실행 결과</div><div class="win">{body}</div>"""
    src = CAP / f"{name}.html"
    src.write_text(page, encoding="utf-8")
    out = CAP / f"{name}.png"
    wraps = sum(max(0, (len(l) + sum(1 for ch in l if ord(ch) > 0x1100)) // 125) for l in lines)  # 줄바꿈 여유
    h = 40 + 19 * (len(lines) + wraps + 2)
    for _ in range(2):  # 스크린샷은 한 번 실패하면 재시도
        if out.exists():
            out.unlink()
        subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        f"--window-size=1000,{h}", f"--screenshot={out}", src.as_uri()],
                       capture_output=True, timeout=60)
        if out.exists() and out.stat().st_size > 0:
            return out
    sys.exit(f"캡처 실패: {name}")


split1 = next(i for i, l in enumerate(log) if "BidAnalytics" in l and "통계 갱신" in l)
split2 = next(i for i, l in enumerate(log) if "경매 마감" in l) - 6
caps = [capture("cap1", log[:split1], True), capture("cap2", log[split1:split2], False),
        capture("cap3", log[split2:], False)]
rng = [(1, split1), (split1 + 1, split2), (split2 + 1, len(log))]


def grab(pat):
    return next(l for l in log if re.search(pat, l))


def ms(line):
    return re.match(r"\[\s*([\d.]+)ms\]", line).group(1)


t_pub = grab("입찰 26건 발행 완료")
t_noti = [l for l in log if "새 최고가" in l][-1]
t_dlq1 = grab("DLQ 수신")
t_close = grab("경매 마감")
t_settle = grab("낙찰 결과:")
c2 = grab("제약검사C2")
pub_ms = re.search(r"발행 ([\d.]+)ms", c2).group(1)
ana_ms = re.search(r"처리 ([\d.]+)ms", c2).group(1)

# ── 2. 구성도 (SVG) ──
TOPICS = [("bid.placed", "BidPlaced", "BidProducer"), ("bid.highest_changed", "HighestBidChanged", "BidTracker"),
          ("auction.closed", "AuctionClosed", "AuctionClock"), ("auction.settled", "AuctionSettled", "Settlement"),
          ("deadletter", "EventDeadLettered", "구독 워커"), ("notification.sent", "NotificationSent", "Notifier")]
SUBS = [("BidCapture", "모든 입찰 기록", ""), ("BidTracker", "최고가 추적", "발행 → bid.highest_changed"),
        ("BidAnalytics #1·#2", "느린 통계 · 워커 2개", "실패 4회 → deadletter"),
        ("Notifier", "알림 발송", "발행 → notification.sent"),
        ("Settlement", "낙찰 확정(최고가 자체 보관)", "발행 → auction.settled"),
        ("DeadLetterMonitor", "실패 이벤트 기록", "")]
EDGES = [(0, 0), (0, 1), (0, 2), (1, 3), (1, 4), (2, 4), (3, 3), (4, 5)]
ty = lambda i: 92 + 66 * i          # 토픽 상자 위쪽 y (높이 48)
sy = lambda i: 62 + 76 * i          # 구독자 상자 위쪽 y (높이 62)
svg = ['<svg viewBox="0 0 960 540" xmlns="http://www.w3.org/2000/svg" font-family="Malgun Gothic, sans-serif">',
       '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
       '<path d="M0,0L10,5L0,10z" fill="#334155"/></marker>'
       '<marker id="b" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
       '<path d="M0,0L10,5L0,10z" fill="#15803d"/></marker></defs>',
       '<text x="110" y="34" text-anchor="middle" font-size="15" font-weight="700" fill="#1d4ed8">이벤트 발행자</text>',
       '<text x="450" y="34" text-anchor="middle" font-size="15" font-weight="700" fill="#334155">이벤트 브로커</text>',
       '<text x="830" y="34" text-anchor="middle" font-size="15" font-weight="700" fill="#15803d">이벤트 구독자(프로세서)</text>',
       '<rect x="300" y="52" width="300" height="456" rx="12" fill="#f1f5f9" stroke="#64748b" stroke-width="1.5"/>',
       '<text x="450" y="76" text-anchor="middle" font-size="12" fill="#475569">EventBroker — 전달만 · 이벤트 채널 = 토픽</text>']
for i, (t, ev, pub) in enumerate(TOPICS):
    y = ty(i)
    red = t == "deadletter"
    svg.append(f'<rect x="320" y="{y}" width="260" height="48" rx="6" fill="{"#fef2f2" if red else "#fff"}" '
               f'stroke="{"#dc2626" if red else "#94a3b8"}"/>')
    svg.append(f'<text x="450" y="{y + 20}" text-anchor="middle" font-size="13.5" font-weight="700" '
               f'font-family="Consolas, monospace" fill="#0f172a">{t}</text>')
    svg.append(f'<text x="450" y="{y + 38}" text-anchor="middle" font-size="11" fill="#64748b">{ev} · 발행: {pub}</text>')
for name, yy in [("BidProducer", ty(0)), ("AuctionClock", ty(2))]:
    svg.append(f'<rect x="20" y="{yy - 4}" width="180" height="56" rx="8" fill="#eff6ff" stroke="#2563eb" stroke-width="1.5"/>')
    svg.append(f'<text x="110" y="{yy + 20}" text-anchor="middle" font-size="14" font-weight="700" fill="#1e3a8a">{name}</text>')
    svg.append(f'<text x="110" y="{yy + 39}" text-anchor="middle" font-size="11" fill="#475569">'
               f'{"입찰 → 시작 이벤트" if name == "BidProducer" else "마감 → 시작 이벤트"}</text>')
    svg.append(f'<line x1="200" y1="{yy + 24}" x2="318" y2="{yy + 24}" stroke="#334155" stroke-width="2" marker-end="url(#a)"/>')
    svg.append(f'<text x="259" y="{yy + 16}" text-anchor="middle" font-size="10.5" fill="#334155">publish</text>')
for ti, si in EDGES:
    svg.append(f'<path d="M580,{ty(ti) + 24} C650,{ty(ti) + 24} 650,{sy(si) + 31} 718,{sy(si) + 31}" fill="none" '
               f'stroke="#15803d" stroke-width="1.6" stroke-dasharray="5 3" marker-end="url(#b)"/>')
for i, (name, role, pub) in enumerate(SUBS):
    y = sy(i)
    svg.append(f'<rect x="720" y="{y}" width="225" height="62" rx="8" fill="#f0fdf4" stroke="#16a34a" stroke-width="1.5"/>')
    svg.append(f'<text x="832" y="{y + 20}" text-anchor="middle" font-size="13.5" font-weight="700" fill="#14532d">{name}</text>')
    svg.append(f'<text x="832" y="{y + 37}" text-anchor="middle" font-size="11" fill="#475569">{role}</text>')
    if pub:
        svg.append(f'<text x="832" y="{y + 53}" text-anchor="middle" font-size="10.5" fill="#b45309">{pub}</text>')
svg.append('<line x1="320" y1="526" x2="350" y2="526" stroke="#334155" stroke-width="2" marker-end="url(#a)"/>'
           '<text x="356" y="530" font-size="11" fill="#334155">publish (즉시 반환)</text>'
           '<line x1="490" y1="526" x2="520" y2="526" stroke="#15803d" stroke-width="1.6" stroke-dasharray="5 3" marker-end="url(#b)"/>'
           '<text x="526" y="530" font-size="11" fill="#334155">구독별 큐로 전달 (JSON 문자열)</text>')
svg.append("</svg>")
svg = "\n".join(svg)

# ── 3. 보고서 HTML ──
code_lines = "".join(f'<span class="ln">{i:>3}</span>{esc(l)}\n' for i, l in enumerate(code.splitlines(), 1))
fig = lambda p, cap: f'<figure><img src="{p.relative_to(HERE).as_posix()}"><figcaption>{cap}</figcaption></figure>'

report = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>과제2 이벤트 드리븐 시스템 구현</title>
<style>
@page {{ size: A4; margin: 16mm 15mm; }}
body {{ font: 10.5pt/1.65 'Malgun Gothic', sans-serif; color: #1e293b; margin: 0; }}
h1 {{ font-size: 21pt; margin: 0 0 4mm; color: #0f172a; }}
h2 {{ font-size: 14.5pt; border-bottom: 2px solid #0f172a; padding-bottom: 2mm; margin: 9mm 0 4mm; break-after: avoid; }}
h3 {{ font-size: 11.5pt; margin: 6mm 0 2mm; color: #1d4ed8; break-after: avoid; }}
p {{ margin: 0 0 2.5mm; }}
table {{ border-collapse: collapse; width: 100%; margin: 2mm 0 4mm; font-size: 9.5pt; }}
th, td {{ border: 1px solid #cbd5e1; padding: 1.6mm 2.2mm; vertical-align: top; text-align: left; }}
th {{ background: #f1f5f9; }}
tr {{ break-inside: avoid; }}
code {{ font-family: Consolas, monospace; font-size: 9pt; background: #f1f5f9; padding: 0 1mm; border-radius: 2px; }}
pre {{ font: 8.6pt/1.5 Consolas, 'Malgun Gothic', monospace; background: #f8fafc; border: 1px solid #e2e8f0; padding: 3mm; white-space: pre-wrap; margin: 2mm 0 4mm; }}
blockquote {{ margin: 2mm 0 4mm; padding: 2mm 4mm; border-left: 3px solid #94a3b8; background: #f8fafc; white-space: pre-wrap; }}
figure {{ margin: 3mm 0 5mm; break-inside: avoid; }}
figure img {{ width: 100%; border: 1px solid #cbd5e1; }}
figure svg {{ width: 100%; height: auto; }}
figcaption {{ font-size: 9pt; color: #475569; margin-top: 1.5mm; }}
ol.flow li {{ margin-bottom: 1.6mm; }}
.cover {{ height: 255mm; display: flex; flex-direction: column; justify-content: center; break-after: page; }}
.cover .sub {{ font-size: 13pt; color: #475569; margin-bottom: 18mm; }}
.cover table {{ width: 90mm; font-size: 11pt; }}
.cover td {{ border: none; border-bottom: 1px solid #94a3b8; height: 9mm; }}
.cover td:first-child {{ width: 22mm; color: #475569; }}
.ok {{ color: #15803d; font-weight: 700; }}
.src {{ font-size: 7.4pt; line-height: 1.42; }}
.src .ln {{ color: #94a3b8; user-select: none; margin-right: 3mm; }}
.pb {{ break-before: page; }}
a {{ color: #1d4ed8; }}
.cover .repo {{ margin-top: 14mm; font-size: 11pt; color: #475569; line-height: 1.8; }}
.cover .repo a {{ font-size: 12pt; font-weight: 700; }}
.chain {{ background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 4px; padding: 2.5mm 4mm; font-size: 9.8pt; }}
</style></head><body>

<section class="cover">
<h1>과제2 · 이벤트 드리븐 아키텍처 스타일 시스템 구현</h1>
<div class="sub">온라인 경매 시스템 — 브로커 토폴로지 (Python asyncio)</div>
<table><tr><td>과목</td><td>소프트웨어아키텍처설계</td></tr><tr><td>학번</td><td>202110914</td></tr>
<tr><td>이름</td><td>김대진</td></tr><tr><td>제출일</td><td>2026년 10월 25일</td></tr></table>
<div class="repo">전체 자료(소스 코드 · 실행 로그 · 보고서)<br><a href="{REPO}">{REPO}</a></div>
</section>

<h2>1. 사용한 프롬프트</h2>
<p>AI 코딩 도구 Claude Code를 두 단계로 사용했습니다.</p>
<ul>
<li><b>프롬프트 1 — 제가 직접 한 지시</b>: 과제 설명을 그대로 Claude Code(메인 에이전트, Claude Opus 5.5)에 전달했습니다.</li>
<li><b>프롬프트 2 — 제 지시를 구체화한 구현 지시</b>: 메인 에이전트가 프롬프트 1을 바탕으로 도메인, 컴포넌트·커넥터·제약조건, 검증 방법을 구체화해 작성했습니다. 이 프롬프트를 구현 담당 서브에이전트(Claude Sonnet)에게 전달해 코드를 만들게 했습니다.</li>
<li><b>검토</b>: 메인 에이전트가 서브에이전트의 코드를 검토해 로그 문구 한 곳을 고쳤습니다. 이후 실행 결과와 별도 테스트로 동작을 확인하고 이 보고서를 만들었습니다.</li>
</ul>
<p class="chain">나 → <b>프롬프트 1</b> → 메인 에이전트(구체화) → <b>프롬프트 2</b> → 서브에이전트(구현) → 메인 에이전트(검토·실행·보고서)</p>
<h3>프롬프트 1 — 직접 한 지시 (메인 에이전트에 입력)</h3>
<blockquote>{esc(p1)}</blockquote>
<h3>프롬프트 2 — 구체화한 구현 지시 (메인 에이전트가 작성해 서브에이전트에 전달)</h3>
<pre>{esc(p2)}</pre>

<h2 class="pb">2. 시스템 구성과 전체 동작 흐름</h2>
<h3>2.1 도메인과 스타일 선택 이유</h3>
<p>온라인 경매는 입찰이 동시에 많이 들어옵니다. 입찰 하나를 기록·추적·분석·알림 서비스가 각자 처리해야 하므로 <b>응답성·확장성·기능확장성</b>이 중요합니다. 그래서 발행자와 구독자를 떼어 놓는 이벤트 드리븐 스타일을 골랐습니다.</p>
<p>흐름이 단순하고 처리 서비스끼리 순서를 맞출 필요가 거의 없으므로 중재자 대신 <b>브로커 토폴로지</b>를 썼습니다. 브로커는 워크플로우를 제어하지 않고 이벤트를 전달만 합니다.</p>

<h3>2.2 컴포넌트 · 커넥터 · 제약조건</h3>
<table>
<tr><th style="width:17%">구분</th><th style="width:37%">스타일의 요소</th><th>이 시스템의 구현</th></tr>
<tr><td rowspan="3"><b>컴포넌트</b></td><td>이벤트 발행자 (시작 이벤트)</td><td><code>BidProducer</code>(입찰 → BidPlaced), <code>AuctionClock</code>(마감 → AuctionClosed)</td></tr>
<tr><td>이벤트 브로커</td><td><code>EventBroker</code>: 토픽 6개와 구독별 큐를 관리합니다. 비즈니스 로직은 없습니다.</td></tr>
<tr><td>이벤트 구독자 (처리 후 처리 이벤트 발행 가능)</td><td><code>BidCapture</code>, <code>BidTracker</code>, <code>BidAnalytics</code>(워커 2개), <code>Notifier</code>, <code>Settlement</code>, <code>DeadLetterMonitor</code></td></tr>
<tr><td rowspan="3"><b>커넥터</b></td><td>이벤트 채널</td><td>토픽 6개: <code>bid.placed</code>, <code>bid.highest_changed</code>, <code>auction.closed</code>, <code>auction.settled</code>, <code>notification.sent</code>, <code>deadletter</code></td></tr>
<tr><td>통신 메커니즘</td><td><code>publish()</code>/<code>subscribe()</code>, 구독마다 하나씩 있는 <code>asyncio.Queue</code></td></tr>
<tr><td>데이터 형식 변환 (어댑터)</td><td><code>Envelope.dumps()</code>/<code>loads()</code>: 이벤트를 JSON 문자열로 바꿔 채널에 넣고, 꺼낼 때 객체로 되돌립니다. 봉투 필드는 event_id, type, topic, timestamp, ttl, payload입니다.</td></tr>
<tr><td rowspan="4"><b>제약조건</b></td><td>C1 컴포넌트끼리 직접 참조·호출하지 않음</td><td>모든 컴포넌트는 <code>broker</code>만 가집니다. 실행 시작 때 속성을 검사합니다.</td></tr>
<tr><td>C2 비동기 통신</td><td><code>publish()</code>는 <code>put_nowait</code>로 바로 반환합니다. 발행자는 처리 결과를 기다리지 않습니다.</td></tr>
<tr><td>C3 이벤트 = 이미 일어난 사실, 응답을 요구하지 않음</td><td>이벤트 이름이 모두 과거형입니다(BidPlaced, AuctionClosed …). 반환값을 쓰지 않습니다.</td></tr>
<tr><td>C4 익명성 (발행자는 구독자를 모름)</td><td>발행자는 토픽 이름만 압니다. 구독자 목록은 브로커 안에만 있습니다.</td></tr>
</table>

<h3>2.3 구성도</h3>
<figure>{svg}<figcaption>그림 1. 컴포넌트와 커넥터. 실선은 발행, 점선은 브로커가 구독별 큐로 전달하는 경로입니다. 구독자가 발행하는 처리 이벤트는 상자 아래 주황 글씨로 표시했습니다(선을 그리면 교차가 많아 생략).</figcaption></figure>

<h3>2.4 이벤트 목록</h3>
<table>
<tr><th>토픽</th><th>이벤트</th><th>발행자</th><th>구독자</th></tr>
<tr><td><code>bid.placed</code></td><td>BidPlaced</td><td>BidProducer</td><td>BidCapture, BidTracker, BidAnalytics</td></tr>
<tr><td><code>bid.highest_changed</code></td><td>HighestBidChanged</td><td>BidTracker</td><td>Notifier, Settlement</td></tr>
<tr><td><code>auction.closed</code></td><td>AuctionClosed</td><td>AuctionClock</td><td>Settlement</td></tr>
<tr><td><code>auction.settled</code></td><td>AuctionSettled</td><td>Settlement</td><td>Notifier</td></tr>
<tr><td><code>notification.sent</code></td><td>NotificationSent</td><td>Notifier</td><td>(없음 — 외부 연계용으로 열어 둠)</td></tr>
<tr><td><code>deadletter</code></td><td>EventDeadLettered</td><td>구독 워커(재시도 초과)</td><td>DeadLetterMonitor</td></tr>
</table>

<h3>2.5 전체 동작 흐름</h3>
<p>괄호 안 시각은 3장 실행 결과의 경과 시간입니다.</p>
<ol class="flow">
<li><b>시작·제약 검사</b>: 브로커와 컴포넌트 8개를 만들고, 구독자는 생성 시 토픽을 구독합니다. 각 컴포넌트에 다른 컴포넌트 참조가 없는지 검사합니다(C1).</li>
<li><b>입찰 발행</b>: <code>BidProducer</code>가 입찰 26건을 <code>bid.placed</code>에 발행합니다. 발행 전체가 {pub_ms}ms 만에 끝나고, 처리 결과는 기다리지 않습니다({ms(t_pub)}ms).</li>
<li><b>브로커 전달</b>: 브로커는 이벤트를 JSON으로 바꿔 <code>bid.placed</code>의 구독 큐 3개(Capture·Tracker·Analytics)에 각각 넣습니다. 구독자는 자기 큐를 자기 속도로 소비합니다.</li>
<li><b>기록·추적</b>: <code>BidCapture</code>가 입찰을 기록합니다. <code>BidTracker</code>는 최고가가 바뀔 때마다 처리 이벤트 <code>HighestBidChanged</code>를 발행합니다(317원 → 392원 → 398원).</li>
<li><b>연쇄 반응</b>: <code>HighestBidChanged</code>를 구독한 <code>Notifier</code>가 알림을 보냅니다. <code>Settlement</code>는 BidTracker를 호출하지 않고 이 이벤트로 자기 최고가를 유지합니다. 마지막 알림 시각은 {ms(t_noti)}ms입니다.</li>
<li><b>느린 분석·장애 격리</b>: <code>BidAnalytics</code>는 워커 2개가 한 큐를 나눠 처리합니다. 13의 배수 입찰에서 일부러 실패하면 3번 재시도한 뒤 <code>deadletter</code>로 보내고(첫 DLQ {ms(t_dlq1)}ms), <code>DeadLetterMonitor</code>가 받아 기록합니다. 다른 구독자는 이미 처리를 끝냈습니다.</li>
<li><b>만료</b>: ttl=0으로 보낸 입찰 3건은 모든 구독자가 처리하지 않고 버립니다.</li>
<li><b>마감·낙찰</b>: <code>AuctionClock</code>이 <code>AuctionClosed</code>를 발행합니다({ms(t_close)}ms). <code>Settlement</code>가 낙찰자를 확정해 <code>AuctionSettled</code>를 발행하고, <code>Notifier</code>가 결과를 알립니다({ms(t_settle)}ms).</li>
<li><b>요약</b>: C2~C4를 검사하고, 토픽별 발행 수와 구독별 처리·재시도·DLQ·만료 수를 출력합니다.</li>
</ol>

<h2 class="pb">3. 실행 결과 캡처</h2>
<p><code>python auction_eda.py</code>를 한 번 실행한 콘솔 출력입니다(Python 3.14, Windows 11, 약 1초). 출력 {len(log)}줄 전체를 세 장으로 나눠 실었습니다. 화면은 콘솔 출력을 터미널 화면으로 그려 캡처한 것이고, 같은 내용이 <code>run_log.txt</code>에 저장됩니다.</p>
{fig(caps[0], f"그림 2. 출력 {rng[0][0]}~{rng[0][1]}줄 — C1 제약 검사(PASS), 입찰 26건 발행({pub_ms}ms), BidCapture·BidTracker 처리, 최고가 변경 알림, 만료 폐기")}
{fig(caps[1], f"그림 3. 출력 {rng[1][0]}~{rng[1][1]}줄 — BidAnalytics 워커 #1·#2 분산 처리, 실패 → 재시도 1/3~3/3 → deadletter → DeadLetterMonitor 수신")}
{fig(caps[2], f"그림 4. 출력 {rng[2][0]}~{rng[2][1]}줄 — 경매 마감, 낙찰 확정·알림, C2~C4 검사, 요약 표")}

<h2 class="pb">4. 기타 — 설계 검증과 트레이드오프</h2>
<h3>4.1 제약조건을 지켰는지 확인한 방법</h3>
<table>
<tr><th style="width:24%">제약</th><th>확인 방법</th><th style="width:12%">결과</th></tr>
<tr><td>C1 직접 참조 없음</td><td>각 컴포넌트의 속성(<code>vars()</code>)을 재귀로 훑어 다른 컴포넌트 객체·바운드 메서드가 있는지 찾습니다. 검사기 자체가 동작하는지 보려고 일부러 A가 B를 참조하게 만든 객체로 위반을 잡아내는 자가검증도 합니다.</td><td class="ok">PASS</td></tr>
<tr><td>C2 비동기 발행</td><td>입찰 26건 발행 시간({pub_ms}ms)과 BidAnalytics 처리 시간({ana_ms}ms)을 비교합니다. 발행이 처리 완료를 기다렸다면 발행 시간이 처리 시간보다 길어야 합니다.</td><td class="ok">PASS</td></tr>
<tr><td>C3 과거형 이벤트</td><td>실제로 발행된 이벤트 type 6종이 모두 과거형인지 검사합니다.</td><td class="ok">PASS</td></tr>
<tr><td>C4 익명성</td><td>발행자 객체에 구독(Subscription) 정보가 없는지 검사합니다.</td><td class="ok">PASS</td></tr>
</table>

<h3>4.2 메시지 전달 신뢰성 (ED 슬라이드의 승인·만료)</h3>
<ul>
<li><b>승인(ack)</b>: 구독 워커는 처리가 끝나야 <code>task_done()</code>으로 승인합니다. 예외가 나면 최대 3번 재시도합니다.</li>
<li><b>데드레터</b>: 재시도를 넘기면 원래 이벤트를 <code>deadletter</code> 토픽으로 보내 잃지 않습니다. 실행 결과는 7건이었습니다.</li>
<li><b>만료(TTL)</b>: 봉투에 유효기간을 넣고, 만료된 이벤트는 처리하지 않고 버린 뒤 로그를 남깁니다.</li>
</ul>

<h3>4.3 실행에서 확인한 품질속성</h3>
<table>
<tr><th style="width:18%">품질속성</th><th>실행 결과에서 보이는 근거</th></tr>
<tr><td>응답성·성능</td><td>발행자는 {pub_ms}ms 만에 반환했습니다. 입찰자 알림은 {ms(t_noti)}ms에 끝났고, 느린 분석({ana_ms}ms)을 기다리지 않았습니다.</td></tr>
<tr><td>내고장성</td><td>BidAnalytics가 7건에서 실패하는 동안에도 BidCapture·BidTracker·Notifier는 23건을 모두 처리했습니다.</td></tr>
<tr><td>확장성</td><td>느린 BidAnalytics만 워커 2개로 늘렸고(#1 11건, #2 5건), 다른 서비스는 바꾸지 않았습니다.</td></tr>
<tr><td>기능확장성</td><td>새 구독자(예: DeadLetterMonitor)는 생성자에서 <code>listen()</code>으로 토픽을 구독하기만 하면 됩니다. 발행자 코드는 바뀌지 않습니다.</td></tr>
</table>

<h3>4.4 한계와 트레이드오프</h3>
<ul>
<li><b>흐름 제어가 없습니다.</b> 브로커는 "입찰 처리가 다 끝난 뒤 마감"을 보장하지 않습니다. 이 시연은 <code>broker.drain()</code>으로 큐가 빈 뒤에 마감 이벤트를 발행했습니다. 실제로는 마감 직전 입찰이 Settlement에 늦게 도착하면 낙찰자가 틀릴 수 있습니다(데이터 비일관성). 대책은 입찰 시각을 마감 시각과 비교하거나, 마감·낙찰 흐름만 중재자로 관리하는 것입니다.</li>
<li><b>복구성이 낮습니다.</b> deadletter로 간 분석 이벤트는 자동으로 다시 처리되지 않아 통계가 틀어집니다. 사람이 확인하거나 재투입 도구가 필요합니다.</li>
<li><b>디버깅이 어렵습니다.</b> 처리 순서가 구독자마다 달라서, 시각이 찍힌 로그 없이는 흐름을 따라가기 어렵습니다.</li>
<li><b>인메모리 브로커입니다.</b> 프로세스가 끝나면 큐가 사라집니다. 실제 시스템에서는 Kafka, RabbitMQ처럼 메시지를 영속 저장하는 브로커로 바꿉니다. 컴포넌트는 <code>publish/subscribe</code>만 쓰므로 브로커만 교체하면 됩니다.</li>
</ul>

<h3>4.5 실행 방법과 파일</h3>
<table>
<tr><th style="width:30%">파일</th><th>내용</th></tr>
<tr><td><code>auction_eda.py</code></td><td>시스템 전체(표준 라이브러리만 사용, {len(code.splitlines())}줄). <code>python auction_eda.py</code>로 실행합니다.</td></tr>
<tr><td><code>run_log.txt</code></td><td>실행 출력 저장본</td></tr>
<tr><td><code>prompt.md</code></td><td>1장의 프롬프트 원문</td></tr>
</table>

<h2 class="pb">부록. 소스 코드 (auction_eda.py)</h2>
<pre class="src">{code_lines}</pre>
</body></html>"""
(HERE / "report.html").write_text(report, encoding="utf-8")

# ── 4. PDF (이번 실행의 결과인지 확인) ──
tmp = Path(tempfile.mkdtemp()) / "report.pdf"
start = time.time()
r = subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    "--virtual-time-budget=4000", f"--print-to-pdf={tmp}", (HERE / "report.html").as_uri()],
                   capture_output=True, timeout=120)
if not tmp.exists() or tmp.stat().st_mtime < start - 1:
    sys.exit(f"PDF 생성 실패 (exit {r.returncode})")
from pypdf import PdfReader
n = len(PdfReader(str(tmp)).pages)
final = HERE / "과제2-보고서.pdf"
os.replace(tmp, final)
print(f"ok {final.name} {n}쪽, 캡처 {[c.name for c in caps]}")
