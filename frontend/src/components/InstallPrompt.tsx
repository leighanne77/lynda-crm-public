import { useEffect, useState } from "react";

/**
 * In-app "install to your phone" prompt for the PWA.
 *
 * The app is already installable (manifest + service worker); this just makes
 * it discoverable so users don't have to know the steps.
 *   - Android / Chromium: captures the `beforeinstallprompt` event and offers a
 *     one-tap Install button.
 *   - iOS / Safari: never fires that event, so we show the manual
 *     "Share -> Add to Home Screen" instructions instead.
 * Hidden when already installed (standalone) or after the user dismisses it.
 */

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

const DISMISS_KEY = "dess-install-dismissed";

function isStandalone(): boolean {
  return (
    window.matchMedia("(display-mode: standalone)").matches ||
    // iOS Safari uses a non-standard navigator.standalone flag.
    (window.navigator as unknown as { standalone?: boolean }).standalone === true
  );
}

function isIOS(): boolean {
  const ua = window.navigator.userAgent;
  const iPhoneiPad = /iphone|ipad|ipod/i.test(ua);
  // iPadOS 13+ reports as "MacIntel"; detect it via touch support.
  const iPadOS =
    window.navigator.platform === "MacIntel" && window.navigator.maxTouchPoints > 1;
  return iPhoneiPad || iPadOS;
}

export default function InstallPrompt() {
  const [deferred, setDeferred] = useState<BeforeInstallPromptEvent | null>(null);
  const [mode, setMode] = useState<"android" | "ios" | null>(null);

  useEffect(() => {
    if (isStandalone()) return; // already installed
    if (localStorage.getItem(DISMISS_KEY)) return; // user dismissed earlier

    const onBeforeInstall = (e: Event) => {
      // Stop Chrome's mini-infobar; we surface our own banner instead.
      e.preventDefault();
      setDeferred(e as BeforeInstallPromptEvent);
      setMode("android");
    };
    window.addEventListener("beforeinstallprompt", onBeforeInstall);

    // iOS gets manual instructions immediately (no install event exists there).
    if (isIOS()) setMode("ios");

    const onInstalled = () => setMode(null);
    window.addEventListener("appinstalled", onInstalled);

    return () => {
      window.removeEventListener("beforeinstallprompt", onBeforeInstall);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  if (!mode) return null;

  const dismiss = () => {
    localStorage.setItem(DISMISS_KEY, "1");
    setMode(null);
  };

  const install = async () => {
    if (!deferred) return;
    await deferred.prompt();
    await deferred.userChoice;
    setDeferred(null);
    setMode(null);
  };

  return (
    <div className="fixed inset-x-0 bottom-0 z-50 flex justify-center px-3 pb-[env(safe-area-inset-bottom)]">
      <div className="mb-3 flex w-full max-w-md items-center gap-3 rounded-2xl border border-din-gold/40 bg-din-navy px-4 py-3 text-din-cream shadow-lg">
        <img
          src="/icons/pwa-192.png"
          alt=""
          className="h-9 w-9 shrink-0 rounded-lg"
        />
        <div className="min-w-0 flex-1 text-sm leading-snug">
          {mode === "android" ? (
            <span>
              Install <strong>DESS</strong> on your phone for one-tap access.
            </span>
          ) : (
            <span>
              Install <strong>DESS</strong>: tap <strong>Share</strong>, then{" "}
              <strong>Add to Home Screen</strong>.
            </span>
          )}
        </div>
        {mode === "android" && (
          <button
            onClick={install}
            className="shrink-0 rounded-lg bg-din-gold px-3 py-1.5 text-sm font-semibold text-din-navy hover:bg-din-gold-soft"
          >
            Install
          </button>
        )}
        <button
          onClick={dismiss}
          aria-label="Dismiss install prompt"
          className="shrink-0 rounded-lg px-2 py-1 text-din-cream/70 hover:text-din-cream"
        >
          ✕
        </button>
      </div>
    </div>
  );
}
