# Reaching herdr-chat from your phone with Tailscale

[한국어](TAILSCALE.ko.md)

herdr-chat only listens on your Mac itself (`127.0.0.1`). Tailscale builds a private network
between your own devices, so your phone can reach the Mac from anywhere (home Wi‑Fi, office, mobile
data) while nobody else on the internet can. The free Personal plan is enough.

## 1. Install Tailscale on the Mac

1. Download it from https://tailscale.com/download (or the Mac App Store) and open it.
2. Sign in. Any of the offered accounts works (Google, Apple, GitHub, Microsoft…). **Remember which
   one**: the phone must use the same.
3. Allow the VPN configuration when macOS asks.
4. The Tailscale icon in the menu bar should show the Mac as connected.

The `tailscale` command is needed for step 3. Check with `tailscale version`. If it is missing:

- In the Tailscale menu bar app, open Settings and choose to install the CLI, or
- use the full path: `/Applications/Tailscale.app/Contents/MacOS/Tailscale` in place of `tailscale`.

## 2. Install Tailscale on the iPhone or iPad

1. Install **Tailscale** from the App Store.
2. Sign in with the **same account** as the Mac.
3. Allow the VPN configuration and turn Tailscale on.
4. The Mac should appear in the app's device list.

## 3. Give herdr-chat an https address inside your network

On the Mac, first look at what it already shares:

```sh
tailscale serve status
```

If that shows nothing, share herdr-chat on the default address:

```sh
tailscale serve --bg 5393
tailscale serve status
```

If something is already shared there, leave it alone and use a port that `status` does not list yet, for example:
`tailscale serve --bg --https=8443 5393`. The address then ends in `:8443`.

`status` shows an address like `https://my-mac.tail1234.ts.net` pointing to
`http://127.0.0.1:5393`. That address works only on devices signed in to your Tailscale network.

- If `tailscale serve` says HTTPS is not enabled, it prints a link. Open it and allow HTTPS
  certificates (this also needs MagicDNS, which is on by default; both are under **DNS** at
  https://login.tailscale.com/admin/dns). Then run the command again.
- `--bg` keeps the setting after you close the terminal and after restarts.
- Use another port number if you started herdr-chat on another port.

> **Do not use `tailscale funnel`.** Funnel publishes the address to the whole internet. Anyone who
> got your token could then type into your terminals.

## 4. Open it on a phone, tablet or computer

Every device signed in to your Tailscale network can use it: iPhone, iPad, Android, another Mac or a
PC.

1. With Tailscale on, open the `https://….ts.net` address in the browser.
2. Paste the token from `~/.config/herdr-chat/token`.
3. Optional: iPhone/iPad Share → **Add to Home Screen**, Android Chrome → **Install app**. On
   iPhone/iPad, notifications only work from the home-screen app.

## Turning it off

```sh
tailscale serve --https=443 off     # or --https=8443 if you used that port
tailscale serve status              # check it is gone
```

This turns off what is served on that https port only; other ports stay. Avoid `tailscale serve
reset`: it removes everything this Mac shares with Tailscale.

## Notes

- The Mac must be awake. For a Mac that serves all day, turn on
  System Settings → Energy → "Prevent automatic sleeping when the display is off" (wording varies by
  macOS version), or keep it plugged in with sleep disabled.
- If Tailscale on the phone is off, the address will not load. Turn it on and reload.
