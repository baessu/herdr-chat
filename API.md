# herdr-chat API 약속 (v0)

서버(server.py)와 화면(web/)이 주고받는 HTTP 규격이다. 한쪽을 바꿀 때는 이 파일을 먼저 고친다.

## 공통
- 서버: Python 표준 라이브러리만. `127.0.0.1:5393` 에서만 듣는다. iPad 는 `tailscale serve` (HTTPS) 로 들어온다.
- 인증: `Authorization: Bearer <token>` 또는 쿠키 `hc_token`. 토큰 파일 `~/.config/herdr-chat/token` (없으면 서버가 만들고 권한 600).
- 응답은 JSON, UTF-8(파일·이미지·이벤트 스트림은 예외로 그 형식대로). 오류는 `{"error": "<code>", "message": "<한국어 설명>"}` 와 4xx/5xx.
- 터미널 id 는 herdr 주소 그대로 (`t8p`, 다른 기기는 `macbook-pro/t37`).
- 모든 API 응답에 `Server-Timing: app;dur=<ms>` 헤더가 붙는다(측정용).

## GET /api/machines
```json
{"machines": [{"id": "local", "label": "iMac", "state": "connected"},
  {"id": "macbook-pro", "label": "MacBook", "state": "unreachable", "why": "…", "since_ms": 1794659680796}]}
```
- label 은 서버 상수(local=iMac, macbook-pro=MacBook, 그 밖은 이름 그대로). state 는 hc 가 준 값.

## GET /api/terminals
```json
{"terminals": [{
  "id": "t8p", "machine": "local", "agent": "claude-code",
  "where": "웹사이트 리뉴얼 / UX·UI 설계", "title": "BRIEF.md 기반 UX·UI 방향 논의",
  "space_id": "sp0b06…", "tab_id": "tb1e3a…",   // labels["herdr.space"]·["herdr.tab"], 없으면 null
  "space": "웹사이트 리뉴얼", "tab": "UX·UI 설계", // where 를 첫 " / " 에서 나눔. where 없으면 "(위치 없음)"·null
  "status": "working",            // working | blocked | done | idle | null(상태 보고 없음)
  "turn": 39, "status_ms": 1794656252418,
  "chat": true,                   // 대화 기록 파일과 짝지어졌으면 true, 아니면 화면만. 다른 기기는 remote.py 로 그 기기에서 짝짓는다(30초 재사용)
  "writable": true                // 셸(zsh·sh·bash)·Herdr 는 false
}]}
```

## GET /api/layout
기기 → 공간 → 탭 → 칸 구성. 순서는 herdr 그대로다. 성공은 10초, 연결 안 된 기기의 오류는 30초 캐시한다(다른 기기는 3초 안에 답이 없으면 오류). 레이아웃에 없는 터미널(예: 워커)은 /api/terminals 의 where 로 처리한다.
```json
{"machines": [
  {"id": "local", "spaces": [{"id": "sp…", "name": "내 프로젝트",   // 이름이 비면 folder 의 마지막 이름
    "tabs": [{"id": "tb…", "name": "출시 계획", "items": [
      {"kind": "terminal", "id": "t1n"},                   // 다른 기기는 "macbook-pro/t37" (/api/terminals 와 같은 id)
      {"kind": "doc", "doc_id": "3f9a…", "title": "ROADMAP.md", "mod": "md-viewer"},
      {"kind": "mod", "mod": "web-viewer"}                // 그 밖의 mod 는 이름만, 내용 없음
    ]}]}]},
  {"id": "macbook-pro", "spaces": null, "error": "unreachable"}]}
```
- 다른 기기의 md-viewer 는 아직 열 수 없어 `{"kind": "mod", "mod": "md-viewer", "title": "<파일 이름>"}` 로 준다(아직 미구현).

