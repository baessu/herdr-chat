# herdr-chat

[English](README.md)

휴대폰에서 코딩 에이전트의 대화를 읽고 답하는 웹앱입니다. Mac 에서 [Herdr](https://herdr.dev) 로
띄운 터미널(Claude Code, Codex, opencode, Antigravity)을 iPhone·iPad·Android·다른 컴퓨터의 브라우저에서
채팅처럼 보여 줍니다.

> **비공식 프로젝트입니다.** Herdr, Anthropic, OpenAI 와 관계없는 개인 프로젝트입니다.

- **내 Mac 에서 직접 돌립니다.** 따로 가입할 서비스나 계정이 없습니다. 대화는 그 Mac 과 내 기기
  사이에서 내 Tailscale 네트워크 안으로만 오갑니다. 알림만 Apple·Google·Mozilla 푸시 서버를
  암호화된 채로 거치며, 대화 내용 없이 위치와 제목만 담습니다.
- **내 Tailscale 네트워크 안에서만 열립니다.** 서버는 `127.0.0.1` 에서만 듣습니다.
- **파이썬 기본 기능만 씁니다.** 푸시 알림만 선택으로 부품 하나(`cryptography`)를 씁니다.

## 할 수 있는 것

- 이 Mac 과, Herdr 로 연결한 다른 Mac 의 터미널 목록 보기
- Claude Code·Codex·opencode·Antigravity 대화를 채팅으로 보기(각 프로그램의 기록 파일을 읽기만
  함). 다른 프로그램은 터미널 화면으로 보기
- 답장 보내기, 승인 요청에 한 번에 답하기, 선택형 질문에 답하기, 작업 중지, 권한 모드 바꾸기
- 휴대폰의 사진·파일 첨부, 에이전트가 만든 파일 열어 보기
- 에이전트가 답을 기다리면 푸시 알림(iOS 16.4 이상, 홈 화면에 추가한 앱)
- 선택: Herdr 내장 브라우저와 평소 쓰는 Chrome 탭을 보고 누르기. Chrome 은
  `chrome://inspect/#remote-debugging` 을 직접 켜고 Chrome 의 **허용** 창을 눌러야만 됩니다. 그 탭들은
  내 계정으로 로그인된 상태이니 필요할 때만 켜세요.

## 필요한 것

| | |
|---|---|
| 켜 둘 Mac | 서버가 돕니다. macOS 15(Intel)에서 시험했습니다. 다른 Mac 은 Herdr 와 `python3` 만 있으면 됩니다. |
| [Herdr](https://herdr.dev) | `hc` 명령이 있어야 합니다(Herdr Core `hc 0.1.21` 로 시험). |
| Python 3.9 이상 | macOS 에 들어 있는 것으로 됩니다(`python3 --version`). |
| [Tailscale](https://tailscale.com) | 무료 요금제로 충분합니다. Mac **과** 쓰는 기기 모두에 설치합니다. |
| 접속 기기 | 내 Tailscale 네트워크 안의 브라우저면 됩니다. iPhone·iPad 에서 매일 쓰고 있고, Android·컴퓨터 브라우저는 덜 시험했습니다. |

## 설치

### 방법 A: 코딩 에이전트에게 맡기기

Mac 에서 Claude Code 나 Codex 를 열고 아래를 붙여 넣으세요.

```
이 Mac 에 herdr-chat 을 설치해 줘. 아래 문서를 그대로 따라 해.
https://github.com/baessu/herdr-chat/blob/main/docs/AGENT_SETUP.md
내가 해야 하는 단계에서는 멈추고 나한테 물어봐.
```

에이전트가 설치하고, 서버를 켜고, Tailscale 을 연결한 뒤 휴대폰에서 무엇을 누르면 되는지 알려
줍니다. 네트워크 설정을 바꾸거나 로그인이 필요한 단계에서는 먼저 묻습니다.

### 방법 B: 직접 하기

```sh
git clone https://github.com/baessu/herdr-chat.git ~/herdr-chat
cd ~/herdr-chat
python3 server.py            # "herdr-chat: http://127.0.0.1:5393" 이 보이면 켜진 것
```

1. 처음 켜면 접속 토큰이 `~/.config/herdr-chat/token` 에 생깁니다(나만 읽을 수 있음).
2. 서버를 내 Tailscale 네트워크에 엽니다: [docs/TAILSCALE.ko.md](docs/TAILSCALE.ko.md)
3. 휴대폰(또는 내 Tailscale 네트워크 안의 아무 기기)에서 `https://<내 Mac>.<내 tailnet>.ts.net` 을
   열고 토큰을 붙여 넣습니다. iPhone·iPad 는 공유 → **홈 화면에 추가** 하면 앱처럼 씁니다.

로그인할 때 자동으로 켜려면 [docs/AGENT_SETUP.md](docs/AGENT_SETUP.md#7-run-at-login) 의
"Run at login" 을 봅니다.

푸시 알림을 쓰려면(선택):

```sh
python3 -m venv .venv && .venv/bin/pip install cryptography
.venv/bin/python server.py
```

## 설정

모두 선택이며 환경 변수로 줍니다. 표는 [README.md](README.md#settings) 에 있습니다. 자주 쓰는 것:

- `HERDR_CHAT_MACHINE_LABELS="local=iMac,macbook-pro=MacBook"`: 화면에 보일 기기 이름
  (`hc machine ls` 로 id 확인)
- `HERDR_CHAT_PUSH_CONTACT="mailto:나@example.com"`: 푸시 서비스에 알릴 연락처

## 보안

herdr-chat 은 내 터미널에 글을 칠 수 있습니다. 토큰을 비밀번호처럼 다루세요. 쓰기 전에
[SECURITY.md](SECURITY.md) 를 읽어 주세요. 요점:

- 인터넷에 공개하지 않습니다(`tailscale funnel`, 공유기 포트 열기, 공개 터널 금지).
- 일반 셸과 Herdr 자체 터미널에는 앱에서 입력할 수 없습니다.
- 터미널에 보낸 입력은 `logs/audit.jsonl` 에 시각·터미널·종류·글자 수만 남습니다. 보낸 글은
  남기지 않습니다.

## 한계

- 일부 기능(권한 모드 표시 등)은 에이전트 화면의 글자를 읽어서 동작합니다. Claude Code·Codex 가
  업데이트로 화면을 바꾸면, 여기서 고칠 때까지 그 기능이 안 될 수 있습니다.
- 화면은 지금 한국어만 있습니다.

## 라이선스

[MIT](LICENSE). 있는 그대로 제공하며 어떤 보증도 하지 않습니다.
