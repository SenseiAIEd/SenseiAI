// Sensei Cam: the phone on the Sensei Desk head.
//
// Streams live camera video and microphone audio to the Sensei gateway on the DGX Spark over
// WebRTC, and speaks aloud every instruction the gateway sends back on the "sensei" data channel.
// Reaches the gateway directly on a LAN, or from anywhere through Tailscale Funnel: then the
// media goes through the TURN relay the gateway lists at /config.
//
// Data channel messages (JSON):
//   gateway -> phone  {"type": "say", "text": "..."}     speak this now
//                     {"type": "hush"}                   stop speaking
//   phone -> gateway  {"type": "hello", "app": "..."}    channel is open
//                     {"type": "spoken", "text": "..."}  finished speaking this
//
// Needs a custom build (not Expo Go): see README.md.
import { useEffect, useRef, useState } from "react";
import { PermissionsAndroid, Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { MediaStream, RTCPeerConnection, RTCRtpSender, RTCView, mediaDevices } from "react-native-webrtc";
import * as Speech from "expo-speech";
import { useKeepAwake } from "expo-keep-awake";
import { StatusBar } from "expo-status-bar";
import * as SecureStore from "expo-secure-store";

const APP_ID = "sensei-cam/0.2";
const DEFAULT_SERVER = "https://spark-e257.tail803c7f.ts.net:8443";
const REQUEST_TIMEOUT_MS = 10000;
const ICE_GATHER_DIRECT_MS = 3000;
const ICE_GATHER_RELAY_MS = 8000; // a TURN allocation through Funnel crosses the internet

type Phase = "idle" | "connecting" | "live" | "lost";
type GatewayMessage = { type: "say"; text: string } | { type: "hush" };
type IceServer = { urls: string | string[]; username?: string; credential?: string };

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

// Server address and access key survive app restarts (the key in the Android keystore).
const saved = {
  load: async () => ({
    server: (await SecureStore.getItemAsync("server")) ?? DEFAULT_SERVER,
    key: (await SecureStore.getItemAsync("key")) ?? "",
  }),
  save: (server: string, key: string) =>
    Promise.all([SecureStore.setItemAsync("server", server), SecureStore.setItemAsync("key", key)]).catch(() => {}),
};

export default function App() {
  useKeepAwake();
  const [server, setServer] = useState(DEFAULT_SERVER);
  const [key, setKey] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [status, setStatus] = useState("Point the camera at the notebook, then connect.");
  const [said, setSaid] = useState<string | null>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);

  useEffect(() => {
    saved.load().then((v) => {
      setServer(v.server);
      setKey(v.key);
    }).catch(() => {});
    return () => hangUp();
  }, []);

  function hangUp() {
    pcRef.current?.close();
    pcRef.current = null;
    setStream((s) => {
      s?.getTracks().forEach((t) => t.stop());
      return null;
    });
    Speech.stop();
  }

  async function connect() {
    const base = server.trim().replace(/\/+$/, "");
    const accessKey = key.trim();
    setPhase("connecting");
    setStatus(`Contacting ${base}`);
    try {
      // Before touching the camera: is the gateway there, and does it offer a relay?
      const { iceServers } = (await gateway(`${base}/config`, accessKey)) as { iceServers: IceServer[] };
      saved.save(base, accessKey);

      setStatus("Starting camera and microphone");
      if (!(await askPermissions())) throw new Error("Camera and microphone permission are needed.");

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
      channel.onopen = () => channel.send(JSON.stringify({ type: "hello", app: APP_ID }));
      channel.onmessage = (e: unknown) => {
        let msg: GatewayMessage;
        try {
          msg = JSON.parse(String((e as { data: unknown }).data));
        } catch {
          return;
        }
        if (msg.type === "say") {
          const text = msg.text;
          setSaid(text);
          Speech.stop();
          Speech.speak(text, {
            rate: 0.95,
            onDone: () => {
              if (channel.readyState === "open") channel.send(JSON.stringify({ type: "spoken", text }));
            },
          });
        } else if (msg.type === "hush") {
          Speech.stop();
        }
      };

      pc.onconnectionstatechange = () => {
        if (pcRef.current !== pc) return; // an old call closing
        const s = pc.connectionState;
        if (s === "connected") {
          setPhase("live");
          setStatus("Live: Sensei is watching and recording");
        } else if (s === "disconnected" || s === "failed") {
          setPhase("lost");
          setStatus("Connection lost. Check the network, then reconnect.");
        }
      };

      const relayed = iceServers.length > 0;
      setStatus(relayed ? `Calling ${base} (via relay)` : `Calling ${base}`);
      const offer = await pc.createOffer({});
      await pc.setLocalDescription(offer);
      await waitForIceGathering(pc, relayed ? ICE_GATHER_RELAY_MS : ICE_GATHER_DIRECT_MS);
      const answer = await gateway(`${base}/offer`, accessKey, pc.localDescription);
      await pc.setRemoteDescription(answer);
      await preferSharpVideo(videoSender);
    } catch (e) {
      hangUp();
      setPhase("idle");
      setStatus(`Couldn't connect to ${base}: ${e instanceof Error ? e.message : e}`);
    }
  }

  function disconnect() {
    hangUp();
    setPhase("idle");
    setStatus("Disconnected");
  }

  const busy = phase === "connecting" || phase === "live" || phase === "lost";

  return (
    <View style={styles.root}>
      <StatusBar style="light" />
      {stream ? (
        <RTCView streamURL={stream.toURL()} style={styles.camera} objectFit="cover" />
      ) : (
        <View style={[styles.camera, styles.center]}>
          <Text style={styles.status}>Camera starts when you connect.</Text>
        </View>
      )}
      <View style={styles.panel}>
        <Text style={styles.said}>{said ?? "Sensei's instructions will appear here."}</Text>
        <View style={styles.statusRow}>
          <View style={[styles.dot, phase === "live" && styles.dotLive, phase === "lost" && styles.dotLost]} />
          <Text style={styles.status}>{status}</Text>
        </View>
        <TextInput
          style={styles.input}
          value={server}
          onChangeText={setServer}
          editable={!busy}
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
          editable={!busy}
          autoCapitalize="none"
          autoCorrect={false}
          secureTextEntry
          placeholder="Access key (from the Spark's sensei.env)"
          placeholderTextColor="#6F8580"
        />
        <Pressable style={busy ? styles.secondary : styles.primary} onPress={busy ? disconnect : connect}>
          <Text style={busy ? styles.secondaryText : styles.primaryText}>{busy ? "Disconnect" : "Connect"}</Text>
        </Pressable>
      </View>
    </View>
  );
}