## POST /api/layout/space · /api/layout/tab · /api/layout/panel
새 공간·탭·칸. herdr 의 `herdr-app` 문서를 버전 확인(doc.set version)으로 고치고, 새 칸 터미널을 `hc run --bg` 로 띄운다(라벨 herdr.space·herdr.tab). Herdr 앱이 곧바로 반영한다.
- 공통: `{"machine": "local"|"macbook-pro", "program": "claude"|"codex"|"shell"}`. claude·codex 는 Herdr 와 같은 감싸개로 띄워 끝나면 로그인 셸이 남는다.
- space: `name`(필수, 60자), `folder`(없으면 홈, 그 기기에 있어야 함). 첫 탭 이름은 "1".
- tab: `space_id`, `name`. panel: `space_id`, `tab_id`(칸 너비는 똑같이 나눔).
- 성공 `{"ok": true, "id": "<새 터미널>", "space_id", "tab_id"}`. 고치기 전 문서를 `logs/layout-backups/` 에 남긴다(최근 50개).
- 오류: 400 `bad_name`·`bad_folder`·`bad_program`, 404 `layout_not_found`, 409(문서가 세 번 연속 바뀜).

## GET /api/docs/{doc_id}
`{"title": "ROADMAP.md", "text": "마크다운 원문", "mtime_ms": 1794656252418}`
- 경로는 주고받지 않는다. doc_id 는 /api/layout 이 준 값만 쓰며, 지금 레이아웃에 있는 md-viewer 파일만 열린다.
- 읽기 전용. `.md .markdown .txt` 만, 2MB 까지. 오류: 404 `doc_not_found`, 403 `doc_not_allowed`(확장자·링크 바꿔치기), 413 `doc_too_large`.

## GET /api/terminals/{id}/files
그 터미널이 만든 결과물 목록. 경로는 주고받지 않고, 서버가 발급한 file_id 로만 연다.
```json
{"files": [{"file_id": "06444b7d…", "name": "MORNING_BRIEF.html", "rel": "research/overnight/MORNING_BRIEF.html",
  "kind": "html",                 // md | html | image | pdf | csv | text
  "size": 152828, "mtime_ms": 1794656252418,
  "source": "mentioned"}],         // mentioned: 대화 기록 최근 500개 메시지에 나온 경로, recent: cwd 아래 24시간 안에 바뀐 파일
```
- 같은 응답에 `"links": [{"url": "https://…", "host": "…", "ts_ms": …}]`: 에이전트 답에 나온 http(s) 링크, 최신순 50개. 다른 기기 터미널은 files 는 빈 목록, links 만.
- rel 은 cwd 기준, cwd 밖이면 `~/…`. mentioned 가 먼저, 그다음 recent 최신순 50개. 화면 모드 터미널은 recent 만, 다른 기기 터미널은 지금 빈 목록.
- recent 는 숨김·`.git`·`node_modules`·`.venv`·`__pycache__` 폴더를 건너뛰고 파일 5000개·1초에서 멈춘다.

## GET /api/files/{file_id}
파일 내용 그대로(JSON 아님), 알맞은 Content-Type. 쿠키만으로도 인증된다(iframe·img 용).
- 열리는 조건(목록을 만들 때와 열 때 모두 검사): 실제 경로가 홈 아래, 경로의 어느 칸도 `.` 으로 시작하지 않음, 어느 칸에도 `key secret token credential password .pem .p12 id_rsa` 가 없음, 확장자 `md markdown txt log csv tsv json html htm png jpg jpeg gif webp svg pdf`, 텍스트 2MB·그 외 30MB.
- html·svg 는 `Content-Security-Policy: sandbox allow-scripts; default-src 'none'; …` 로 고유 출처 샌드박스에서 열린다(앱 토큰·쿠키·API 에 닿지 않음).
- 오류(JSON): 404 `file_not_found`(목록 밖 id·서버 재시작 뒤 → 목록을 다시 받는다), 403 `file_not_allowed`(링크 바꿔치기 등), 413 `file_too_large`.

