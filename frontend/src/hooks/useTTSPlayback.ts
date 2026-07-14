/**
 * TTS playback hook for DESS's assistant messages.
 *
 * State machine: idle → loading → playing → idle (or error).
 *
 * `play(text)` calls /api/voice/speak with the given text, receives
 * audio/mpeg bytes, plays them via HTMLAudioElement. Calling play()
 * again while audio is playing stops the current playback first.
 * `stop()` halts playback and releases the blob URL.
 *
 * Each play() call hits the endpoint fresh — no per-message cache.
 * ElevenLabs response is small (10-50 KB for typical replies) and a
 * cache would add memory + cleanup complexity that's not worth it
 * for Slice 5. Polish slice if it ever matters.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export type TTSState = "idle" | "loading" | "playing" | "error";

export interface UseTTSPlayback {
  state: TTSState;
  error: string | null;
  play: (text: string) => Promise<void>;
  stop: () => void;
}

export function useTTSPlayback(): UseTTSPlayback {
  const [state, setState] = useState<TTSState>("idle");
  const [error, setError] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const blobUrlRef = useRef<string | null>(null);

  const cleanup = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = "";
      audioRef.current = null;
    }
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current);
      blobUrlRef.current = null;
    }
  }, []);

  // Tear down on unmount so audio doesn't keep playing after a
  // navigation or component remove.
  useEffect(() => cleanup, [cleanup]);

  const stop = useCallback(() => {
    cleanup();
    setState("idle");
  }, [cleanup]);

  const play = useCallback(
    async (text: string) => {
      // If we're mid-playback, stop the current audio first.
      cleanup();
      setError(null);
      setState("loading");

      try {
        const resp = await fetch("/api/voice/speak", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        });
        if (!resp.ok) {
          const body = await resp.text();
          setError(
            resp.status === 503
              ? "Voice mode isn't enabled yet."
              : `TTS failed (HTTP ${resp.status}): ${body.slice(0, 200)}`,
          );
          setState("error");
          return;
        }
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        blobUrlRef.current = url;

        const audio = new Audio(url);
        audioRef.current = audio;
        audio.onended = () => {
          cleanup();
          setState("idle");
        };
        audio.onerror = () => {
          cleanup();
          setError("Audio playback failed.");
          setState("error");
        };

        setState("playing");
        await audio.play();
      } catch (e) {
        setError(e instanceof Error ? e.message : "Network error during TTS");
        setState("error");
      }
    },
    [cleanup],
  );

  return { state, error, play, stop };
}
