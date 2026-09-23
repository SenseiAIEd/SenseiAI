// Sensei Cam: the phone on the Sensei Desk head.
//
// Streams live camera video and microphone audio to the Sensei gateway on the DGX Spark over
// WebRTC (local network or Tailscale; no internet needed on a LAN), and speaks aloud every instruction the
// gateway sends back on the "sensei" data channel.
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

const APP_ID = "sensei-cam/0.1";
const REQUEST_TIMEOUT_MS = 10000;
const ICE_GATHER_TIMEOUT_MS = 3000;

type Phase = "idle" | "connecting" | "live" | "lost";
type GatewayMessage = { type: "say"; text: string } | { type: "hush" };

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
function waitForIceGathering(pc: RTCPeerConnection) {
  return new Promise<void>((resolve) => {
    if (pc.iceGatheringState === "complete") return resolve();
    const timer = setTimeout(resolve, ICE_GATHER_TIMEOUT_MS);
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

async function postJson(url: string, body: unknown) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

export default function App() {
  useKeepAwake();
  const [server, setServer] = useState("http://spark-e257.tail803c7f.ts.net:8787");
  const [phase, setPhase] = useState<Phase>("idle");
  const [status, setStatus] = useState("Point the camera at the notebook, then connect.");
  const [said, setSaid] = useState<string | null>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);

  useEffect(() => () => hangUp(), []);

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
    setPhase("connecting");
    setStatus("Starting camera and microphone");
    try {
      if (!(await askPermissions())) throw new Error("Camera and microphone permission are needed.");

      const local = await mediaDevices.getUserMedia({
        audio: true,
        video: { facingMode: "environment", width: 1280, height: 720, frameRate: 15 },
      });
      setStream(local);

      const pc = new RTCPeerConnection({ iceServers: [] }); // LAN only: host candidates are enough
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
          setStatus("Connection lost. Check the Wi-Fi, then reconnect.");
        }
      };

      setStatus(`Calling ${base}`);
      const offer = await pc.createOffer({});
      await pc.setLocalDescription(offer);
      await waitForIceGathering(pc);
      const answer = await postJson(`${base}/offer`, pc.localDescription);
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
          placeholder="http://<spark-address>:8787"
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