## GET /api/terminals/{id}/messages?before=<cursor>&limit=50
최신 limit 개를 오래된→최신 순서로 준다. 위로 스크롤하면 `before` 로 더 받는다.
- 경로의 id 는 encodeURIComponent 되어 온다(`macbook-pro%2Ft37`). 서버가 디코딩한다.
- cursor 는 문자열 그대로 되돌아온다. message id 는 한 터미널 안에서 유일하다.
- `/api/events` 는 쿠키 `hc_token`(encodeURIComponent 값)만으로 인증한다.
- web/ 정적 파일은 같은 주소의 `/` 에서 제공한다.
```json
{"source": "transcript",          // transcript | screen (짝짓기 실패 시 화면 글자)
 "messages": [{
   "id": "<기록 파일 안 고유값>", "role": "user",   // user | assistant | tool
   "text": "마크다운 원문", "ts_ms": 1794656252418,
   "tool": {"name": "Bash", "summary": "ls ~/Projects"}   // role=tool 일 때만
 }],
 "cursor": "<더 오래된 쪽을 받을 값, 끝이면 null>"}
```
- 생각 과정(reasoning)·토큰 집계·내부 이벤트는 넣지 않는다. 도구 호출은 한 줄 요약만.
- `source=screen` 이면 messages 는 1개(role=tool, name="screen")이고 text 가 화면 글자다.
- `"queued": true`(선택): 에이전트가 일하는 중이라 아직 전달되지 않고 대기 중인 user 메시지다(Claude 만). 최신 페이지(before 없음)의 끝에만 붙고, id 는 `queue-<숫자>` 다. 전달되면 사라지고 같은 글이 일반 user 메시지로 나온다.

## GET /api/terminals/{id}/screen?lines=60
`{"text": "...", "status": "blocked"}` 대기 중일 때 화면이 보여 줄 원문.

## POST /api/terminals/{id}/send   `{"text": "..."}`
## POST /api/terminals/{id}/keys   `{"keys": ["enter"]}`
허용 키: `enter esc up down left right tab 1 2 3 4 5 6 7 8 9 y n ctrl+c`.
## POST /api/terminals/{id}/attach   `{"name": "photo.jpg", "data": "<base64>"}`
파일을 터미널 옆 전용 폴더에 놓는다(`hc put`, 아무것도 입력하지 않음. 터미널이 끝나면 지워짐). 다른 기기 터미널은 그 기기에 놓인다.
- 성공: `{"ok": true, "path": "<터미널 쪽 경로>", "name": "<실제로 놓인 이름>", "size": <바이트>}`. 이 path 를 send 글에 넣어 에이전트에게 알린다.
- 이름은 서버가 정리한다(basename, 앞 '.' 제거, 한글·영문·숫자·공백·`_-.()` 밖은 `_`, 120자, 확장자 없으면 `.bin`). hc 가 한 번 더 바꿀 수 있어(실측: 한글·공백·괄호 → `_`) 응답의 name 을 쓴다.
- 디코딩 후 8MB 초과 413 `file_too_large`, base64 오류 400 `bad_data`, 셸·Herdr 403 `shell_blocked`. 이 요청만 본문 한도가 약 11MB(나머지 64KB).
- 감사 기록에는 시각·id·'attach'·정리된 이름·바이트만 남는다.
## GET /api/terminals/{id}/suggest
`{"text": "다음에 보낼 말" | null}` — Claude Code 가 빈 입력칸에 흐리게 띄우는 추천. 화면 맨 아래 입력 상자에서 읽는다(작업 중·대기 중이면 null).

## GET /api/terminals/{id}/browser
Herdr 브라우저(terminal-browser) 터미널의 지금 화면 이미지(JPEG). 헤더 `X-Page-Url`·`X-Page-Title`(퍼센트 인코딩). 보기만 한다(cdp.py, 127.0.0.1 DevTools). 이 터미널의 /messages 는 `{"source": "browser"}`. 다른 기기 브라우저는 아직 안 됨.

