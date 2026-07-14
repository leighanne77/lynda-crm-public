import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { users } from "../api/client";
import { ColorModeToggle } from "../components/ColorModeToggle";
import { DinLogo } from "../components/DinLogo";

/**
 * Welcome / intro screen — Feature 0 from the PRD.
 * Shown once after first login. "Let's get to work" dismisses,
 * marks intro_seen on the server, and routes to home.
 *
 * Copy is the DIN-approved intro that introduces DESS (the system),
 * lays out the three funds, names the privacy enforcement model, and
 * lists the three-person team plus the system-role vs functional-role
 * distinction.
 */
export default function Intro() {
  const navigate = useNavigate();
  const [dismissing, setDismissing] = useState(false);

  const dismiss = async () => {
    setDismissing(true);
    try {
      await users.markIntroSeen();
    } catch {
      // Even if the server call fails, route to home — the worst case
      // is they see the intro again next session, not a broken app.
    }
    navigate("/", { replace: true });
  };

  return (
    <div className="flex min-h-screen flex-col bg-white dark:bg-din-navy">
      <header className="flex items-center justify-end p-3">
        <ColorModeToggle />
      </header>

      <main className="flex flex-1 flex-col items-center px-6 py-8">
        <div className="w-full max-w-2xl">
          <div className="text-center">
            <DinLogo width={200} className="mx-auto" />
            <div className="din-gold-rule mx-auto mt-3 max-w-xs" />
          </div>

          <h1 className="mt-10 text-center">Welcome to the DIN team portal.</h1>

          <div className="mt-8 space-y-4 text-base leading-relaxed">
            <p>I&apos;m DESS, the DIN Team System.</p>

            <p>
              This is where we work — and what we&apos;re building here matters.
            </p>

            <p>
              DIN — the Dual-Use Investor Network — exists to find and fund the
              best of American manufacturing and energy, right now, for what
              this country needs most. That means three things. We serve
              immediate defense needs. We rebuild America&apos;s capacity to
              produce what is needed to defend America and her allies. And by
              2040, we displace China as the world&apos;s dominant industrial
              power.
            </p>

            <p>
              We run three funds. Critical Minerals. Maritime. Energy. Each one
              targets a gap that matters — gaps that, if left unfilled, leave
              this country exposed.
            </p>

            <p>
              This tool is how we stay aligned, move fast, and stay focused on
              what matters.
            </p>

            <p>
              Privacy in DESS is enforced at the database layer, not the UI.
              The UI surfaces these rules visually; it never substitutes for
              them. Every query is filtered by{" "}
              <code className="font-mono text-sm">current_user_id</code> before
              results are returned.
            </p>
          </div>

          <h2 className="mt-10 text-center text-sm font-bold uppercase tracking-wide">
            Three-person team
          </h2>

          <div className="mt-4 overflow-hidden rounded border border-din-navy/20 dark:border-din-cream/20">
            <table className="w-full text-sm">
              <thead className="bg-din-navy/5 dark:bg-din-cream/5">
                <tr>
                  <th className="px-3 py-2 text-left font-semibold">Name</th>
                  <th className="px-3 py-2 text-left font-semibold">
                    System role
                  </th>
                  <th className="px-3 py-2 text-left font-semibold">
                    Functional role
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr className="border-t border-din-navy/10 dark:border-din-cream/10">
                  <td className="px-3 py-2">Alex Rivera</td>
                  <td className="px-3 py-2">admin</td>
                  <td className="px-3 py-2">
                    AI Tools and Fund Partner, DESS System Admin
                  </td>
                </tr>
                <tr className="border-t border-din-navy/10 dark:border-din-cream/10">
                  <td className="px-3 py-2">Sam Chen Chang</td>
                  <td className="px-3 py-2">member</td>
                  <td className="px-3 py-2">
                    Fund Strategy, Industry Lead and Fund Partner
                  </td>
                </tr>
                <tr className="border-t border-din-navy/10 dark:border-din-cream/10">
                  <td className="px-3 py-2">Jordan Blake Richmond</td>
                  <td className="px-3 py-2">member</td>
                  <td className="px-3 py-2">
                    Investor Relations, Government Relations Lead and Fund
                    Partner
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <p className="mt-4 text-sm leading-relaxed">
            System role gates technical access — admin can reach{" "}
            <code className="font-mono">/admin/audit</code> and (Phase 2+)
            approve cross-team change requests. Functional role describes what
            the person does and surfaces in DESS&apos;s conversational copy.
          </p>

          <div className="mt-12 text-center">
            <button
              type="button"
              onClick={dismiss}
              disabled={dismissing}
              className="inline-flex h-12 items-center justify-center rounded bg-din-red px-10 text-sm font-bold uppercase tracking-wide text-white hover:bg-din-red-soft focus:outline-none focus:ring-2 focus:ring-din-gold disabled:cursor-not-allowed disabled:opacity-60"
            >
              {dismissing ? "…" : "Let's get to work"}
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}
