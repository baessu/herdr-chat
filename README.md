# herdr-chat

[한국어](README.ko.md)

Read and answer your coding agents from your phone. herdr-chat is a small web app that shows the
[Herdr](https://herdr.dev) terminals on your Mac (Claude Code, Codex, opencode, Antigravity) as chat
conversations in a browser: iPhone, iPad, Android, or another computer.

> **Unofficial.** herdr-chat is a community project. It is not made by or affiliated with Herdr,
> Anthropic or OpenAI.

- **Runs on your own Mac.** There is no hosted service and no account. Conversations travel only
  between that Mac and your own devices, inside your Tailscale network. Notifications go through
  Apple/Google/Mozilla push servers, encrypted, and carry only the location and title, never the
  conversation.
- **Reached only through your private Tailscale network.** The server listens on `127.0.0.1` only.
- **Python standard library only.** Push notifications optionally use one package (`cryptography`).

The interface is in Korean for now.

## What it does

- Lists every Herdr terminal on this Mac, and on your other Macs connected to Herdr.
- Shows Claude Code, Codex, opencode and Antigravity sessions as a chat (read from their own
  history files, read-only). Other programs are shown as their terminal screen.
- Lets you reply, answer approval prompts with one tap, answer multiple-choice questions, stop a
  running agent, and switch an agent's permission mode.
- Attaches photos and files from your phone, and opens the files an agent mentions.
- Sends a push notification when an agent is waiting for you (iOS 16.4+, home-screen app).
- Optional: view and tap Herdr's built-in browser, and tabs of your everyday Chrome. Chrome only
  works after you turn on `chrome://inspect/#remote-debugging` and click Chrome's **Allow** prompt.
  Those tabs are logged in as you, so leave this off unless you need it.

## What you need

| | |
|---|---|
| A Mac that stays on | Runs the server. Tested on macOS 15 (Intel). Your other Macs only need Herdr and `python3`. |
| [Herdr](https://herdr.dev) | With its `hc` command (tested with Herdr Core `hc 0.1.21`). |
| Python 3.9 or newer | The one that comes with macOS works (`python3 --version`). |
| [Tailscale](https://tailscale.com) | Free plan is enough. Installed on the Mac **and** on each device you use. |
| Devices | Any browser on your Tailscale network. Used daily on iPhone and iPad; Android and desktop browsers should work but are less tested. |

## Setup

### Option A: let your coding agent do it

Open Claude Code or Codex on the Mac and paste this:

```
Set up herdr-chat on this Mac by following
https://github.com/baessu/herdr-chat/blob/main/docs/AGENT_SETUP.md
Stop and ask me whenever a step needs me.
```

The agent installs it, starts the server, connects Tailscale and tells you exactly what to tap on
your phone. It stops and asks before anything that changes your network or needs your login.

### Option B: by hand

```sh
git clone https://github.com/baessu/herdr-chat.git ~/herdr-chat
cd ~/herdr-chat
python3 server.py            # prints: herdr-chat: http://127.0.0.1:5393
```

1. The first start creates your access token at `~/.config/herdr-chat/token` (readable only by you).
2. Put the server on your Tailscale network: [docs/TAILSCALE.md](docs/TAILSCALE.md).
3. On your phone (or any device on your Tailscale network), open
   `https://<your-mac>.<your-tailnet>.ts.net` and paste the token. On iPhone/iPad, Share → **Add to
   Home Screen** makes it an app.

To start the server automatically at login, see "Run at login" in
[docs/AGENT_SETUP.md](docs/AGENT_SETUP.md#7-run-at-login).

Optional, for push notifications:

```sh
python3 -m venv .venv && .venv/bin/pip install cryptography
.venv/bin/python server.py
```

## Settings

All optional, as environment variables:

| Variable | Default | |
|---|---|---|
| `--port` (argument) | `5393` | Port on `127.0.0.1` |
| `HERDR_CHAT_MACHINE_LABELS` | `local=이 컴퓨터` | Display names, e.g. `local=iMac,macbook-pro=MacBook` (`hc machine ls` shows the ids) |
| `HERDR_CHAT_PUSH_CONTACT` | `mailto:noreply@example.com` | Contact sent to push services. Set your own address. |
| `HERDR_CHAT_FILES_ROOT` | your home folder | Files and documents outside it are not opened |
| `HERDR_CHAT_CONFIG_DIR` | `~/.config/herdr-chat` | Push keys and subscriptions |
| `HERDR_CHAT_TOKEN_FILE` | `~/.config/herdr-chat/token` | Access token |
| `HC_BIN` | `hc` | Path to Herdr's `hc` |

## Security

herdr-chat can type into your terminals, so treat the token like a password.
Read [SECURITY.md](SECURITY.md) before you use it. In short:

- Never expose it to the internet (`tailscale funnel`, port forwarding, a public tunnel).
- Plain shells and Herdr's own terminals cannot receive input from the app.
- Input sent to terminals is logged to `logs/audit.jsonl` with time, terminal, kind and length, never
  the text.

## How it works

- `server.py`: HTTP server, token check, and the API in [API.md](API.md).
- `hc_client.py`: calls Herdr's `hc` with fixed arguments, never through a shell.
- `mapping.py`: finds the history file of the agent in each terminal (by process id and start time).
- `transcripts.py`: turns Claude Code, Codex, opencode and Antigravity history into chat messages.
- `remote.py`: runs the two files above on your other Macs through `hc exec`, read-only.
- `web/`: the phone app (plain HTML, CSS and JavaScript). Open `web/index.html?mock` for a demo with
  fake data.

Some features read the agents' screens (for example the permission mode line). A new Claude Code or
Codex version can change those screens and break a feature until it is updated here.

## Development

```sh
python3 -m unittest discover -s tests
```

Tests use a fake `hc` (`tests/fake_hc.py`) and never touch your real terminals.

## License

[MIT](LICENSE). Provided as is, without warranty.
