"use client";

import {
  AlertTriangle,
  Camera,
  CameraOff,
  Download,
  Eye,
  Loader2,
  MonitorUp,
  Play,
  RefreshCw,
  ScanFace,
  Square,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { analyzeLiveFrameAction, type LiveFrameResult } from "@/app/identify/actions";
import { StatusBadge, toneForOutcome } from "@/components/states/status-badge";
import { formatScore, shortId } from "@/lib/format";
import { motionPercent, shouldIdentifyFrame } from "@/lib/motion";
import { reconcileTracks, type FaceTrack, type TrackDetection } from "@/lib/tracking";

const MOTION_WIDTH = 96;
const MOTION_HEIGHT = 54;
const SAMPLE_INTERVAL_MS = 400;
const IDENTIFICATION_COOLDOWN_MS = 3_000;
// Keep Server Action bodies comfortably below Next's 1 MB default while
// retaining enough facial detail for the detector.
const MAX_CAPTURE_WIDTH = 960;

type CameraState = "stopped" | "starting" | "running";
type LiveSourceKind = "camera" | "screen";

interface CameraDevice {
  deviceId: string;
  label: string;
}

interface LiveSighting {
  trackId: string;
  observedAt: number;
  personUuid: string | null;
  outcome: "accept" | "review" | "reject";
  score: number | null;
}

interface SessionSummary {
  source: LiveSourceKind;
  startedAt: number;
  endedAt: number;
  trackCount: number;
  knownPeople: string[];
  sightingCount: number;
  alertCount: number;
}

interface OverlayRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export function CameraTracker() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const previewRef = useRef<HTMLDivElement>(null);
  const motionCanvasRef = useRef<HTMLCanvasElement>(null);
  const captureCanvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<number | null>(null);
  const previousFrameRef = useRef<Uint8ClampedArray | null>(null);
  const identifyingRef = useRef(false);
  const lastIdentificationAtRef = useRef(0);
  const thresholdRef = useRef(4);
  const tracksRef = useRef<FaceTrack[]>([]);
  const sightingsRef = useRef<LiveSighting[]>([]);
  const seenTrackIdsRef = useRef(new Set<string>());
  const nextTrackNumberRef = useRef(1);
  const sessionStartedAtRef = useRef<number | null>(null);
  const sourceKindRef = useRef<LiveSourceKind | null>(null);

  const [cameraState, setCameraState] = useState<CameraState>("stopped");
  const [sourceKind, setSourceKind] = useState<LiveSourceKind | null>(null);
  const [sourceChooserOpen, setSourceChooserOpen] = useState(true);
  const [devices, setDevices] = useState<CameraDevice[]>([]);
  const [selectedDevice, setSelectedDevice] = useState("");
  const [threshold, setThreshold] = useState(4);
  const [motion, setMotion] = useState(0);
  const [identifying, setIdentifying] = useState(false);
  const [result, setResult] = useState<LiveFrameResult | null>(null);
  const [identifiedAt, setIdentifiedAt] = useState<Date | null>(null);
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [visibleTracks, setVisibleTracks] = useState<FaceTrack[]>([]);
  const [sightings, setSightings] = useState<LiveSighting[]>([]);
  const [frameSize, setFrameSize] = useState<{ width: number; height: number } | null>(null);
  const [overlayRect, setOverlayRect] = useState<OverlayRect | null>(null);
  const [sessionSummary, setSessionSummary] = useState<SessionSummary | null>(null);
  const [sessionTitle, setSessionTitle] = useState("");

  useEffect(() => {
    thresholdRef.current = threshold;
  }, [threshold]);

  const readDevices = useCallback(async () => {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    const available = await navigator.mediaDevices.enumerateDevices();
    setDevices(
      available
        .filter((device) => device.kind === "videoinput")
        .map((device, index) => ({
          deviceId: device.deviceId,
          label: device.label || `Camera ${index + 1}`,
        })),
    );
  }, []);

  const releaseSource = useCallback(() => {
    if (timerRef.current !== null) window.clearInterval(timerRef.current);
    timerRef.current = null;
    const stream = streamRef.current;
    streamRef.current = null;
    stream?.getTracks().forEach((track) => track.stop());
    previousFrameRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
  }, []);

  const beginSession = useCallback((kind: LiveSourceKind) => {
    tracksRef.current = [];
    sightingsRef.current = [];
    seenTrackIdsRef.current = new Set<string>();
    nextTrackNumberRef.current = 1;
    sessionStartedAtRef.current = Date.now();
    sourceKindRef.current = kind;
    setVisibleTracks([]);
    setSightings([]);
    setFrameSize(null);
    setResult(null);
    setIdentifiedAt(null);
    setSessionSummary(null);
  }, []);

  const finishSession = useCallback(() => {
    const startedAt = sessionStartedAtRef.current;
    const kind = sourceKindRef.current;
    if (startedAt !== null && kind !== null) {
      const knownPeople = Array.from(
        new Set(
          sightingsRef.current
            .map((sighting) => sighting.personUuid)
            .filter((person): person is string => person !== null),
        ),
      );
      const endedAt = Date.now();
      setSessionSummary({
        source: kind,
        startedAt,
        endedAt,
        trackCount: seenTrackIdsRef.current.size,
        knownPeople,
        sightingCount: sightingsRef.current.length,
        alertCount: sightingsRef.current.filter((sighting) => sighting.outcome === "accept").length,
      });
      setSessionTitle(`${kind === "screen" ? "CCTV" : "Camera"} session ${new Date(endedAt).toLocaleString()}`);
    }
    sessionStartedAtRef.current = null;
    sourceKindRef.current = null;
    releaseSource();
    setCameraState("stopped");
    setMotion(0);
    setVisibleTracks([]);
  }, [releaseSource]);

  const identifyCurrentFrame = useCallback(async () => {
    const video = videoRef.current;
    const canvas = captureCanvasRef.current;
    if (video === null || canvas === null || video.videoWidth === 0 || identifyingRef.current) return;

    identifyingRef.current = true;
    lastIdentificationAtRef.current = Date.now();
    setIdentifying(true);

    const scale = Math.min(1, MAX_CAPTURE_WIDTH / video.videoWidth);
    canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
    canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
    canvas.getContext("2d", { alpha: false })?.drawImage(video, 0, 0, canvas.width, canvas.height);

    try {
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.78));
      if (blob === null) throw new Error("The browser could not capture this camera frame.");

      const body = new FormData();
      body.append("image", new File([blob], `camera-${Date.now()}.jpg`, { type: "image/jpeg" }));
      const outcome = await analyzeLiveFrameAction(body);
      setResult(outcome);
      const observedAt = Date.now();
      if (outcome.ok) {
        const detections: TrackDetection[] = outcome.analysis.faces.map((face) => {
          const best = face.identification.candidates[0];
          return {
            box: face.box,
            personUuid: face.identification.outcome === "reject" ? null : (best?.person_uuid ?? null),
            outcome: face.identification.outcome,
            score: best?.score ?? null,
          };
        });
        const reconciled = reconcileTracks(
          tracksRef.current,
          detections,
          observedAt,
          nextTrackNumberRef.current,
        );
        tracksRef.current = reconciled.active;
        nextTrackNumberRef.current = reconciled.nextTrackNumber;
        reconciled.visible.forEach((track) => seenTrackIdsRef.current.add(track.trackId));
        const observations: LiveSighting[] = reconciled.visible.map((track) => ({
          trackId: track.trackId,
          observedAt,
          personUuid: track.personUuid,
          outcome: track.outcome,
          score: track.score,
        }));
        sightingsRef.current = [...sightingsRef.current, ...observations].slice(-250);
        setVisibleTracks(reconciled.visible);
        setSightings(sightingsRef.current);
        setFrameSize({ width: outcome.analysis.frame_width, height: outcome.analysis.frame_height });
      }
      setIdentifiedAt(new Date(observedAt));
    } catch (error) {
      setResult({
        ok: false,
        code: "capture_failed",
        message: error instanceof Error ? error.message : "The camera frame could not be identified.",
      });
      setIdentifiedAt(new Date());
    } finally {
      identifyingRef.current = false;
      setIdentifying(false);
    }
  }, []);

  const inspectFrame = useCallback(() => {
    const video = videoRef.current;
    const canvas = motionCanvasRef.current;
    if (video === null || canvas === null || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return;

    const context = canvas.getContext("2d", { alpha: false, willReadFrequently: true });
    if (context === null) return;
    context.drawImage(video, 0, 0, MOTION_WIDTH, MOTION_HEIGHT);
    const current = context.getImageData(0, 0, MOTION_WIDTH, MOTION_HEIGHT).data;
    const previous = previousFrameRef.current;
    previousFrameRef.current = new Uint8ClampedArray(current);
    if (previous === null) return;

    const nextMotion = motionPercent(previous, current);
    setMotion(nextMotion);
    if (
      shouldIdentifyFrame({
        motion: nextMotion,
        threshold: thresholdRef.current,
        now: Date.now(),
        lastIdentificationAt: lastIdentificationAtRef.current,
        cooldownMs: IDENTIFICATION_COOLDOWN_MS,
        identifying: identifyingRef.current,
      })
    ) {
      void identifyCurrentFrame();
    }
  }, [identifyCurrentFrame]);

  const startCamera = useCallback(
    async (deviceId = selectedDevice) => {
      if (!window.isSecureContext) {
        setCameraError("Camera access requires HTTPS, except when the app is opened on localhost.");
        return;
      }
      if (!navigator.mediaDevices?.getUserMedia) {
        setCameraError("This browser does not support camera capture.");
        return;
      }

      releaseSource();
      setCameraState("starting");
      setCameraError(null);
      setMotion(0);
      setSourceKind("camera");
      setSourceChooserOpen(false);

      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: false,
          video: {
            ...(deviceId ? { deviceId: { exact: deviceId } } : { facingMode: { ideal: "environment" } }),
            width: { ideal: 1280 },
            height: { ideal: 720 },
          },
        });
        streamRef.current = stream;
        if (videoRef.current === null) throw new Error("The camera preview is unavailable.");
        videoRef.current.srcObject = stream;
        await videoRef.current.play();

        const activeDevice = stream.getVideoTracks()[0]?.getSettings().deviceId;
        if (activeDevice) setSelectedDevice(activeDevice);
        await readDevices();
        beginSession("camera");
        previousFrameRef.current = null;
        timerRef.current = window.setInterval(inspectFrame, SAMPLE_INTERVAL_MS);
        setCameraState("running");
      } catch (error) {
        releaseSource();
        setCameraState("stopped");
        setCameraError(cameraMessage(error));
      }
    },
    [beginSession, inspectFrame, readDevices, releaseSource, selectedDevice],
  );

  const startSharedScreen = useCallback(async () => {
    if (!window.isSecureContext) {
      setCameraError("Screen sharing requires HTTPS, except when the app is opened on localhost.");
      return;
    }
    if (!navigator.mediaDevices?.getDisplayMedia) {
      setCameraError("This browser does not support screen sharing.");
      return;
    }

    releaseSource();
    setCameraState("starting");
    setCameraError(null);
    setMotion(0);
    setSourceKind("screen");
    setSourceChooserOpen(false);

    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({
        audio: false,
        video: { frameRate: { ideal: 12, max: 20 } },
      });
      streamRef.current = stream;
      if (videoRef.current === null) throw new Error("The shared-screen preview is unavailable.");
      videoRef.current.srcObject = stream;
      await videoRef.current.play();
      beginSession("screen");
      stream.getVideoTracks()[0]?.addEventListener(
        "ended",
        () => {
          if (streamRef.current === stream) finishSession();
        },
        { once: true },
      );
      previousFrameRef.current = null;
      timerRef.current = window.setInterval(inspectFrame, SAMPLE_INTERVAL_MS);
      setCameraState("running");
    } catch (error) {
      releaseSource();
      setCameraState("stopped");
      setCameraError(screenMessage(error));
    }
  }, [beginSession, finishSession, inspectFrame, releaseSource]);

  const stopCamera = useCallback(() => {
    finishSession();
  }, [finishSession]);

  useEffect(() => {
    const mediaDevices = navigator.mediaDevices;
    mediaDevices?.addEventListener?.("devicechange", readDevices);
    return () => {
      mediaDevices?.removeEventListener?.("devicechange", readDevices);
      releaseSource();
    };
  }, [readDevices, releaseSource]);

  useEffect(() => {
    const preview = previewRef.current;
    if (preview === null || frameSize === null) {
      setOverlayRect(null);
      return;
    }
    const update = () => {
      const scale = Math.min(preview.clientWidth / frameSize.width, preview.clientHeight / frameSize.height);
      const width = frameSize.width * scale;
      const height = frameSize.height * scale;
      setOverlayRect({
        left: (preview.clientWidth - width) / 2,
        top: (preview.clientHeight - height) / 2,
        width,
        height,
      });
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(preview);
    return () => observer.disconnect();
  }, [frameSize]);

  const alerts = sightings.filter((sighting) => sighting.outcome === "accept").slice(-5).reverse();

  const downloadSessionSummary = useCallback(() => {
    if (sessionSummary === null) return;
    const data = JSON.stringify(
      { title: sessionTitle, ...sessionSummary, sightings: sightingsRef.current },
      null,
      2,
    );
    const url = URL.createObjectURL(new Blob([data], { type: "application/json" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `eagleeye-live-session-${sessionSummary.endedAt}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }, [sessionSummary, sessionTitle]);

  return (
    <>
      {sessionSummary ? (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm">
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="session-summary-heading"
            className="w-full max-w-xl rounded-2xl border border-line bg-surface p-5 shadow-2xl"
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 id="session-summary-heading" className="text-base font-semibold text-ink">Session complete</h2>
                <p className="mt-1 text-xs text-ink-muted">Metadata and identity proposals only; no full video was retained.</p>
              </div>
              <button
                type="button"
                onClick={() => setSessionSummary(null)}
                aria-label="Close session summary"
                className="rounded-md p-1 text-ink-muted hover:bg-surface-raised"
              >
                <X className="size-5" aria-hidden="true" />
              </button>
            </div>
            <label className="mt-5 block text-xs text-ink-muted">
              Session title
              <input
                value={sessionTitle}
                onChange={(event) => setSessionTitle(event.target.value)}
                className="mt-1 w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink"
              />
            </label>
            <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
              <SummaryMetric label="Tracks" value={sessionSummary.trackCount} />
              <SummaryMetric label="Known people" value={sessionSummary.knownPeople.length} />
              <SummaryMetric label="Sightings" value={sessionSummary.sightingCount} />
              <SummaryMetric label="Alerts" value={sessionSummary.alertCount} />
            </dl>
            <div className="mt-5 flex flex-wrap justify-end gap-2">
              <button
                type="button"
                onClick={() => setSessionSummary(null)}
                className="rounded-md border border-line px-4 py-2 text-sm font-semibold text-ink"
              >
                Close
              </button>
              <button
                type="button"
                onClick={downloadSessionSummary}
                className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-bg"
              >
                <Download className="size-4" aria-hidden="true" /> Save summary
              </button>
            </div>
          </section>
        </div>
      ) : null}

      {sourceChooserOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm">
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="live-source-heading"
            className="w-full max-w-4xl overflow-hidden rounded-2xl border border-line bg-surface shadow-2xl"
          >
            <div className="flex items-center justify-between border-b border-line px-5 py-4">
              <h2 id="live-source-heading" className="text-sm font-semibold text-ink">
                Choose live source
              </h2>
              <button
                type="button"
                onClick={() => setSourceChooserOpen(false)}
                aria-label="Close live source chooser"
                className="rounded-md p-1 text-ink-muted hover:bg-surface-raised hover:text-ink"
              >
                <X className="size-5" aria-hidden="true" />
              </button>
            </div>

            <div className="p-5">
              <div className="flex items-start gap-3">
                <span className="rounded-xl bg-accent/10 p-3 text-accent">
                  <MonitorUp className="size-5" aria-hidden="true" />
                </span>
                <div>
                  <h3 className="text-sm font-semibold text-ink">Camera or shared screen</h3>
                  <p className="mt-1 text-xs text-ink-muted">
                    Analyze a camera or a live CCTV viewer shared from another tab or screen. EagleEye never records the full video.
                  </p>
                </div>
              </div>

              <div className="mt-5 grid gap-3 md:grid-cols-2">
                <button
                  type="button"
                  onClick={() => void startCamera()}
                  className="flex min-h-40 items-start gap-4 rounded-2xl border border-line p-5 text-left transition hover:border-accent/60 hover:bg-surface-raised"
                >
                  <span className="rounded-xl bg-accent/10 p-3 text-accent">
                    <Camera className="size-5" aria-hidden="true" />
                  </span>
                  <span>
                    <span className="block text-sm font-semibold text-ink">Use camera</span>
                    <span className="mt-1 block text-xs leading-relaxed text-ink-muted">
                      Scan from this phone or computer camera with zoom where supported.
                    </span>
                  </span>
                </button>

                <div className="min-h-40 rounded-2xl border border-line p-5">
                  <div className="flex items-start gap-4">
                    <span className="rounded-xl bg-accept/10 p-3 text-accept">
                      <MonitorUp className="size-5" aria-hidden="true" />
                    </span>
                    <div>
                      <h3 className="text-sm font-semibold text-ink">Share CCTV screen</h3>
                      <p className="mt-1 text-xs leading-relaxed text-ink-muted">
                        Select a live CCTV tab, window, or screen from the system picker.
                      </p>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => void startSharedScreen()}
                    className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-bg"
                  >
                    <MonitorUp className="size-4" aria-hidden="true" /> Share live screen
                  </button>
                  <p className="mt-3 text-xs text-ink-faint">Only motion-triggered frames are submitted for identification.</p>
                </div>
              </div>
            </div>
          </section>
        </div>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_22rem]">
      <section className="panel overflow-hidden" aria-labelledby="camera-preview-heading">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
          <div>
            <h2 id="camera-preview-heading" className="text-sm font-semibold text-ink">
              {sourceKind === "screen" ? "Shared CCTV screen" : "Live camera"}
            </h2>
            <p className="mt-0.5 text-xs text-ink-faint">Frames stay local until motion triggers identification</p>
          </div>
          <div className="flex items-center gap-2 text-xs text-ink-muted">
            <span className={`size-2 rounded-full ${cameraState === "running" ? "bg-accept" : "bg-ink-faint"}`} />
            {cameraState === "running" ? "Watching" : cameraState === "starting" ? "Starting" : "Stopped"}
          </div>
        </div>

        <div ref={previewRef} className="relative aspect-video overflow-hidden bg-black">
          <video ref={videoRef} autoPlay muted playsInline className="size-full object-contain" />
          {cameraState !== "running" ? (
            <div className="absolute inset-0 flex items-center justify-center bg-surface-sunken text-center">
              <div className="max-w-xs px-6 text-sm text-ink-muted">
                {cameraState === "starting" ? (
                  <Loader2 className="mx-auto mb-3 size-7 animate-spin text-accent" aria-hidden="true" />
                ) : (
                  <CameraOff className="mx-auto mb-3 size-7 text-ink-faint" aria-hidden="true" />
                )}
                {cameraState === "starting" ? "Waiting for source permission…" : "Choose a live source and start tracking."}
              </div>
            </div>
          ) : null}

          {cameraState === "running" ? (
            <div className="absolute top-3 left-3 rounded-md bg-black/70 px-2.5 py-1.5 text-xs text-white backdrop-blur-sm">
              Motion {motion.toFixed(1)}% · trigger {threshold.toFixed(1)}%
            </div>
          ) : null}

          {identifying ? (
            <div className="absolute top-3 right-3 flex items-center gap-2 rounded-md bg-black/70 px-2.5 py-1.5 text-xs text-white backdrop-blur-sm">
              <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
              Identifying
            </div>
          ) : null}

          {overlayRect && frameSize
            ? visibleTracks.map((track) => {
                const left = overlayRect.left + (track.box.x1 / frameSize.width) * overlayRect.width;
                const top = overlayRect.top + (track.box.y1 / frameSize.height) * overlayRect.height;
                const width = ((track.box.x2 - track.box.x1) / frameSize.width) * overlayRect.width;
                const height = ((track.box.y2 - track.box.y1) / frameSize.height) * overlayRect.height;
                const color =
                  track.outcome === "accept"
                    ? "border-accept text-accept"
                    : track.outcome === "review"
                      ? "border-review text-review"
                      : "border-accent text-accent";
                return (
                  <div
                    key={track.trackId}
                    className={`pointer-events-none absolute border-2 ${color}`}
                    style={{ left, top, width, height }}
                  >
                    <div className="absolute -top-7 left-[-2px] whitespace-nowrap rounded-t bg-black/85 px-2 py-1 text-[11px] font-semibold">
                      {track.trackId} · {track.personUuid ? `person ${shortId(track.personUuid)}` : "unknown"}
                      {track.score === null ? "" : ` · ${formatScore(track.score)}`}
                    </div>
                  </div>
                );
              })
            : null}
        </div>

        <canvas ref={motionCanvasRef} width={MOTION_WIDTH} height={MOTION_HEIGHT} className="hidden" />
        <canvas ref={captureCanvasRef} className="hidden" />

        <div className="grid gap-3 border-t border-line p-4 md:grid-cols-[minmax(12rem,1fr)_minmax(12rem,1fr)_auto] md:items-end">
          <div className="block text-xs text-ink-muted">
            Live source
            <button
              type="button"
              onClick={() => setSourceChooserOpen(true)}
              disabled={cameraState === "starting"}
              className="mt-1 flex w-full items-center justify-between rounded-md border border-line bg-surface px-3 py-2 text-left text-sm text-ink disabled:opacity-50"
            >
              <span>{sourceKind === "screen" ? "Shared CCTV screen" : sourceKind === "camera" ? "Device camera" : "Choose source"}</span>
              {sourceKind === "screen" ? <MonitorUp className="size-4 text-ink-muted" /> : <Camera className="size-4 text-ink-muted" />}
            </button>
            {sourceKind === "camera" && devices.length > 1 ? (
              <select
                aria-label="Camera device"
                value={selectedDevice}
                onChange={(event) => {
                  const deviceId = event.target.value;
                  setSelectedDevice(deviceId);
                  if (cameraState === "running") void startCamera(deviceId);
                }}
                disabled={cameraState === "starting"}
                className="mt-2 w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink"
              >
                <option value="">Default camera</option>
                {devices.map((device) => (
                  <option key={device.deviceId} value={device.deviceId}>
                    {device.label}
                  </option>
                ))}
              </select>
            ) : null}
          </div>

          <label className="block text-xs text-ink-muted">
            Motion trigger · {threshold.toFixed(1)}%
            <input
              type="range"
              min="1"
              max="15"
              step="0.5"
              value={threshold}
              onChange={(event) => setThreshold(Number(event.target.value))}
              className="mt-2 block w-full accent-accent"
            />
          </label>

          <div className="flex gap-2">
            {cameraState === "running" ? (
              <button
                type="button"
                onClick={stopCamera}
                className="inline-flex items-center gap-2 rounded-md border border-line px-4 py-2 text-sm font-semibold text-ink"
              >
                <Square className="size-4" aria-hidden="true" /> Stop
              </button>
            ) : (
              <button
                type="button"
                onClick={() => {
                  if (sourceKind === "screen") void startSharedScreen();
                  else if (sourceKind === "camera") void startCamera();
                  else setSourceChooserOpen(true);
                }}
                disabled={cameraState === "starting"}
                className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-bg disabled:opacity-50"
              >
                {cameraState === "starting" ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}
                Start
              </button>
            )}
            {cameraState === "running" ? (
              <button
                type="button"
                onClick={() => void identifyCurrentFrame()}
                disabled={identifying}
                title="Identify the current frame now"
                className="inline-flex items-center gap-2 rounded-md border border-line px-3 py-2 text-sm text-ink-muted disabled:opacity-50"
              >
                <ScanFace className="size-4" aria-hidden="true" /> Scan
              </button>
            ) : null}
          </div>
        </div>

        {cameraError ? (
          <p className="border-t border-line bg-reject/10 px-4 py-3 text-sm text-reject">{cameraError}</p>
        ) : null}
      </section>

      <aside className="space-y-4">
        <section className="panel p-4" aria-live="polite">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Eye className="size-4 text-accent" aria-hidden="true" />
              <h2 className="text-sm font-semibold text-ink">Live tracks</h2>
            </div>
            {identifiedAt ? <span className="text-xs text-ink-faint">{identifiedAt.toLocaleTimeString()}</span> : null}
          </div>

          {result && !result.ok ? (
            <div className="mt-4">
              <p className="text-sm font-semibold text-ink">
                {result.code === "identification_failed" ? "Frame could not be analyzed" : "Identification unavailable"}
              </p>
              <p className="mt-1 text-sm text-ink-muted">{result.message}</p>
            </div>
          ) : visibleTracks.length === 0 ? (
            <div className="py-8 text-center text-sm text-ink-muted">
              <Camera className="mx-auto mb-2 size-6 text-ink-faint" aria-hidden="true" />
              {result?.ok ? "No faces in the latest frame" : "Waiting for motion"}
            </div>
          ) : (
            <div className="mt-4 space-y-2">
              {visibleTracks.slice(0, 6).map((track) => (
                <div key={track.trackId} className="rounded-lg border border-line bg-surface-raised p-3">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-mono text-xs font-semibold text-ink">{track.trackId}</span>
                    <StatusBadge tone={toneForOutcome(track.outcome)}>{track.outcome}</StatusBadge>
                  </div>
                  <div className="mt-2 flex items-end justify-between gap-3">
                    <div>
                      <p className="text-xs text-ink-faint">Identity proposal</p>
                      <p className="mt-0.5 text-sm font-semibold text-ink">
                        {track.personUuid ? `Person ${shortId(track.personUuid)}` : "Unknown person"}
                      </p>
                    </div>
                    <span className="text-xs tabular-nums text-ink-muted">
                      {track.score === null ? "—" : formatScore(track.score)}
                    </span>
                  </div>
                </div>
              ))}
              {visibleTracks.length > 6 ? (
                <p className="pt-1 text-center text-xs text-ink-faint">+{visibleTracks.length - 6} additional tracks</p>
              ) : null}
            </div>
          )}
        </section>

        <section className="panel p-4" aria-live="polite">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <AlertTriangle className="size-4 text-accept" aria-hidden="true" />
              <h2 className="text-sm font-semibold text-ink">Match alerts</h2>
            </div>
            <span className="rounded-full bg-accept/10 px-2 py-0.5 text-xs font-semibold text-accept">{alerts.length}</span>
          </div>
          {alerts.length === 0 ? (
            <p className="mt-3 text-xs leading-relaxed text-ink-muted">No accepted identity proposals in this session.</p>
          ) : (
            <ol className="mt-3 space-y-2">
              {alerts.map((alert) => (
                <li key={`${alert.trackId}-${alert.observedAt}`} className="rounded-lg border border-accept/30 bg-accept/5 p-3">
                  <div className="flex items-center justify-between gap-3 text-xs">
                    <span className="font-semibold text-ink">
                      {alert.trackId} · {alert.personUuid ? `Person ${shortId(alert.personUuid)}` : "Unknown"}
                    </span>
                    <time className="text-ink-faint">{new Date(alert.observedAt).toLocaleTimeString()}</time>
                  </div>
                  <p className="mt-1 text-xs text-ink-muted">
                    Similarity {alert.score === null ? "—" : formatScore(alert.score)} · requires operator verification
                  </p>
                </li>
              ))}
            </ol>
          )}
        </section>

        <section className="panel p-4">
          <h2 className="text-sm font-semibold text-ink">Recent sightings</h2>
          {sightings.length === 0 ? (
            <p className="mt-3 text-xs text-ink-muted">Sightings will appear after the first analyzed frame.</p>
          ) : (
            <ol className="mt-3 divide-y divide-line">
              {sightings.slice(-6).reverse().map((sighting) => (
                <li key={`${sighting.trackId}-${sighting.observedAt}`} className="flex items-center justify-between gap-3 py-2 text-xs">
                  <span className="font-mono font-semibold text-ink">{sighting.trackId}</span>
                  <span className="min-w-0 flex-1 truncate text-ink-muted">
                    {sighting.personUuid ? `Person ${shortId(sighting.personUuid)}` : "Unknown"}
                  </span>
                  <time className="tabular-nums text-ink-faint">{new Date(sighting.observedAt).toLocaleTimeString()}</time>
                </li>
              ))}
            </ol>
          )}
        </section>

        <section className="panel p-4 text-xs leading-relaxed text-ink-muted">
          <div className="flex items-center gap-2 font-semibold text-ink">
            <RefreshCw className="size-4" aria-hidden="true" /> How tracking works
          </div>
          <p className="mt-2">
            Motion is measured in the browser every 400 ms. At most one frame every three seconds is sent for identification.
            The API detects every face in that frame, and the browser associates detections with stable session track IDs.
            Only submitted motion-triggered frames are retained for audit and review; the full video is not recorded.
          </p>
          <p className="mt-2 text-ink-faint">
            Similarity is a raw cosine score, not a probability. An accept is a system proposal, not proof of identity.
          </p>
        </section>
      </aside>
      </div>
    </>
  );
}

function SummaryMetric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-line bg-surface-raised p-3">
      <dt className="text-xs text-ink-faint">{label}</dt>
      <dd className="mt-1 text-xl font-semibold tabular-nums text-ink">{value}</dd>
    </div>
  );
}

function cameraMessage(error: unknown): string {
  if (error instanceof DOMException) {
    if (error.name === "NotAllowedError") return "Camera permission was denied. Allow access in the browser and try again.";
    if (error.name === "NotFoundError") return "No camera was found on this device.";
    if (error.name === "NotReadableError") return "The camera is already in use by another application.";
    if (error.name === "OverconstrainedError") return "The selected camera is no longer available.";
  }
  return error instanceof Error ? error.message : "The camera could not be started.";
}

function screenMessage(error: unknown): string {
  if (error instanceof DOMException) {
    if (error.name === "NotAllowedError") return "Screen sharing was cancelled or denied.";
    if (error.name === "NotReadableError") return "The selected screen cannot be captured by this browser.";
  }
  return error instanceof Error ? error.message : "The shared screen could not be started.";
}
