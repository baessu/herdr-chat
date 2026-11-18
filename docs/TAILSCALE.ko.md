# Tailscale 로 휴대폰에서 herdr-chat 열기

[English](TAILSCALE.md)

herdr-chat 서버는 Mac 자기 자신(`127.0.0.1`)에서만 요청을 받습니다. Tailscale 은 내 기기끼리만
통하는 비공개 네트워크를 만들어 줍니다. 그래서 집 와이파이, 회사, 휴대폰 데이터 어디서든 내
휴대폰은 Mac 에 들어올 수 있고, 인터넷의 다른 사람은 들어올 수 없습니다. 무료 개인 요금제로
충분합니다.

## 1. Mac 에 Tailscale 설치

1. https://tailscale.com/download (또는 Mac App Store) 에서 받아 엽니다.
2. 로그인합니다. Google, Apple, GitHub, Microsoft 등 어느 계정이든 됩니다. **어느 계정으로
   로그인했는지 기억해 두세요.** 휴대폰도 같은 계정이어야 합니다.
3. macOS 가 VPN 구성을 허용할지 물으면 허용합니다.
4. 메뉴 막대의 Tailscale 아이콘에서 이 Mac 이 연결됨으로 보이면 됩니다.

3단계에는 `tailscale` 명령이 필요합니다. 터미널에서 `tailscale version` 으로 확인합니다. 없으면:

- 메뉴 막대 Tailscale 앱의 설정에서 명령줄 도구(CLI) 설치를 고르거나,
- `tailscale` 대신 전체 경로 `/Applications/Tailscale.app/Contents/MacOS/Tailscale` 를 씁니다.

## 2. iPhone·iPad 에 Tailscale 설치

1. App Store 에서 **Tailscale** 을 설치합니다.
2. Mac 과 **같은 계정**으로 로그인합니다.
3. VPN 구성을 허용하고 Tailscale 을 켭니다.
4. 앱의 기기 목록에 Mac 이 보이면 됩니다.

## 3. 내 네트워크 안에서만 열리는 https 주소 만들기

Mac 터미널에서 먼저 이미 열어 둔 것이 있는지 봅니다:

```sh
tailscale serve status
```

아무것도 없으면 기본 주소로 엽니다:

```sh
tailscale serve --bg 5393
tailscale serve status
```

이미 다른 것이 열려 있으면 그대로 두고 `status` 에 아직 없는 포트로 엽니다. 예: `tailscale serve --bg --https=8443 5393`.
이때 주소 끝에 `:8443` 이 붙습니다.

`status` 에 `https://my-mac.tail1234.ts.net` → `http://127.0.0.1:5393` 같은 줄이 나옵니다. 이
주소는 내 Tailscale 네트워크에 로그인한 기기에서만 열립니다.

- `tailscale serve` 가 HTTPS 가 꺼져 있다며 링크를 보여 주면, 그 링크를 열어 HTTPS 인증서를
  허용합니다. MagicDNS 도 켜져 있어야 하는데 처음부터 켜져 있습니다. 둘 다
  https://login.tailscale.com/admin/dns 의 **DNS** 화면에 있습니다. 그런 다음 명령을 다시
  실행합니다.
- `--bg` 를 붙이면 터미널을 닫거나 Mac 을 다시 켜도 설정이 남습니다.
- herdr-chat 을 다른 포트로 켰다면 5393 대신 그 번호를 씁니다.

> **`tailscale funnel` 은 쓰지 마세요.** funnel 은 주소를 인터넷 전체에 공개합니다. 토큰을 손에
> 넣은 사람은 누구나 내 터미널에 글을 칠 수 있게 됩니다.

## 4. 휴대폰·태블릿·다른 컴퓨터에서 열기

같은 Tailscale 네트워크에 로그인한 기기라면 어디서든 씁니다. iPhone, iPad, Android, 다른 Mac,
PC 모두 됩니다.

1. Tailscale 을 켠 상태로 브라우저에서 `https://….ts.net` 주소를 엽니다.
2. `~/.config/herdr-chat/token` 의 토큰을 붙여 넣습니다.
3. 선택: iPhone·iPad 는 공유 버튼 → **홈 화면에 추가**, Android 는 Chrome 메뉴 → **앱 설치**.
   iPhone·iPad 에서 알림은 홈 화면 앱으로 열 때만 옵니다.

## 끄기

```sh
tailscale serve --https=443 off     # 8443 으로 열었다면 --https=8443
tailscale serve status              # 사라졌는지 확인
```

이 명령은 그 https 포트로 열어 둔 것만 끄고 다른 포트는 그대로 둡니다. `tailscale serve reset` 은 이 Mac 에서 Tailscale 로 열어 둔
것을 모두 지우니 쓰지 않습니다.

## 참고

- Mac 이 잠자고 있으면 열리지 않습니다. 하루 종일 켜 둘 Mac 이라면 시스템 설정 → 에너지에서
  "디스플레이가 꺼져 있을 때 자동으로 잠자기 방지"를 켭니다(macOS 버전마다 이름이 조금 다릅니다).
- 휴대폰의 Tailscale 이 꺼져 있으면 주소가 열리지 않습니다. 켜고 새로고침합니다.
