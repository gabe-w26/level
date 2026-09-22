# Level phone app — running it on this Mac

The app is Expo SDK 52 + expo-router (same versions as the Groundwork app). It is a
native app: every screen is React Native and talks to the Flask site's JSON API at
`/api/mobile` (see `../api_mobile.py`).

## 1. Node on this Mac

`node` and `npm` aren't on the PATH, and the `npm` shim that ships with the Logi
copy of Node is broken. Make a small wrapper folder once:

```bash
NODEDIR="$HOME/Library/Application Support/Logi/LogiPluginService/PluginHosts/node22/node"
mkdir -p ~/.level-node
printf '#!/bin/sh\nexec "%s/bin/node" "%s/lib/node_modules/npm/bin/npm-cli.js" "$@"\n' "$NODEDIR" "$NODEDIR" > ~/.level-node/npm
printf '#!/bin/sh\nexec "%s/bin/node" "%s/lib/node_modules/npm/bin/npx-cli.js" "$@"\n' "$NODEDIR" "$NODEDIR" > ~/.level-node/npx
chmod +x ~/.level-node/npm ~/.level-node/npx
ln -sf "$NODEDIR/bin/node" ~/.level-node/node
```

Then, in any Terminal window where you work on the app:

```bash
export PATH="$HOME/.level-node:$PATH"
```

## 2. Install

```bash
cd "/Users/gabrielwilliams/Downloads/Claude - PT Software/level-trades/mobile"
npm install --no-audit --no-fund
```

`postinstall` runs patch-package, which applies the two fixes in `patches/` for
the space in "Claude - PT Software" (only matters for native iOS builds).

Don't use `npx expo install <pkg>` — it fails here with `spawn ENOEXEC` (it
writes the version to package.json first, so run `npm install` after it).

## 3. Run it against the live site

```bash
npx expo start
```

Press `i` for the iOS Simulator (opens in Expo Go), or scan the QR code with
the Expo Go app on your phone.

## 4. Run it against the site on this Mac

```bash
# Terminal 1 — the Flask site, with demo data
cd "/Users/gabrielwilliams/Downloads/Claude - PT Software/level-trades"
python3 seed.py            # once: demo trades, customers and jobs
PORT=5077 python3 app.py   # add HOST=0.0.0.0 to reach it from a real phone

# Terminal 2 — the app, pointed at it
cd mobile
EXPO_PUBLIC_API_URL=http://localhost:5077 npx expo start
```

On a real phone use your Mac's Wi-Fi address instead of `localhost`
(e.g. `http://192.168.1.20:5077`, with `HOST=0.0.0.0` on the Flask side).

Demo logins after `seed.py`: `customer@level.local` / `demo1234` and
`trade@level.local` / `demo1234`. Admin accounts can't sign in to the app.

The API address lives in one place: `lib/config.ts` (default
`https://level-wcyc.onrender.com`, overridable with `EXPO_PUBLIC_API_URL` or
`extra.apiUrl` in `app.json`).

## 5. Check the code

```bash
npm run typecheck          # tsc --noEmit
```

Server tests (from `level-trades/`): `python3 -m unittest discover -s tests`.

## Push notifications

Push needs a real phone and an EAS project id (`eas init` fills in
`extra.eas.projectId`). In the Simulator and without a project id the app skips
registration quietly. The server sends pushes from the background sweep
(`push.flush`, every 2 minutes) for any notification created after a phone
registered; tapping one opens the matching screen (`lib/links.ts`).

## Where things are

| Path | What |
|---|---|
| `app/index.tsx` | Welcome — "I need a tradie" / "I'm a tradie" (no sign-in needed) |
| `app/login.tsx`, `signup.tsx`, `forgot.tsx` | Sign in, sign up (customer or trade), reset password |
| `app/(customer)/` | Customer tabs: My jobs, Post a job, Messages, Account |
| `app/(trade)/` | Trade tabs: Jobs (offers with live countdowns), My quotes, Messages, Account |
| `app/job/[id].tsx` | Customer job: quotes, share details, accept, decline, close, review |
| `app/offer/[id].tsx`, `quote/[id].tsx` | Trade job detail, send/edit quote, pass |
| `app/thread/[jobId]/[tradeId].tsx` | Message thread |
| `app/notifications.tsx`, `verify-phone.tsx`, `delete-account.tsx`, `review/[id].tsx` | The rest |
| `lib/api.ts` | Every API call and the data shapes |
| `lib/auth.tsx` | Token storage (Keychain via expo-secure-store) and the signed-in person |
| `lib/push.ts` | Push registration and tap routing |
| `lib/theme.ts` | Colours from the website's `static/style.css` |

## Native iOS build (a real app, not Expo Go)

Tested 2026-09-23: a Release build installs and runs on the iPhone Simulator against the live site.

```bash
export PATH="$HOME/.level-node:$HOME/.rbenv/shims:$PATH" LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8
cd mobile && CI=1 npx expo prebuild --platform ios --clean
```

After every `prebuild --clean`, fix one line in `ios/Level.xcodeproj/project.pbxproj`
(the space in "Claude - PT Software" breaks it): in the "Bundle React Native code and
images" script, the last line runs `` `"$NODE_BINARY" --print "...react-native-xcode.sh"` ``
in backticks — wrap it as `"$("$NODE_BINARY" --print "...react-native-xcode.sh")"`.
The other three path-with-spaces fixes are applied automatically from `patches/`.

```bash
cd ios && xcodebuild -workspace Level.xcworkspace -scheme Level -configuration Release \
  -destination "id=<simulator udid>" -derivedDataPath /tmp/level_dd build
xcrun simctl install booted /tmp/level_dd/Build/Products/Release-iphonesimulator/Level.app
```
