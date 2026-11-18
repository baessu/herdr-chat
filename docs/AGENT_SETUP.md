# Setting up herdr-chat: instructions for a coding agent

You are a coding agent (Claude Code, Codex or similar) running on the user's Mac. The user asked you
to set up herdr-chat. Follow the steps in order. Each step says how to check it worked.

The user may not be a developer. Explain what you are doing in one plain sentence per step, in the
user's language. Do not dump command output on them.

## Rules

1. **Stop and ask** before:
   - running `tailscale serve` (it changes the network setup);
   - installing the login item (step 7);
   - installing anything with Homebrew or pip.
2. **Never** run `tailscale funnel`, open router ports, or use any public tunnel. herdr-chat must only
   be reachable inside the user's Tailscale network.
3. **Never print the access token** into this conversation. Your conversation history is stored on
   disk, and herdr-chat itself displays agent conversations. Use `pbcopy` (step 5) instead.
4. Things only the user can do: install apps from the App Store, sign in to Tailscale, approve
   macOS permission dialogs, and use their phone. Say exactly what to tap, then wait.
5. If a check fails, use the troubleshooting table at the end. Do not improvise network or security
   changes.

## 1. Check what is already there

```sh
sw_vers -productVersion        # macOS version
python3 --version              # needs 3.9 or newer
command -v git
command -v hc && hc --version  # Herdr's command
hc ls >/dev/null && echo "herdr ok"
command -v tailscale || ls /Applications/Tailscale.app/Contents/MacOS/Tailscale
```

- No `python3` or `git`: run `xcode-select --install` and ask the user to click **Install** in the
  dialog that appears. Wait for it to finish.
