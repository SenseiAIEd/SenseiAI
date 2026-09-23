# Sensei Cam (Android app)

The phone on the Sensei Desk head. It streams the notebook (camera video and the
student's voice) to the Sensei gateway on the DGX Spark over WebRTC, and speaks aloud
every instruction the gateway sends back.

React Native with Expo (SDK 57). Android is the hackathon target; every library used
also supports iOS, so an iOS build later is a config-and-build job, not a rewrite.

## Install on the Pixel 2 XL

The app uses native WebRTC, so it **does not run in Expo Go**. Build an APK once and
install it; after that it runs on its own, with no dev server.

You need Node 20+, JDK 17 or 21, and the Android SDK (easiest: install Android Studio,
then set `ANDROID_HOME`, e.g. `~/Library/Android/sdk` on macOS or `~/Android/Sdk` on Linux).

```sh
cd app
npm install
npx expo prebuild --platform android          # generates android/ from app.json
cd android
./gradlew assembleRelease -PreactNativeArchitectures=arm64-v8a
# -> android/app/build/outputs/apk/release/app-release.apk
```

Install it: plug in the phone with USB debugging on and run
`adb install -r app/build/outputs/apk/release/app-release.apk`, or copy the APK to the
phone and open it (allow "install unknown apps" for your file manager).

The release build is signed with the debug key, which is fine for sideloading. Re-run
`npx expo prebuild --platform android --clean` after changing `app.json` or adding a
native package.

## Use it

1. Start the gateway on the Spark (see `../gateway/README.md`).
2. Open Sensei Cam, enter the server and access key, tap **Connect**, allow camera and microphone.
   Both are remembered (the key in the Android keystore).
   - From anywhere, through Tailscale Funnel (default): `https://spark-e257.tail803c7f.ts.net:8443`
     plus the key from the Spark's `sensei.env`. The phone needs no Tailscale app.
   - Same Wi-Fi or hotspot, no internet: `http://<spark-LAN-ip>:8787` (key only if the gateway has one).
3. Point the camera at the notebook. Instructions typed in the gateway console are spoken by the phone.

## How it talks to the gateway

- `GET /config` -> ICE servers: the gateway's TURN relay with short-lived credentials, or none.
- `POST /offer` with the WebRTC offer (all ICE candidates included) -> the answer.
- Every request carries the access key as `X-Sensei-Key`.
- Media: back camera at 1280x720, 15 fps, plus the microphone.
- Data channel `sensei`, JSON messages:
  - gateway -> phone: `{"type": "say", "text": "..."}`, `{"type": "hush"}`
  - phone -> gateway: `{"type": "hello", "app": "..."}`, `{"type": "spoken", "text": "..."}` when speech finishes

`usesCleartextTraffic` is enabled in `app.json` because in the LAN mode the gateway is plain
HTTP; through Funnel everything is HTTPS/TLS.

## Develop

```sh
npx tsc --noEmit                  # typecheck
npx expo run:android              # debug build on a USB-connected phone, with fast refresh
```