## POST /api/terminals/{id}/answer   `{"answers": [{"choices": [2]}, {"choices": [1, 3]}, {"text": "직접 쓴 답"}]}`
떠 있는 선택형 질문(Claude AskUserQuestion)에 답한다. 질문마다 하나씩, 번호는 1부터. 서버가 실측한 키 순서로 바꿔 보낸다(한 개 고르기: 숫자 / 여러 개: 숫자들 뒤 → / 직접 입력: 선택지수+1 뒤 글+Enter / 끝에 검토 화면이면 1).
- 메시지의 `ask`: `{"tool": "claude"|"codex", "questions": [{"header", "question", "multi", "options": [{"label", "description"}]}]}`. 최신 페이지의 마지막 메시지가 ask 면 응답에 `"asking": true`.
- 오류: 409 `no_question`(이미 답했거나 질문 없음), 400 `bad_answer`. Codex 질문은 아직 화면에서 답한다.
## GET /api/terminals/{id}/mode · POST /api/terminals/{id}/mode   `{"mode": "plan"}`
권한 모드. GET: `{"modes": [{"id", "label", "desc"}], "current": id|null}`. Claude Code·Codex 만 목록이 있고 나머지는 `[]`.
- Claude Code: 입력 상자 아래 줄(`⏵⏵ auto mode on` 등)에서 읽고, shift+tab 을 눌러 원하는 모드가 화면에 뜰 때까지 돈다(실측 순서 auto → manual → accept-edits → plan). id: `auto manual accept-edits plan bypass`. 한 바퀴에 없으면 409 `mode_unavailable`(bypass 는 켜 두고 시작한 세션에만 있음).
- Codex: 화면에 모드가 없어 GET 의 current 는 이 서버가 마지막으로 바꾼 값(모르면 null). POST 는 `/permissions` 를 열어 번호를 누르고(Full Access 의 재확인은 1), 다시 열어 `(current)` 로 확인한 뒤 닫는다. id: `ask auto full read-only`. 일하는 중이면 409 `busy`. 확인 실패 502 `mode_failed`.
- 답 대기(blocked) 중이면 409 `busy`. 성공 `{"ok": true, "current": id}`. 감사 기록에 kind `mode` 와 고른 id 를 남긴다.
## POST /api/terminals/{id}/interrupt
- 세 요청 모두 `writable=false` 면 403 `shell_blocked`.
- 성공 시 `{"ok": true}`. 모든 요청은 `logs/audit.jsonl` 에 (시각, id, 종류, 글자 수) 를 남긴다. 보낸 원문은 남기지 않는다.

## 푸시 알림 (Web Push)
- `GET /api/push/key` → `{"public_key": "<base64url, 65바이트 uncompressed>"}` (PushManager.subscribe 의 applicationServerKey)
- `POST /api/push/subscribe` `{"subscription": <PushSubscription.toJSON()>, "prefs": {"blocked": true, "done": false}}` → `{"ok": true, "prefs": {...}}`
  - endpoint 는 https 이고 호스트가 `*.push.apple.com`·`fcm.googleapis.com`·`*.push.services.mozilla.com` 일 때만 받는다. 아니면 400 `endpoint_not_allowed`. 키가 틀리면 400 `bad_keys`.
- `POST /api/push/unsubscribe` `{"endpoint": "..."}` → `{"ok": true, "removed": 0|1}`
- 알림 내용(서비스 워커 push 이벤트의 JSON): `{"title": "답을 기다리는 중"|"작업을 마쳤습니다", "body": "<기기> · <공간> / <탭> · <제목>", "id": "<터미널 id>", "tag": "<터미널 id>"}`. 대화 원문은 없다.
- 보내는 때: blocked 가 될 때, prefs.done 이면 working→done 일 때. 같은 터미널은 60초에 한 번. 푸시 서버가 404·410 을 주면 구독을 지운다.
- cryptography 가 없는 파이썬으로 켜면 세 요청 모두 503 `push_unavailable`.

## GET /api/events   (Server-Sent Events)
```
event: terminal
data: {"id": "t8p", "status": "blocked", "turn": 40, "status_ms": ...}

event: message
data: {"id": "t8p"}              // 새 메시지가 생김 → 화면이 /messages 를 다시 부른다

event: gone
data: {"id": "t8p"}

event: machine
data: {"id": "macbook-pro", "state": "connected"}   // 기기 연결 상태가 바뀜(10초마다 확인)
```