const SLATE = "#17252A";
const CHALK = "#EEF1EC";
const MUTED = "#9FB3AE";
const PENCIL = "#F2C14E";
const RED = "#E07A5F";

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: SLATE },
  center: { justifyContent: "center", alignItems: "center" },
  camera: { flex: 1, backgroundColor: "#0E181B" },
  panel: { padding: 20, paddingBottom: 32, gap: 14, backgroundColor: SLATE },
  said: { color: CHALK, fontSize: 22, lineHeight: 30 },
  statusRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  dot: { width: 10, height: 10, borderRadius: 5, backgroundColor: MUTED },
  dotLive: { backgroundColor: PENCIL },
  dotLost: { backgroundColor: RED },
  status: { color: MUTED, fontSize: 14, flex: 1 },
  input: {
    color: CHALK, borderColor: "#34484E", borderWidth: 1, borderRadius: 8,
    paddingHorizontal: 12, paddingVertical: 10, fontSize: 15,
  },
  primary: { backgroundColor: PENCIL, borderRadius: 8, paddingVertical: 14, alignItems: "center" },
  primaryText: { color: SLATE, fontSize: 16, fontWeight: "600" },
  secondary: { borderColor: PENCIL, borderWidth: 1, borderRadius: 8, paddingVertical: 14, alignItems: "center" },
  secondaryText: { color: PENCIL, fontSize: 16, fontWeight: "600" },
});
