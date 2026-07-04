import logoNavyUrl from "../assets/brand-icons/din-logo.svg";
import logoCreamUrl from "../assets/brand-icons/din-logo-reversed.svg";

/**
 * The DIN primary logo — the "DIN / Dual-Use Investor Network" wordmark
 * (Global Primary Navy). Used on the login screen, intro screen, and
 * other brand-moment surfaces per Brand_Mobile_Annex.md §2.2.
 *
 * Renders the navy mark in light mode and the reversed-cream mark in
 * dark mode (Tailwind `dark:` variant, driven by the `html.dark` class
 * from colorMode.ts). To update the artwork, replace din-logo.svg
 * (navy) and/or din-logo-reversed.svg (cream) — this component renders
 * them automatically.
 */
interface DinLogoProps {
  /** Visual width in pixels. Height scales proportionally to viewBox. */
  width?: number;
  className?: string;
}

export function DinLogo({ width = 240, className = "" }: DinLogoProps) {
  return (
    <>
      <img
        src={logoNavyUrl}
        alt="DIN — Dual-Use Investor Network"
        width={width}
        className={`select-none dark:hidden ${className}`}
        draggable={false}
      />
      <img
        src={logoCreamUrl}
        alt="DIN — Dual-Use Investor Network"
        width={width}
        className={`hidden select-none dark:block ${className}`}
        draggable={false}
      />
    </>
  );
}
