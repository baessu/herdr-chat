# Security

herdr-chat is a remote control for your terminals: anyone who can use it can type into your coding
agents, and through them run commands on your Mac. Set it up with that in mind.

## How it is meant to be used

- The server listens on `127.0.0.1` only. It is reached from your phone through `tailscale serve`,
  which keeps it inside your own Tailscale network.
- Every API request needs the access token in `~/.config/herdr-chat/token` (created with mode 600).
- **Do not** put it on the public internet: no `tailscale funnel`, no router port forwarding, no
  public tunnels or reverse proxies.
- Do not share your Tailscale network (tailnet) with people you would not hand your Mac to.

## What the app limits

- Plain shells (`zsh`, `bash`, `sh`, `fish`) and Herdr's own terminals cannot receive text, keys or
  interrupts from the app.
- Only a fixed list of keys can be sent. Text is limited to 8,000 characters per message.
- `hc` is always called with fixed arguments and never through a shell.
- Agent history files and databases are opened read-only.
- Files and documents are opened only inside your home folder (`HERDR_CHAT_FILES_ROOT`), with links
  re-checked when opened. A program already running on your Mac with write access to that folder
  could still race these checks; this is not reachable from the network. HTML and SVG files are
  shown in a sandbox that cannot read the token or call the API.
- Your everyday Chrome is reachable only if you turned on `chrome://inspect/#remote-debugging`, and
  Chrome asks you to **Allow** the connection. The app attaches only to the tab you pick and never
  copies cookies or profiles. Those tabs are logged in as you: turn remote debugging off when you do
  not need it.
- Push notifications contain the machine, location and title, never the conversation, and go only
  to Apple, Google and Mozilla push servers.
- `logs/audit.jsonl` records time, terminal, kind and length of input sent to terminals, never the
  text. Attachment file names are recorded.
- The app is not allowed to be embedded in other sites' frames.
- Your browser keeps the token and recently viewed conversations in its site storage. On a shared or
  lost device, clear the site's data (Safari: Settings → Apps → Safari → Advanced → Website Data).

## If your token leaks

Delete the token file and the push subscriptions, then restart the server. A new token is created,
every device has to enter it again, and devices turn notifications on again from the bell.

```sh
rm ~/.config/herdr-chat/token ~/.config/herdr-chat/push.json
launchctl kickstart -k gui/$(id -u)/com.herdr-chat.server   # or restart server.py by hand
```

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private vulnerability reporting
("Security" tab → "Report a vulnerability") on this repository.
