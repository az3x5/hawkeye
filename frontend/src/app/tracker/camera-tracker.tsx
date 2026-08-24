"use client";

import { Camera, CameraOff, Loader2, Play, RefreshCw, ScanFace, Square } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { identifyAction, type IdentifyResult } from "@/app/identify/actions";
import { StatusBadge, toneForOutcome } from "@/components/states/status-badge";
import { formatScore, shortId } from "@/lib/format";
import { motionPercent, shouldIdentifyFrame } from "@/lib/motion";

const MOTION_WIDTH = 96;
const MOTION_HEIGHT = 54;
const SAMPLE_INTERVAL_MS = 400;
const IDENTIFICATION_COOLDOWN_MS = 3_000;
// Keep Server Action bodies comfortably below Next's 1 MB default while
// retaining enough facial detail for the detector.
const MAX_CAPTURE_WIDTH = 960;

type CameraState = "stopped" | "starting" | "running";

interface CameraDevice {
  deviceId: string;
  label: string;
}

export function CameraTracker() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const motionCanvasRef = useRef<HTMLCanvasElement>(null);
  const captureCanvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<number | null>(null);
  const previousFrameRef = useRef<Uint8ClampedArray | null>(null);
  const identifyingRef = useRef(false);
  const lastIdentificationAtRef = useRef(0);
  const thresholdRef = useRef(4);

  const [cameraState, setCameraState] = useState<CameraState>("stopped");
  const [devices, setDevices] = useState<CameraDevice[]>([]);
  const [selectedDevice, setSelectedDevice] = useState("");
  const [threshold, setThreshold] = useState(4);
  const [motion, setMotion] = useState(0);
  const [identifying, setIdentifying] = useState(false);
  const [result, setResult] = useState<IdentifyResult | null>(null);
  const [identifiedAt, setIdentifiedAt] = useState<Date | null>(null);
  const [cameraError, setCameraError] = useState<string | null>(null);

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

  const releaseCamera = useCallback(() => {
    if (timerRef.current !== null) window.clearInterval(timerRef.current);
    timerRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    previousFrameRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
  }, []);

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
      const outcome = await identifyAction(body);
      setResult(outcome);
      setIdentifiedAt(new Date());
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

      releaseCamera();
      setCameraState("starting");
      setCameraError(null);
      setMotion(0);

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
        previousFrameRef.current = null;
        timerRef.current = window.setInterval(inspectFrame, SAMPLE_INTERVAL_MS);
        setCameraState("running");
      } catch (error) {
        releaseCamera();
        setCameraState("stopped");
        setCameraError(cameraMessage(error));
      }
    },
    [inspectFrame, readDevices, releaseCamera, selectedDevice],
  );

  const stopCamera = useCallback(() => {
    releaseCamera();
    setCameraState("stopped");
    setMotion(0);
  }, [releaseCamera]);

  useEffect(() => {
    const mediaDevices = navigator.mediaDevices;
    mediaDevices?.addEventListener?.("devicechange", readDevices);
    return () => {
      mediaDevices?.removeEventListener?.("devicechange", readDevices);
      releaseCamera();
    };
  }, [readDevices, releaseCamera]);

  const best = result?.ok ? result.identification.candidates[0] : undefined;

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_22rem]">
      <section className="panel overflow-hidden" aria-labelledby="camera-preview-heading">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
          <div>
            <h2 id="camera-preview-heading" className="text-sm font-semibold text-ink">
              Live camera
            </h2>
            <p className="mt-0.5 text-xs text-ink-faint">Frames stay local until motion triggers identification</p>
          </div>
          <div className="flex items-center gap-2 text-xs text-ink-muted">
            <span className={`size-2 rounded-full ${cameraState === "running" ? "bg-accept" : "bg-ink-faint"}`} />
            {cameraState === "running" ? "Watching" : cameraState === "starting" ? "Starting" : "Stopped"}
          </div>
        </div>

        <div className="relative aspect-video overflow-hidden bg-black">
          <video ref={videoRef} autoPlay muted playsInline className="size-full object-contain" />
          {cameraState !== "running" ? (
            <div className="absolute inset-0 flex items-center justify-center bg-surface-sunken text-center">
              <div className="max-w-xs px-6 text-sm text-ink-muted">
                {cameraState === "starting" ? (
                  <Loader2 className="mx-auto mb-3 size-7 animate-spin text-accent" aria-hidden="true" />
                ) : (
                  <CameraOff className="mx-auto mb-3 size-7 text-ink-faint" aria-hidden="true" />
                )}
                {cameraState === "starting" ? "Waiting for camera permission…" : "Choose a camera and start tracking."}
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

          {result?.ok && best ? (
            <div className="absolute right-3 bottom-3 left-3 rounded-md bg-black/75 p-3 text-white backdrop-blur-sm">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge tone={toneForOutcome(result.identification.outcome)}>
                  {result.identification.outcome}
                </StatusBadge>
                <span className="text-sm font-semibold">
                  {result.identification.outcome === "accept" ? "Known person" : "Possible person"}
                </span>
              </div>
              <p className="mt-1 text-sm">
                person <span className="font-mono">{shortId(best.person_uuid)}</span> · similarity {formatScore(best.score)}
              </p>
            </div>
          ) : result?.ok && result.identification.outcome === "reject" ? (
            <div className="absolute right-3 bottom-3 left-3 rounded-md bg-black/75 p-3 text-sm text-white backdrop-blur-sm">
              No enrolled person proposed
            </div>
          ) : null}
        </div>

        <canvas ref={motionCanvasRef} width={MOTION_WIDTH} height={MOTION_HEIGHT} className="hidden" />
        <canvas ref={captureCanvasRef} className="hidden" />

        <div className="grid gap-3 border-t border-line p-4 md:grid-cols-[minmax(12rem,1fr)_minmax(12rem,1fr)_auto] md:items-end">
          <label className="block text-xs text-ink-muted">
            Camera source
            <select
              value={selectedDevice}
              onChange={(event) => {
                const deviceId = event.target.value;
                setSelectedDevice(deviceId);
                if (cameraState === "running") void startCamera(deviceId);
              }}
              disabled={cameraState === "starting"}
              className="mt-1 w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink"
            >
              <option value="">Default camera</option>
              {devices.map((device) => (
                <option key={device.deviceId} value={device.deviceId}>
                  {device.label}
                </option>
              ))}
            </select>
          </label>

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
                onClick={() => void startCamera()}
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
            <h2 className="text-sm font-semibold text-ink">Latest identification</h2>
            {identifiedAt ? <span className="text-xs text-ink-faint">{identifiedAt.toLocaleTimeString()}</span> : null}
          </div>

          {result === null ? (
            <div className="py-8 text-center text-sm text-ink-muted">
              <Camera className="mx-auto mb-2 size-6 text-ink-faint" aria-hidden="true" />
              Waiting for motion
            </div>
          ) : result.ok ? (
            <div className="mt-4 space-y-3">
              <StatusBadge tone={toneForOutcome(result.identification.outcome)}>
                {result.identification.outcome}
              </StatusBadge>
              {best ? (
                <dl className="space-y-2 text-sm">
                  <div>
                    <dt className="text-xs text-ink-faint">Person UUID</dt>
                    <dd className="identifier break-all">{best.person_uuid}</dd>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <dt className="text-xs text-ink-faint">Similarity</dt>
                      <dd className="font-semibold tabular-nums text-ink">{formatScore(best.score)}</dd>
                    </div>
                    <div>
                      <dt className="text-xs text-ink-faint">Samples</dt>
                      <dd className="font-semibold tabular-nums text-ink">{best.sample_count}</dd>
                    </div>
                  </div>
                </dl>
              ) : (
                <p className="text-sm text-ink-muted">No enrolled person was proposed.</p>
              )}
            </div>
          ) : (
            <div className="mt-4">
              <p className="text-sm font-semibold text-ink">
                {result.code === "identification_failed" ? "No single face detected" : "Identification unavailable"}
              </p>
              <p className="mt-1 text-sm text-ink-muted">{result.message}</p>
            </div>
          )}
        </section>

        <section className="panel p-4 text-xs leading-relaxed text-ink-muted">
          <div className="flex items-center gap-2 font-semibold text-ink">
            <RefreshCw className="size-4" aria-hidden="true" /> How tracking works
          </div>
          <p className="mt-2">
            Motion is measured in the browser every 400 ms. At most one frame every three seconds is sent for identification.
            The API requires exactly one face and records submitted frames for audit and review.
          </p>
          <p className="mt-2 text-ink-faint">
            Similarity is a raw cosine score, not a probability. An accept is a system proposal, not proof of identity.
          </p>
        </section>
      </aside>
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
