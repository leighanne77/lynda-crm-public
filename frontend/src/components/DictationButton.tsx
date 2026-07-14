/**
 * Large round dictation button — sits at the top of the chat area,
 * centered. Primary voice-input affordance for the DIN portal.
 *
 * Gestures:
 *   - Mouse / pen / desktop: tap to start, tap again to stop.
 *   - Touch (mobile): press and hold to record, release to stop.
 *     Brushes under ~250 ms are treated as accidental and discarded.
 *
 * On stop, the uploaded transcript is dropped into the chat textarea
 * via `onTranscript`; the user reviews and clicks Send. ChatInterface
 * flags the next send as mode="voice", which both shortens DESS's
 * reply for ear-friendly reading AND triggers automatic TTS playback
 * on the assistant bubble.
 */

import { Loader2, Mic, MicOff, X } from "lucide-react";
import { useEffect, useRef } from "react";

import { useVoiceRecorder } from "../hooks/useVoiceRecorder";

interface Props {
  onTranscript: (text: string) => void;
  disabled?: boolean;
  /** "large" = empty-state hero button at the top of the chat.
   *  "compact" = secondary placement above the input after the first
   *  message, so the welcome-state button isn't taking up real estate
   *  next to scrolled message history. */
  size?: "large" | "compact";
}

const MIN_HOLD_MS = 250;

const SIZE_CLASSES = {
  large: {
    button: "h-24 w-24",
    icon: 40,
    pulse: "inset-3",
    cancelIcon: 14,
    errorMaxWidth: "max-w-xs",
  },
  compact: {
    button: "h-12 w-12",
    icon: 22,
    pulse: "inset-1",
    cancelIcon: 12,
    errorMaxWidth: "max-w-[14rem]",
  },
} as const;

function formatElapsed(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function DictationButton({
  onTranscript,
  disabled,
  size = "large",
}: Props) {
  const recorder = useVoiceRecorder();
  const sz = SIZE_CLASSES[size];
  const pressStartRef = useRef(0);
  const isHoldingTouchRef = useRef(false);
  // Touch pointer-down is followed by a synthetic click; swallow it so
  // it doesn't re-trigger start() on top of the gesture we already
  // handled in onPointerDown.
  const suppressClickRef = useRef(false);

  useEffect(() => {
    if (recorder.transcript !== null) {
      onTranscript(recorder.transcript);
      recorder.reset();
    }
  }, [recorder.transcript, onTranscript, recorder]);

  const onPointerDown = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (e.pointerType !== "touch") return;
    if (disabled) return;
    if (recorder.state !== "idle" && recorder.state !== "error") return;
    e.preventDefault();
    suppressClickRef.current = true;
    isHoldingTouchRef.current = true;
    pressStartRef.current = Date.now();
    e.currentTarget.setPointerCapture(e.pointerId);
    void recorder.start();
  };

  const onTapClick = () => {
    if (suppressClickRef.current) {
      suppressClickRef.current = false;
      return;
    }
    // Tap-to-toggle for mouse/pen. Click on idle → start; click while
    // recording → stop.
    if (recorder.state === "recording") {
      recorder.stop();
    } else if (recorder.state === "idle" || recorder.state === "error") {
      void recorder.start();
    }
  };

  const endTouchHold = () => {
    if (!isHoldingTouchRef.current) return;
    isHoldingTouchRef.current = false;
    const heldMs = Date.now() - pressStartRef.current;
    if (recorder.state === "recording") {
      if (heldMs < MIN_HOLD_MS) recorder.cancel();
      else recorder.stop();
    } else {
      recorder.cancel();
    }
  };

  const onPointerUp = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (e.pointerType !== "touch") return;
    endTouchHold();
  };

  const onPointerCancel = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (e.pointerType !== "touch") return;
    endTouchHold();
  };

  const supported =
    typeof navigator !== "undefined" &&
    typeof navigator.mediaDevices !== "undefined" &&
    typeof window.MediaRecorder !== "undefined";
  if (!supported) {
    return (
      <button
        type="button"
        disabled
        title="Voice recording isn't supported in this browser"
        className={`inline-flex ${sz.button} items-center justify-center rounded-full border-2 border-din-blue/30 text-din-blue/40 dark:text-din-cream/40`}
        aria-label="Voice recording unsupported"
      >
        <MicOff size={sz.icon} />
      </button>
    );
  }

  if (recorder.state === "uploading") {
    return (
      <button
        type="button"
        disabled
        className={`inline-flex ${sz.button} items-center justify-center rounded-full bg-din-blue text-white dark:bg-din-cream dark:text-din-navy`}
        aria-label="Uploading audio"
      >
        <Loader2 size={sz.icon} className="animate-spin" />
      </button>
    );
  }

  if (
    recorder.state === "recording" ||
    recorder.state === "requesting-permission"
  ) {
    return (
      <div className="flex flex-col items-center gap-1">
        <button
          type="button"
          onClick={() => recorder.stop()}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerCancel}
          disabled={recorder.state !== "recording"}
          className={`relative inline-flex ${sz.button} items-center justify-center rounded-full bg-din-red text-white shadow-lg ring-4 ring-din-red/30 focus:outline-none focus:ring-4 focus:ring-din-red/40 disabled:opacity-60 dark:bg-din-red-soft`}
          aria-label="Stop recording"
          title="Stop recording"
        >
          <span
            className={`absolute ${sz.pulse} animate-pulse rounded-full bg-white/15`}
            aria-hidden="true"
          />
          <Mic size={sz.icon} className="relative" />
        </button>
        <div className="flex items-center gap-2 text-xs font-medium text-din-red dark:text-din-red-soft">
          <span className={recorder.nearMax ? "font-bold" : ""}>
            {formatElapsed(recorder.elapsedSec)}
          </span>
          <button
            type="button"
            onClick={() => recorder.cancel()}
            className="inline-flex items-center justify-center text-din-red/70 hover:text-din-red dark:text-din-red-soft/70 dark:hover:text-din-red-soft"
            aria-label="Cancel recording"
          >
            <X size={sz.cancelIcon} />
          </button>
        </div>
      </div>
    );
  }

  // idle or error
  return (
    <div className="flex flex-col items-center gap-1">
      <button
        type="button"
        onClick={onTapClick}
        onPointerDown={onPointerDown}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerCancel}
        disabled={disabled}
        title="Tap to dictate (or press and hold on touch). DESS will speak the reply."
        className={`inline-flex ${sz.button} items-center justify-center rounded-full bg-din-blue text-white shadow-lg transition hover:bg-din-blue-dark focus:outline-none focus:ring-4 focus:ring-din-gold/40 active:scale-95 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-din-cream dark:text-din-navy dark:hover:bg-din-cream/85`}
        aria-label="Start dictation"
      >
        <Mic size={sz.icon} />
      </button>
      {recorder.error ? (
        <span
          className={`${sz.errorMaxWidth} text-center text-xs text-din-red dark:text-din-red-soft`}
          role="alert"
        >
          {recorder.error}
        </span>
      ) : null}
    </div>
  );
}
