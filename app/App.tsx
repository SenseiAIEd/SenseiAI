// Sensei Cam: the student's side of Sensei Desk.
//
// The student picks a session length and taps Start. The app calls the Sensei gateway on
// the DGX Spark over WebRTC (live camera video + microphone), and Sensei leads the session:
// it greets, watches the notebook, asks guiding questions out loud, and wraps up at the end.
// The student can ask for a Hint, a Check of their work, a Repeat, or End early.
//
// Reaches the gateway directly on a LAN, or from anywhere through Tailscale Funnel: then the
// media goes through the TURN relay the gateway lists at /config.
//
// Data channel "sensei" (JSON):
//   gateway -> phone  {"type": "say", "text", "why"}     speak this now
//                     {"type": "hush"}                   stop speaking
//                     {"type": "tutor", "phase", "remaining_s", "thinking", ...}
//                     {"type": "session_ended", "summary", "hints_given", "mistakes_fixed", ...}
//   phone -> gateway  {"type": "hello", "app"}  {"type": "start", "minutes"}
//                     {"type": "request", "what": "hint" | "check" | "repeat" | "end"}
//                     {"type": "spoken", "text"}         finished speaking this
//
// Needs a custom build (not Expo Go): see README.md.
import { useEffect, useRef, useState } from "react";
import { PermissionsAndroid, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { MediaStream, RTCPeerConnection, RTCRtpSender, RTCView, mediaDevices } from "react-native-webrtc";
import * as Speech from "expo-speech";
import { useKeepAwake } from "expo-keep-awake";
import { StatusBar } from "expo-status-bar";
import * as SecureStore from "expo-secure-store";
import { SafeAreaProvider, useSafeAreaInsets } from "react-native-safe-area-context";

const APP_ID = "sensei-cam/0.3.1";
const DEFAULT_SERVER = "https://spark-e257.tail803c7f.ts.net:8443";
const LENGTHS = [5, 10, 15];
const REQUEST_TIMEOUT_MS = 10000;
const ICE_GATHER_DIRECT_MS = 3000;
const ICE_GATHER_RELAY_MS = 8000; // a TURN allocation through Funnel crosses the internet
const TUTOR_ANSWER_MS = 8000; // after Start, the Spark's tutor should greet within this
const NO_TUTOR = "Sensei's brain on the Spark isn't answering. Ask your teacher to update and restart the Sensei gateway.";

type Screen = "home" | "connecting" | "session" | "summary";
type Request = "hint" | "check" | "repeat" | "end";
type IceServer = { urls: string | string[]; username?: string; credential?: string };
type TutorState = { phase: string; remaining_s: number; thinking: boolean };
type Summary = { summary: string; hints_given: number; mistakes_fixed: number; problems_finished: number; minutes: number };
type GatewayMessage =
  | { type: "say"; text: string; why?: string }
  | { type: "hush" }
  | ({ type: "tutor" } & TutorState)
  | ({ type: "session_ended" } & Summary);

async function askPermissions() {
  if (Platform.OS !== "android") return true;
  const res = await PermissionsAndroid.requestMultiple([
    PermissionsAndroid.PERMISSIONS.CAMERA,
    PermissionsAndroid.PERMISSIONS.RECORD_AUDIO,
  ]);
  return Object.values(res).every((r) => r === PermissionsAndroid.RESULTS.GRANTED);
}

// The gateway gets our offer in one HTTP request (no trickle ICE), so wait until
// all local candidates are in the SDP. On a LAN this takes well under a second.
function waitForIceGathering(pc: RTCPeerConnection, timeoutMs: number) {
  return new Promise<void>((resolve) => {
    if (pc.iceGatheringState === "complete") return resolve();
    const timer = setTimeout(resolve, timeoutMs);
    pc.onicegatheringstatechange = () => {
      if (pc.iceGatheringState === "complete") {
        clearTimeout(timer);
        resolve();
      }
    };
  });
}

// When bandwidth is short, drop frame rate rather than resolution: the tutor needs
// sharp handwriting far more than smooth motion.
async function preferSharpVideo(sender: RTCRtpSender | undefined) {
  if (!sender) return;
  try {
    const params = sender.getParameters();
    params.degradationPreference = "maintain-resolution";
    params.encodings.forEach((e) => (e.maxBitrate = 2_500_000));
    await sender.setParameters(params);
  } catch {
    // keep WebRTC's defaults
  }
}

// JSON request to the gateway, with the access key and a timeout.
async function gateway(url: string, key: string, body?: unknown) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      method: body === undefined ? "GET" : "POST",
      headers: { "Content-Type": "application/json", ...(key ? { "X-Sensei-Key": key } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
    if (res.status === 401) throw new Error("wrong or missing access key");
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    return await res.json();
  } catch (e) {
    if (controller.signal.aborted) throw new Error("no answer (is the gateway running and reachable?)");
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

// Settings survive app restarts (the key in the Android keystore).
const saved = {
  load: async () => ({
    server: (await SecureStore.getItemAsync("server")) ?? DEFAULT_SERVER,
    key: (await SecureStore.getItemAsync("key")) ?? "",
    minutes: Number((await SecureStore.getItemAsync("minutes")) ?? 10) || 10,
  }),
  save: (server: string, key: string, minutes: number) =>
    Promise.all([
      SecureStore.setItemAsync("server", server),
      SecureStore.setItemAsync("key", key),
      SecureStore.setItemAsync("minutes", String(minutes)),
    ]).catch(() => {}),
};

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

export default function App() {
  return (
    <SafeAreaProvider>
      <SenseiApp />
    </SafeAreaProvider>
  );
}

function SenseiApp() {
  useKeepAwake();
  const insets = useSafeAreaInsets();
  // Edge-to-edge: keep content clear of the status bar and the navigation buttons.
  const rootStyle = [styles.root, { paddingTop: insets.top, paddingBottom: insets.bottom }];
  const [server, setServer] = useState(DEFAULT_SERVER);
  const [key, setKey] = useState("");
  const [minutes, setMinutes] = useState(10);
  const [showSettings, setShowSettings] = useState(false);
  const [screen, setScreen] = useState<Screen>("home");
  const [status, setStatus] = useState("");
  const [said, setSaid] = useState<string | null>(null);
  const [tutor, setTutor] = useState<TutorState | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [lost, setLost] = useState(false);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const channelRef = useRef<ReturnType<RTCPeerConnection["createDataChannel"]> | null>(null);
  const tutorAnswered = useRef(false);
  const watchdog = useRef<ReturnType<typeof setTimeout> | null>(null);

  function speak(text: string, onDone?: () => void) {
    setSaid(text);
    Speech.stop();
    Speech.speak(text, { rate: 0.95, onDone });
  }

  useEffect(() => {
    saved.load().then((v) => {
      setServer(v.server);
      setKey(v.key);
      setMinutes(v.minutes);
      if (!v.key) setShowSettings(true);
    }).catch(() => {});
    return () => hangUp();
  }, []);

  function hangUp() {
    if (watchdog.current) clearTimeout(watchdog.current);
    watchdog.current = null;
    channelRef.current = null;
    pcRef.current?.close();
    pcRef.current = null;
    setStream((s) => {
      s?.getTracks().forEach((t) => t.stop());
      return null;
    });
  }

  function send(msg: object) {
    const ch = channelRef.current;
    if (ch && ch.readyState === "open") ch.send(JSON.stringify(msg));
  }

  function onMessage(msg: GatewayMessage) {
    if (msg.type === "say") {
      const { text, why } = msg;
      if (why) tutorAnswered.current = true;
      speak(text, () => {
        send({ type: "spoken", text });
        if (why === "wrap_up") hangUp(); // the goodbye was the last thing: stop recording
      });
    } else if (msg.type === "hush") {
      Speech.stop();
    } else if (msg.type === "tutor") {
      tutorAnswered.current = true;
      setTutor({ phase: msg.phase, remaining_s: msg.remaining_s, thinking: msg.thinking });
    } else if (msg.type === "session_ended") {
      setSummary(msg);
      setScreen("summary");
    }
  }

  async function start() {
    const base = server.trim().replace(/\/+$/, "");
    const accessKey = key.trim();
    Speech.stop();
    hangUp(); // e.g. "Start again" while the last goodbye is still playing
    setScreen("connecting");
    setShowSettings(false);
    setSaid(null);
    setSummary(null);
    setTutor(null);
    setLost(false);
    setStatus("Waking up Sensei…");
    try {
      // Before touching the camera: is the gateway there, and does it offer a relay?
      const config = (await gateway(`${base}/config`, accessKey)) as {
        iceServers: IceServer[];
        tutor?: { brain: string | null };
      };
      const iceServers = config.iceServers;
      if (!config.tutor) {
        throw new Error("the Sensei gateway on the Spark is an old version without the tutor. Update it (git pull) and restart it.");
      }
      saved.save(base, accessKey, minutes);

      setStatus("Starting the camera…");
      if (!(await askPermissions())) throw new Error("Sensei needs the camera and microphone.");
      const local = await mediaDevices.getUserMedia({
        audio: true,
        video: { facingMode: "environment", width: 1280, height: 720, frameRate: 15 },
      });
      setStream(local);

      // No STUN: direct on a LAN, otherwise through the gateway's TURN relay.
      const pc = new RTCPeerConnection({ iceServers });
      pcRef.current = pc;
      const senders = local.getTracks().map((t) => pc.addTrack(t, local));
      const videoSender = senders.find((s) => s.track?.kind === "video");

      const channel = pc.createDataChannel("sensei");
      channelRef.current = channel;
      channel.onopen = () => {
        channel.send(JSON.stringify({ type: "hello", app: APP_ID }));
        channel.send(JSON.stringify({ type: "start", minutes }));
        setScreen("session");
        // An old gateway (or one without the tutor) ignores "start": say so instead of staying silent.
        tutorAnswered.current = false;
        watchdog.current = setTimeout(() => {
          if (!tutorAnswered.current && channelRef.current === channel) speak(NO_TUTOR);
        }, TUTOR_ANSWER_MS);
      };
      channel.onmessage = (e: unknown) => {
        try {
          onMessage(JSON.parse(String((e as { data: unknown }).data)));
        } catch {
          // ignore malformed messages
        }
      };

      pc.onconnectionstatechange = () => {
        if (pcRef.current !== pc) return; // an old call closing
        const s = pc.connectionState;
        if (s === "connected") setLost(false);
        else if (s === "disconnected" || s === "failed") setLost(true);
      };

      setStatus("Connecting to Sensei…");
      const offer = await pc.createOffer({});
      await pc.setLocalDescription(offer);
      await waitForIceGathering(pc, iceServers.length > 0 ? ICE_GATHER_RELAY_MS : ICE_GATHER_DIRECT_MS);
      const answer = await gateway(`${base}/offer`, accessKey, pc.localDescription);
      await pc.setRemoteDescription(answer);
      await preferSharpVideo(videoSender);
    } catch (e) {
      hangUp();
      setScreen("home");
      setStatus(`Couldn't reach Sensei: ${e instanceof Error ? e.message : e}`);
    }
  }

  function press(what: Request) {
    if (what === "repeat" && said) {
      // Repeat locally: instant, and works even if the connection hiccups.
      speak(said);
      return;
    }
    send({ type: "request", what });
    if (what === "end" && !(channelRef.current?.readyState === "open")) {
      hangUp();
      setScreen("home");
    }
  }

  function leave() {
    Speech.stop();
    hangUp();
    setScreen("home");
    setStatus("");
  }

  // --- screens ---------------------------------------------------------------------------
  const camera = stream ? (
    <RTCView streamURL={stream.toURL()} style={styles.camera} objectFit="cover" />
  ) : (
    <View style={[styles.camera, styles.center]}>
      <Text style={styles.brand}>Sensei</Text>
      <Text style={styles.muted}>Your tutor that watches your notebook and asks you questions.</Text>
    </View>
  );

  if (screen === "summary" && summary) {
    return (
      <View style={rootStyle}>
        <StatusBar style="light" />
        <ScrollView contentContainerStyle={styles.summary}>
          <Text style={styles.heading}>Session complete</Text>
          <Text style={styles.said}>{summary.summary}</Text>
          <View style={styles.stats}>
            <Stat label="minutes" value={summary.minutes} />
            <Stat label="hints" value={summary.hints_given} />
            <Stat label="fixes" value={summary.mistakes_fixed} />
            <Stat label="solved" value={summary.problems_finished} />
          </View>
          <Pressable style={styles.primary} onPress={start}>
            <Text style={styles.primaryText}>Start again</Text>
          </Pressable>
          <Pressable style={styles.secondary} onPress={leave}>
            <Text style={styles.secondaryText}>Done</Text>
          </Pressable>
        </ScrollView>
      </View>
    );
  }

  if (screen === "session" || screen === "connecting") {
    const thinking = tutor?.thinking;
    const line = lost
      ? "Connection lost. Check the network; Sensei will pick up when it's back."
      : screen === "connecting" ? status : thinking ? "Sensei is looking at your page…" : "Sensei is watching";
    return (
      <View style={rootStyle}>
        <StatusBar style="light" />
        {camera}
        <View style={styles.panel}>
          <Text style={styles.said}>{said ?? "Hi! Getting ready…"}</Text>
          <View style={styles.statusRow}>
            <View style={[styles.dot, !lost && screen === "session" && styles.dotLive, lost && styles.dotLost]} />
            <Text style={styles.status}>{line}</Text>
            {tutor && <Text style={styles.timer}>{clock(tutor.remaining_s)}</Text>}
          </View>
          <View style={styles.buttons}>
            <Button label="Hint" onPress={() => press("hint")} disabled={screen !== "session"} />
            <Button label="Check my work" onPress={() => press("check")} disabled={screen !== "session"} />
            <Button label="Repeat" onPress={() => press("repeat")} disabled={!said} />
          </View>
          <Pressable style={styles.secondary} onPress={() => (screen === "session" ? press("end") : leave())}>
            <Text style={styles.secondaryText}>{screen === "session" ? "End session" : "Cancel"}</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  // home
  return (
    <View style={rootStyle}>
      <StatusBar style="light" />
      {camera}
      <View style={styles.panel}>
        <Text style={styles.label}>How long do you want to study?</Text>
        <View style={styles.buttons}>
          {LENGTHS.map((m) => (
            <Pressable key={m} style={[styles.chip, minutes === m && styles.chipOn]} onPress={() => setMinutes(m)}>
              <Text style={[styles.chipText, minutes === m && styles.chipTextOn]}>{m} min</Text>
            </Pressable>
          ))}
        </View>
        <Pressable style={styles.primary} onPress={start}>
          <Text style={styles.primaryText}>Start with Sensei</Text>
        </Pressable>
        {!!status && <Text style={styles.error}>{status}</Text>}
        <View style={styles.links}>
          <Pressable onPress={() => setShowSettings((v) => !v)}>
            <Text style={styles.link}>{showSettings ? "Hide settings" : "Settings"}</Text>
          </Pressable>
          <Pressable onPress={() => speak("Hi, I'm Sensei. If you can hear me, your sound is working.")}>
            <Text style={styles.link}>Test voice</Text>
          </Pressable>
        </View>
        {showSettings && (
          <>
            <TextInput
              style={styles.input}
              value={server}
              onChangeText={setServer}
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
              placeholder="https://<spark>.ts.net:8443 or http://<spark-ip>:8787"
              placeholderTextColor="#6F8580"
            />
            <TextInput
              style={styles.input}
              value={key}
              onChangeText={setKey}
              autoCapitalize="none"
              autoCorrect={false}
              secureTextEntry
              placeholder="Access key (from the Spark's sensei.env)"
              placeholderTextColor="#6F8580"
            />
          </>
        )}
      </View>
    </View>
  );
}

function Button({ label, onPress, disabled }: { label: string; onPress: () => void; disabled?: boolean }) {
  return (
    <Pressable style={[styles.button, disabled && styles.disabled]} onPress={onPress} disabled={disabled}>
      <Text style={styles.buttonText}>{label}</Text>
    </Pressable>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.statValue}>{value}</Text>
      <Text style={styles.muted}>{label}</Text>
    </View>
  );
}

const SLATE = "#17252A";
const PANEL = "#1F3238";
const CHALK = "#EEF1EC";
const MUTED = "#9FB3AE";
const PENCIL = "#F2C14E";
const RED = "#E07A5F";
const LINE = "#34484E";

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: SLATE },
  center: { justifyContent: "center", alignItems: "center", padding: 24, gap: 8 },
  camera: { flex: 1, backgroundColor: "#0E181B" },
  brand: { color: PENCIL, fontSize: 40, fontWeight: "700" },
  panel: { padding: 20, paddingBottom: 32, gap: 14, backgroundColor: SLATE },
  said: { color: CHALK, fontSize: 22, lineHeight: 30 },
  heading: { color: PENCIL, fontSize: 26, fontWeight: "700" },
  label: { color: MUTED, fontSize: 15 },
  statusRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  dot: { width: 10, height: 10, borderRadius: 5, backgroundColor: MUTED },
  dotLive: { backgroundColor: PENCIL },
  dotLost: { backgroundColor: RED },
  status: { color: MUTED, fontSize: 14, flex: 1 },
  timer: { color: CHALK, fontSize: 16, fontVariant: ["tabular-nums"] },
  muted: { color: MUTED, fontSize: 14, textAlign: "center" },
  error: { color: RED, fontSize: 14 },
  link: { color: MUTED, fontSize: 14, textDecorationLine: "underline" },
  links: { flexDirection: "row", justifyContent: "space-between" },
  buttons: { flexDirection: "row", gap: 10, flexWrap: "wrap" },
  button: { flexGrow: 1, borderColor: LINE, borderWidth: 1, borderRadius: 8, paddingVertical: 12, paddingHorizontal: 10, alignItems: "center", backgroundColor: PANEL },
  buttonText: { color: CHALK, fontSize: 15, fontWeight: "600" },
  disabled: { opacity: 0.4 },
  chip: { flexGrow: 1, borderColor: LINE, borderWidth: 1, borderRadius: 20, paddingVertical: 10, alignItems: "center" },
  chipOn: { borderColor: PENCIL, backgroundColor: PENCIL },
  chipText: { color: CHALK, fontSize: 15 },
  chipTextOn: { color: SLATE, fontWeight: "700" },
  input: {
    color: CHALK, borderColor: LINE, borderWidth: 1, borderRadius: 8,
    paddingHorizontal: 12, paddingVertical: 10, fontSize: 15,
  },
  primary: { backgroundColor: PENCIL, borderRadius: 8, paddingVertical: 16, alignItems: "center" },
  primaryText: { color: SLATE, fontSize: 17, fontWeight: "700" },
  secondary: { borderColor: PENCIL, borderWidth: 1, borderRadius: 8, paddingVertical: 14, alignItems: "center" },
  secondaryText: { color: PENCIL, fontSize: 16, fontWeight: "600" },
  summary: { padding: 24, paddingTop: 40, gap: 18 },
  stats: { flexDirection: "row", justifyContent: "space-between" },
  stat: { alignItems: "center", flex: 1 },
  statValue: { color: CHALK, fontSize: 28, fontWeight: "700" },
});