- No `hc`, or `hc ls` fails: the user needs Herdr installed and running (https://herdr.dev). Ask them
  to install and open it, then check again. If Herdr is installed but `hc` is still not found, look
  for it (`ls ~/.local/bin/hc /usr/local/bin/hc /opt/homebrew/bin/hc 2>/dev/null`) and ask the user
  before changing their `PATH`.
- No Tailscale: that is fine for now. Step 4 installs it.

## 2. Get the code

Default location is `~/herdr-chat`. If that folder already exists, ask before touching it.

```sh
git clone https://github.com/baessu/herdr-chat.git ~/herdr-chat
cd ~/herdr-chat
python3 -m unittest discover -s tests     # expect: OK (some skipped)
```

## 3. Start the server once

Pick the port. Use 5393 unless something already listens on it:

```sh
lsof -nP -iTCP:5393 -sTCP:LISTEN || echo "5393 is free"
launchctl print gui/$(id -u)/com.herdr-chat.server >/dev/null 2>&1 && echo "already installed"
```

If it says "already installed", herdr-chat was set up before: skip step 3 and step 7, and reuse that
server (restart it with `launchctl kickstart -k gui/$(id -u)/com.herdr-chat.server`).
If another program holds 5393, pick another port (for example 5394) and use it in every later step.

Start a temporary server in the background, remember its process id, and check it:

```sh
cd ~/herdr-chat
nohup python3 server.py --port 5393 > /tmp/herdr-chat.log 2>&1 &
echo $! > /tmp/herdr-chat.pid     # a plain command (no `&&` before it), so $! is the server itself
sleep 2
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5393/api/terminals   # expect 401
ls -l ~/.config/herdr-chat/token                                                # expect -rw-------
```

`401` means the server is up and correctly refuses requests without the token. It stays running
until step 7 replaces it.

If the user has other Macs connected to Herdr (`hc machine ls`), ask what to call each one, and
remember them for step 7 as `HERDR_CHAT_MACHINE_LABELS`, for example
`local=iMac,macbook-pro=MacBook`.

## 4. Tailscale

Follow [TAILSCALE.md](TAILSCALE.md). Check first, change nothing yet:

```sh
tailscale status          # the Mac must show as connected
tailscale serve status    # what this Mac already shares
```

- `serve status` shows nothing: ask the user, then run `tailscale serve --bg 5393`.
- It already shares something on the default address: do **not** replace it. Show the user what is
  there, and with their OK use a port that `serve status` does not list yet, such as 8443:
  `tailscale serve --bg --https=8443 5393`. The address then ends in `:8443`.

Run `tailscale serve status` again. It should show `https://<mac>.<tailnet>.ts.net` (plus `:8443` if
you used it) pointing at `http://127.0.0.1:5393`.

If `tailscale serve` prints a link about enabling HTTPS, give the user that link and wait until they
have enabled it, then run it again.

## 5. The phone, tablet or other computer

The server runs on this one Mac. Any device signed in to the same Tailscale network can open it in a
browser: iPhone, iPad, Android, another Mac or a PC. Tell the user, step by step:

1. Install **Tailscale** on the device (App Store, Google Play, or tailscale.com/download), sign in
   with the **same account** as the Mac, and turn it on.
2. Open the browser (Safari on iPhone/iPad, Chrome on Android, any browser on a computer) and go to
   the `https://….ts.net` address from step 4.
3. Paste the token. To get it onto the device without showing it here, run:

   ```sh
   pbcopy < ~/.config/herdr-chat/token
   ```

   With Universal Clipboard (same Apple Account, Bluetooth and Wi‑Fi on) the user can paste it on an
   iPhone, iPad or other Mac right away. Otherwise tell the user to open the file themselves
   (`open -e ~/.config/herdr-chat/token`) and copy it over in a way they trust.
4. Optional, to use it like an app: on iPhone/iPad tap Share → **Add to Home Screen**; on Android,
   Chrome menu → **Add to Home screen** / **Install app**.
5. Optional: tap the bell icon for notifications. On iPhone/iPad this needs iOS 16.4+ and the
   home-screen app. It also needs step 6.

Ask the user to confirm they can see their terminals in the list.

## 6. Push notifications (optional)

Ask the user whether they want notifications. If yes, ask before installing:

```sh
cd ~/herdr-chat && python3 -m venv .venv && .venv/bin/pip install cryptography
```

Without it, everything else works and the bell shows that notifications are unavailable.

## 7. Run at login

Skip this step if step 3 found it already installed. Otherwise ask the user first. Then stop the
temporary server from step 3 and make sure the port is free:

```sh
kill "$(cat /tmp/herdr-chat.pid)" && rm /tmp/herdr-chat.pid
sleep 1
lsof -nP -iTCP:5393 -sTCP:LISTEN || echo "5393 is free"      # must say free before going on
```

Create `~/Library/LaunchAgents/com.herdr-chat.server.plist` with the real values filled in:

- `HOME_DIR`: output of `echo $HOME`
- `HC_DIR`: output of `dirname "$(command -v hc)"`
- `PYTHON`: `HOME_DIR/herdr-chat/.venv/bin/python` if you did step 6, else the output of
  `command -v python3`
- Keep the `HERDR_CHAT_MACHINE_LABELS` lines only if the user gave you names in step 3. Add
  `HERDR_CHAT_PUSH_CONTACT` (`mailto:` + their address) only if they want to give one.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.herdr-chat.server</string>
  <key>ProgramArguments</key>
  <array>
    <string>PYTHON</string><string>-B</string>
    <string>HOME_DIR/herdr-chat/server.py</string><string>--port</string><string>5393</string>
  </array>
  <key>WorkingDirectory</key><string>HOME_DIR/herdr-chat</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>HC_DIR:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>HERDR_CHAT_MACHINE_LABELS</key><string>local=iMac</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>HOME_DIR/Library/Logs/herdr-chat.log</string>
  <key>StandardErrorPath</key><string>HOME_DIR/Library/Logs/herdr-chat.log</string>
</dict>
</plist>
```

```sh
plutil -lint ~/Library/LaunchAgents/com.herdr-chat.server.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.herdr-chat.server.plist
sleep 2
launchctl print gui/$(id -u)/com.herdr-chat.server | grep -m1 'pid ='              # launchd's server
lsof -nP -iTCP:5393 -sTCP:LISTEN                                                  # same pid
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5393/api/terminals   # expect 401
```

After changing the code: `launchctl kickstart -k gui/$(id -u)/com.herdr-chat.server`.
After changing the plist (for example adding push later): run `launchctl bootout
gui/$(id -u)/com.herdr-chat.server`, edit the file, then `bootstrap` it again as above.

## Troubleshooting

| Symptom | Check |
|---|---|
| `curl` gives no answer | `tail -20 /tmp/herdr-chat.log` or `~/Library/Logs/herdr-chat.log`. Port taken: pick another port and use it everywhere. |
| "herdr 명령이 실패했습니다" (herdr command failed) | Herdr is not running, or `hc` is not on the server's `PATH`. Check `hc ls` in Terminal and `HC_DIR` in the plist. |
| Device cannot open the address | Tailscale is on on the device, same account, and `tailscale serve status` lists the address. |
| "토큰이 맞지 않습니다" (wrong token) | Copy the token again. It must be the whole line from the token file. |
| List is empty | `hc ls` on the Mac. Under launchd, `HC_DIR` in the plist `PATH` must be where `hc` lives. |
| An agent shows only its screen, not a chat | Normal for programs herdr-chat does not know, and briefly for new sessions. |
| Other Mac shows "unreachable" | That Mac is asleep or not connected in Herdr (`hc machine ls`). |

## Uninstall

```sh
launchctl bootout gui/$(id -u)/com.herdr-chat.server
rm ~/Library/LaunchAgents/com.herdr-chat.server.plist
tailscale serve --https=443 off      # or --https=8443 if you used that port: turns off what is served on that port
rm -rf ~/herdr-chat ~/.config/herdr-chat     # ask first: this deletes the token and push keys
```
