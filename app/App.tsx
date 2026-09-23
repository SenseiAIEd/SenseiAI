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
//                     {"type": "heard", "text"}          what Sensei understood the student said
//   phone -> gateway  {"type": "hello", "app"}  {"type": "start", "minutes"}
//                     {"type": "request", "what": "hint" | "check" | "look" | "repeat" | "pause" | "resume" | "end"}
//                     {"type": "voice", "on"}            voice mode (the mic is muted when off)
//                     {"type": "spoken", "text"}         finished speaking this
//
// Needs a custom build (not Expo Go): see README.md.
import React, { useEffect, useRef, useState } from "react";
import { PermissionsAndroid, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { MediaStream, RTCPeerConnection, RTCRtpSender, RTCView, mediaDevices } from "react-native-webrtc";
import * as Speech from "expo-speech";
import { useKeepAwake } from "expo-keep-awake";
import { StatusBar } from "expo-status-bar";
import * as SecureStore from "expo-secure-store";
import { SafeAreaProvider, useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";

type IconName = React.ComponentProps<typeof Ionicons>["name"];

const APP_ID = "sensei-cam/0.6.0";
const DEFAULT_SERVER = "https://spark-e257.tail803c7f.ts.net:8443";
const LENGTHS = [5, 10, 15];
const REQUEST_TIMEOUT_MS = 10000;
const ICE_GATHER_DIRECT_MS = 3000;
const ICE_GATHER_RELAY_MS = 8000; // a TURN allocation through Funnel crosses the internet
const TUTOR_ANSWER_MS = 8000; // after Start, the Spark's tutor should greet within this
const OLD_GATEWAY = "The Sensei gateway on the Spark needs an update for this button. Ask your teacher to update and restart it.";
const NO_TUTOR = "Sensei's brain on the Spark isn't answering. Ask your teacher to update and restart the Sensei gateway.";

type Screen = "home" | "connecting" | "session" | "summary";
type Request = "hint" | "check" | "look" | "repeat" | "pause" | "resume" | "end";
type IceServer = { urls: string | string[]; username?: string; credential?: string };
type TutorState = { phase: string; remaining_s: number; thinking: boolean };
type Summary = { summary: string; hints_given: number; mistakes_fixed: number; problems_finished: number; minutes: number };
type GatewayMessage =
  | { type: "say"; text: string; why?: string }
  | { type: "hush" }
  | ({ type: "tutor" } & TutorState)
  | ({ type: "session_ended" } & Summary)
  | { type: "heard"; text: string };

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
  const micRef = useRef<ReturnType<MediaStream["getAudioTracks"]>[number] | null>(null);
  const [voice, setVoice] = useState(false); // voice mode: the mic only carries sound when on
  const voiceRef = useRef(false);
  const [heard, setHeard] = useState<string | null>(null);
  const [showControls, setShowControls] = useState(true);
  const pausedRef = useRef(false);
  const featuresRef = useRef<string[]>([]); // what this gateway supports (from /config)
  const [showText, setShowText] = useState(true); // Sensei's words over the camera
  const watchdog = useRef<ReturnType<typeof setTimeout> | null>(null);

  // The mic sends sound only in voice mode, and never while Sensei is talking
  // (so Sensei doesn't hear and answer itself).
  function setMic(open: boolean) {
    if (micRef.current) micRef.current.enabled = open;
  }

  function speak(text: string, onDone?: () => void) {
    setSaid(text);
    Speech.stop();
    setMic(false);
    const after = () => setMic(voiceRef.current && !pausedRef.current);
    Speech.speak(text, {
      rate: 0.95,
      onDone: () => {
        after();
        onDone?.();
      },
      onStopped: after,
      onError: after,
    });
  }

  function toggleVoice() {
    const on = !voiceRef.current;
    voiceRef.current = on;
    setVoice(on);
    setMic(on && !pausedRef.current);
    send({ type: "voice", on });
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
      if (msg.phase === "paused") setMic(false); // paused: Sensei neither looks nor listens
      pausedRef.current = msg.phase === "paused";
      setTutor({ phase: msg.phase, remaining_s: msg.remaining_s, thinking: msg.thinking });
    } else if (msg.type === "heard") {
      setHeard(msg.text);
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
        tutor?: { brain: string | null; features?: string[] };
      };
      featuresRef.current = config.tutor?.features ?? [];
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
      // Voice mode starts on: students talk to Sensei. The mic stays muted until the call is up,
      // and whenever Sensei is speaking. One tap turns it off.
      micRef.current = local.getAudioTracks()[0] ?? null;
      voiceRef.current = true;
      pausedRef.current = false;
      setVoice(true);
      setHeard(null);
      setMic(false);

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
        channel.send(JSON.stringify({ type: "voice", on: voiceRef.current }));
        setMic(voiceRef.current);
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
    const needs: Partial<Record<Request, string>> = { pause: "pause", resume: "pause", look: "look" };
    const feature = needs[what];
    if (feature && !featuresRef.current.includes(feature)) {
      speak(OLD_GATEWAY);
      return;
    }
    if (what === "pause" || what === "resume") {
      // Act on the phone at once; the Spark confirms with its next state update.
      const paused = what === "pause";
      pausedRef.current = paused;
      setTutor((t) => (t ? { ...t, phase: paused ? "paused" : "watching" } : t));
      setMic(!paused && voiceRef.current);
    }
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
    <RTCView streamURL={stream.toURL()} style={StyleSheet.absoluteFill} objectFit="cover" />
  ) : (
    <View style={[StyleSheet.absoluteFill, styles.center, styles.cameraOff]}>
      <Ionicons name="school" size={56} color={PENCIL} />
      <Text style={styles.brand}>Sensei</Text>
      <Text style={styles.muted}>Your tutor that watches your notebook, listens, and asks you questions.</Text>
    </View>
  );

  if (screen === "summary" && summary) {
    return (
      <View style={rootStyle}>
        <StatusBar style="light" />
        <ScrollView contentContainerStyle={styles.summary}>
          <Ionicons name="sparkles" size={40} color={PENCIL} />
          <Text style={styles.heading}>Session complete</Text>
          <Text style={styles.said}>{summary.summary}</Text>
          <View style={styles.stats}>
            <Stat icon="time-outline" label="minutes" value={summary.minutes} />
            <Stat icon="bulb-outline" label="hints" value={summary.hints_given} />
            <Stat icon="checkmark-done" label="fixes" value={summary.mistakes_fixed} />
            <Stat icon="school" label="solved" value={summary.problems_finished} />
          </View>
          <Pressable style={styles.primary} onPress={start}>
            <Ionicons name="play" size={20} color={SLATE} />
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
    const thinking = !!tutor?.thinking;
    const paused = tutor?.phase === "paused";
    const live = screen === "session" && !paused;
    const line = lost
      ? "Connection lost. Sensei will pick up when it's back."
      : screen === "connecting" ? status : paused ? "Paused: not looking or listening"
      : thinking ? "Looking and thinking…" : voice ? "Watching and listening" : "Watching";
    return (
      <View style={styles.fill}>
        <StatusBar style="light" />
        {camera}
        {/* top: what's being recorded, the clock, and the two hide/show toggles */}
        <View style={[styles.topBar, { paddingTop: insets.top + 8 }]}>
          <Pill icon={paused ? "pause" : "ellipse"} color={paused ? PENCIL : RED} text={paused ? "PAUSED" : "REC"} />
          <Pill icon={voice && !paused ? "mic" : "mic-off"} color={voice && !paused ? CHALK : MUTED}
                text={voice && !paused ? "listening" : "mic off"} />
          <View style={styles.grow} />
          {tutor && <Pill icon="time-outline" color={CHALK} text={clock(tutor.remaining_s)} />}
          <RoundIcon icon={showText ? "chatbubble-ellipses" : "chatbubble-ellipses-outline"} size={40}
                     onPress={() => setShowText((v) => !v)} label={showText ? "Hide text" : "Show text"} />
          <RoundIcon icon={showControls ? "eye" : "eye-off"} size={40}
                     onPress={() => setShowControls((v) => !v)} label={showControls ? "Hide controls" : "Show controls"} />
        </View>

        <View style={[styles.bottom, { paddingBottom: insets.bottom + 12 }]}>
          {showText ? (
            <View style={styles.caption}>
              <Text style={styles.captionSensei}>{said ?? "Hi! Getting ready…"}</Text>
              {!!heard && <Text style={styles.captionYou}>You: “{heard}”</Text>}
              <View style={styles.statusRow}>
                <View style={[styles.dot, live && !lost && styles.dotLive, lost && styles.dotLost]} />
                <Text style={styles.status}>{line}</Text>
              </View>
            </View>
          ) : (
            (thinking || lost || screen === "connecting") && <Pill icon="ellipsis-horizontal" color={CHALK} text={line} />
          )}
          {showControls && (
            <View style={styles.controls}>
              <View style={styles.actionRow}>
                <Action icon="bulb-outline" label="Hint" onPress={() => press("hint")} disabled={!live || thinking} />
                <Action icon="checkmark-done" label="Check" onPress={() => press("check")} disabled={!live || thinking} />
                <Action icon="scan-outline" label="See" onPress={() => press("look")} disabled={!live || thinking} />
                <Action icon="repeat" label="Repeat" onPress={() => press("repeat")} disabled={!said} />
              </View>
              <View style={styles.mainRow}>
                <RoundIcon icon={voice ? "mic" : "mic-off"} size={60} active={voice && !paused}
                           onPress={toggleVoice} disabled={!live} label={voice ? "Voice on" : "Voice off"} />
                {screen === "session" && (
                  <RoundIcon icon={paused ? "play" : "pause"} size={72} primary
                             onPress={() => press(paused ? "resume" : "pause")} label={paused ? "Resume" : "Pause"} />
                )}
                <RoundIcon icon="stop" size={60} danger
                           onPress={() => (screen === "session" ? press("end") : leave())}
                           label={screen === "session" ? "End" : "Cancel"} />
              </View>
            </View>
          )}
        </View>
      </View>
    );
  }

  // home
  return (
    <View style={rootStyle}>
      <StatusBar style="light" />
      <View style={styles.homeTop}>{camera}</View>
      <View style={styles.panel}>
        <Text style={styles.label}>How long do you want to study?</Text>
        <View style={styles.chipRow}>
          {LENGTHS.map((m) => (
            <Pressable key={m} style={[styles.chip, minutes === m && styles.chipOn]} onPress={() => setMinutes(m)}>
              <Ionicons name="hourglass-outline" size={16} color={minutes === m ? SLATE : CHALK} />
              <Text style={[styles.chipText, minutes === m && styles.chipTextOn]}>{m} min</Text>
            </Pressable>
          ))}
        </View>
        <Pressable style={styles.primary} onPress={start}>
          <Ionicons name="play" size={22} color={SLATE} />
          <Text style={styles.primaryText}>Start with Sensei</Text>
        </Pressable>
        {!!status && <Text style={styles.error}>{status}</Text>}
        <View style={styles.links}>
          <Pressable style={styles.linkRow} onPress={() => setShowSettings((v) => !v)}>
            <Ionicons name="settings-outline" size={16} color={MUTED} />
            <Text style={styles.link}>{showSettings ? "Hide settings" : "Settings"}</Text>
          </Pressable>
          <Pressable style={styles.linkRow} onPress={() => speak("Hi, I'm Sensei. If you can hear me, your sound is working.")}>
            <Ionicons name="volume-high" size={16} color={MUTED} />
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

function Pill({ icon, color, text }: { icon: IconName; color: string; text: string }) {
  return (
    <View style={styles.pill}>
      <Ionicons name={icon} size={icon === "ellipse" ? 10 : 14} color={color} />
      <Text style={[styles.pillText, { color }]}>{text}</Text>
    </View>
  );
}

function RoundIcon(props: {
  icon: IconName; size: number; onPress: () => void; label: string;
  disabled?: boolean; active?: boolean; primary?: boolean; danger?: boolean;
}) {
  const { icon, size, onPress, label, disabled, active, primary, danger } = props;
  const bg = primary ? PENCIL : active ? RED : danger ? "rgba(224,122,95,0.18)" : "rgba(23,37,42,0.72)";
  const fg = primary ? SLATE : danger ? RED : CHALK;
  return (
    <Pressable onPress={onPress} disabled={disabled} accessibilityLabel={label} accessibilityRole="button"
               style={[styles.round, { width: size, height: size, borderRadius: size / 2, backgroundColor: bg },
                       danger && styles.roundDanger, disabled && styles.disabled]}>
      <Ionicons name={icon} size={size * 0.46} color={fg} />
    </Pressable>
  );
}

function Action({ icon, label, onPress, disabled }: { icon: IconName; label: string; onPress: () => void; disabled?: boolean }) {
  return (
    <Pressable onPress={onPress} disabled={disabled} accessibilityLabel={label} accessibilityRole="button"
               style={[styles.action, disabled && styles.disabled]}>
      <Ionicons name={icon} size={24} color={CHALK} />
      <Text style={styles.actionText}>{label}</Text>
    </Pressable>
  );
}

function Stat({ icon, label, value }: { icon: IconName; label: string; value: number }) {
  return (
    <View style={styles.stat}>
      <Ionicons name={icon} size={20} color={PENCIL} />
      <Text style={styles.statValue}>{value}</Text>
      <Text style={styles.muted}>{label}</Text>
    </View>
  );
}

const SLATE = "#17252A";
const CHALK = "#EEF1EC";
const MUTED = "#9FB3AE";
const PENCIL = "#F2C14E";
const RED = "#E07A5F";
const LINE = "#34484E";
const GLASS = "rgba(23,37,42,0.78)";

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: SLATE },
  fill: { flex: 1, backgroundColor: "#000" },
  center: { justifyContent: "center", alignItems: "center", padding: 24, gap: 8 },
  cameraOff: { backgroundColor: "#0E181B" },
  homeTop: { flex: 1 },
  brand: { color: PENCIL, fontSize: 40, fontWeight: "700" },
  grow: { flex: 1 },
  // session overlays
  topBar: { position: "absolute", top: 0, left: 0, right: 0, paddingHorizontal: 12, flexDirection: "row",
            alignItems: "center", gap: 8 },
  pill: { flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: GLASS, borderRadius: 16,
          paddingHorizontal: 10, paddingVertical: 6, alignSelf: "flex-start" },
  pillText: { fontSize: 13, fontWeight: "600", fontVariant: ["tabular-nums"] },
  bottom: { position: "absolute", left: 0, right: 0, bottom: 0, paddingHorizontal: 12, gap: 10 },
  caption: { backgroundColor: GLASS, borderRadius: 14, padding: 14, gap: 6 },
  captionSensei: { color: CHALK, fontSize: 20, lineHeight: 27 },
  captionYou: { color: MUTED, fontSize: 15, fontStyle: "italic" },
  controls: { backgroundColor: GLASS, borderRadius: 18, paddingVertical: 12, paddingHorizontal: 10, gap: 12 },
  actionRow: { flexDirection: "row", justifyContent: "space-around" },
  action: { alignItems: "center", gap: 4, minWidth: 64, paddingVertical: 4 },
  actionText: { color: CHALK, fontSize: 12 },
  mainRow: { flexDirection: "row", justifyContent: "space-evenly", alignItems: "center" },
  round: { alignItems: "center", justifyContent: "center" },
  roundDanger: { borderWidth: 1.5, borderColor: RED },
  // shared
  panel: { padding: 20, paddingBottom: 28, gap: 14, backgroundColor: SLATE },
  said: { color: CHALK, fontSize: 22, lineHeight: 30 },
  heading: { color: PENCIL, fontSize: 26, fontWeight: "700" },
  label: { color: MUTED, fontSize: 15 },
  statusRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  dot: { width: 8, height: 8, borderRadius: 4, backgroundColor: MUTED },
  dotLive: { backgroundColor: PENCIL },
  dotLost: { backgroundColor: RED },
  status: { color: MUTED, fontSize: 13, flex: 1 },
  muted: { color: MUTED, fontSize: 14, textAlign: "center" },
  error: { color: RED, fontSize: 14 },
  link: { color: MUTED, fontSize: 14, textDecorationLine: "underline" },
  links: { flexDirection: "row", justifyContent: "space-between" },
  linkRow: { flexDirection: "row", alignItems: "center", gap: 6 },
  disabled: { opacity: 0.35 },
  chipRow: { flexDirection: "row", gap: 10 },
  chip: { flexGrow: 1, flexDirection: "row", justifyContent: "center", gap: 6, borderColor: LINE, borderWidth: 1,
          borderRadius: 20, paddingVertical: 10, alignItems: "center" },
  chipOn: { borderColor: PENCIL, backgroundColor: PENCIL },
  chipText: { color: CHALK, fontSize: 15 },
  chipTextOn: { color: SLATE, fontWeight: "700" },
  input: {
    color: CHALK, borderColor: LINE, borderWidth: 1, borderRadius: 8,
    paddingHorizontal: 12, paddingVertical: 10, fontSize: 15,
  },
  primary: { backgroundColor: PENCIL, borderRadius: 10, paddingVertical: 16, alignItems: "center",
             flexDirection: "row", justifyContent: "center", gap: 8 },
  primaryText: { color: SLATE, fontSize: 17, fontWeight: "700" },
  secondary: { borderColor: PENCIL, borderWidth: 1, borderRadius: 10, paddingVertical: 14, alignItems: "center" },
  secondaryText: { color: PENCIL, fontSize: 16, fontWeight: "600" },
  summary: { padding: 24, paddingTop: 40, gap: 18, alignItems: "stretch" },
  stats: { flexDirection: "row", justifyContent: "space-between" },
  stat: { alignItems: "center", flex: 1, gap: 2 },
  statValue: { color: CHALK, fontSize: 28, fontWeight: "700" },
});
