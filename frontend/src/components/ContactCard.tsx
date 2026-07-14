import { Lock } from "lucide-react";
import type {
  ContactCardData,
  ExportFilter,
  FlyStatus,
  PrimaryFund,
} from "../api/client";
import { useState } from "react";

import { BrandIcon, type BrandIconName } from "./BrandIcon";
import { CardPatina, type Patina, patinaForContact } from "./CardPatina";
import { ContactCardExpanded } from "./ContactCardExpanded";
import { ContactListExport } from "./ContactListExport";
import { CountryFlag } from "./CountryFlag";
import { countryToFlag } from "./countryToFlag";

/**
 * Fly-status badge — top-right of every card. Half the size of the fund
 * icon (18px vs 36px) so it's a quiet signal, not a competing element.
 * "Unknown" and "Off Fly List" both render nothing; Off Fly List adds
 * the ripped-channel treatment so the two are still distinguishable.
 * Slice 6.11 — renamed "Not Sure Yet" → "Maybe Must Fly" (still dotted)
 * and added "Unknown" (default for new contacts; no plane).
 */
const FLY_STATUS_ICON: Record<FlyStatus, BrandIconName | null> = {
  "Must Fly": "airplane-solid",
  "Fly List": "airplane-outline",
  "Maybe Must Fly": "airplane-dotted",
  Unknown: null,
  "Off Fly List": null,
};

/**
 * Slice 6.11 — when a contact is a CURRENT government employee, the
 * card gets a 3-side fund-colored frame (left + right + bottom). The
 * top stays bare because the existing fund-tab stripe already lives
 * there — adding a fourth side would double-up the top edge.
 */
const GOV_BORDER_COLOR_HEX: Record<PrimaryFund, string> = {
  "Critical Minerals": "#C8202F", // din-red
  Maritime: "#4A6B8A", // din-blue
  Energy: "#E8A82A", // din-gold
  General: "#000000", // true black per Alex Rivera's 2026-05-20 ruling
};
const GOV_BORDER_WIDTH_PX = 3;

/**
 * Inline SVG fractal-noise grain — gives the card a subtle paper texture
 * (Brand Guide §7.3 photography treatment). 3% opacity, mix-blend so it
 * picks up the underlying card color in both light and dark mode.
 */
const GRAIN_BG =
  "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='140' height='140'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0.1 0 0 0 0 0.14 0 0 0 0 0.2 0 0 0 1 0'/></filter><rect width='140' height='140' filter='url(%23n)'/></svg>\")";

/**
 * Stain templates — picked deterministically by `contact.id`. Slot
 * layout (24 slots so we can space out the rarities):
 *   0-2   coffee / tea blobs           (3/24  ≈ 12%)
 *   3-5   water rings, 3 positions     (3/24  ≈ 12%)  — never repeat
 *   6     coffee splatter (rare)       (1/24  ≈ 4%)
 *   7-23  no stain                     (17/24 ≈ 71%)
 * Roughly 30% of cards carry a stain. Water rings come from setting a
 * glass down — a memorable event — so each card always gets a UNIQUE
 * ring position, never the same as another stained card.
 */
const STAIN_TEMPLATES: (string | null)[] = [
  // 0 — coffee blob, upper-right
  "radial-gradient(ellipse 60px 50px at 78% 22%, rgba(99, 60, 26, 0.20), rgba(99, 60, 26, 0.10) 60%, transparent 80%)",
  // 1 — tea blob, lower-left
  "radial-gradient(ellipse 70px 55px at 18% 75%, rgba(120, 80, 40, 0.18), rgba(120, 80, 40, 0.09) 55%, transparent 78%)",
  // 2 — coffee drip streak, top-center
  "radial-gradient(ellipse 80px 30px at 50% 8%, rgba(99, 60, 26, 0.18), transparent 70%)",
  // 3 — WATER RING anchored bottom-right (sweeping arc into upper-left).
  // Dialed lighter again — the meniscus whispers.
  "radial-gradient(circle 420px at 180% 130%, transparent 91%, rgba(74, 107, 138, 0.07) 94%, rgba(74, 107, 138, 0.04) 96.5%, transparent 100%)",
  // 4 — WATER RING anchored top-left (sweeping arc into lower-right)
  "radial-gradient(circle 380px at -70% -40%, transparent 91%, rgba(74, 107, 138, 0.06) 94%, rgba(74, 107, 138, 0.035) 96.5%, transparent 100%)",
  // 5 — TEA RING (smaller than water ring, amber-brown) anchored bottom-
  // left. Same physics — a teacup left on a card — but in a warmer color
  // and a smaller diameter (a teacup is smaller than a water glass).
  "radial-gradient(circle 280px at -40% 130%, transparent 90%, rgba(140, 95, 55, 0.18) 93%, rgba(140, 95, 55, 0.10) 96%, transparent 100%)",
  // 6 — COFFEE SPLATTER: main blob plus four small droplets scattered
  // around it, like a kicked cup. Rare on purpose.
  [
    "radial-gradient(ellipse 50px 38px at 62% 60%, rgba(99,60,26,0.22), rgba(99,60,26,0.10) 60%, transparent 80%)",
    "radial-gradient(circle 5px at 78% 68%, rgba(99,60,26,0.30), transparent 80%)",
    "radial-gradient(circle 3px at 50% 78%, rgba(99,60,26,0.26), transparent 80%)",
    "radial-gradient(circle 4px at 82% 50%, rgba(99,60,26,0.24), transparent 80%)",
    "radial-gradient(circle 3px at 46% 52%, rgba(99,60,26,0.22), transparent 80%)",
    "radial-gradient(circle 2px at 70% 80%, rgba(99,60,26,0.28), transparent 85%)",
  ].join(", "),
  null, // 7
  null, // 8
  null, // 9
  null, // 10
  null, // 11
  null, // 12
  null, // 13
  null, // 14
  null, // 15
  null, // 16
  null, // 17
  null, // 18
  null, // 19
  null, // 20
  null, // 21
  null, // 22
  null, // 23
];

function pickStain(id: number): string | null {
  return STAIN_TEMPLATES[id % STAIN_TEMPLATES.length];
}

/** Days a contact stays "freshly UPDATED" after a real edit. */
const UPDATED_WINDOW_DAYS = 14;

/**
 * If the contact was edited within the last UPDATED_WINDOW_DAYS AND the
 * edit was a real change (updated_at != created_at), return a Typewritten
 * patina that says "UPDATED MM/DD/YY". Otherwise null. The stamp is
 * system-derived — not part of patina_overrides, so the user can't
 * remove it; it expires on its own after 14 days.
 */
function computeUpdatedStamp(
  createdAt: string | undefined,
  updatedAt: string | undefined,
): Patina | null {
  if (!createdAt || !updatedAt) return null;
  const created = new Date(createdAt).getTime();
  const updated = new Date(updatedAt).getTime();
  if (Number.isNaN(created) || Number.isNaN(updated)) return null;
  // Treat anything within 1 second of creation as "not really updated"
  // (handles ORM timestamp jitter).
  if (updated - created < 1000) return null;
  const ageMs = Date.now() - updated;
  const ageDays = ageMs / (1000 * 60 * 60 * 24);
  if (ageDays > UPDATED_WINDOW_DAYS) return null;
  const d = new Date(updated);
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const yy = String(d.getFullYear()).slice(-2);
  return {
    kind: "typewritten",
    text: `UPDATED ${mm}/${dd}/${yy}`,
    color: "rgba(110, 30, 30, 0.65)", // dark red typewriter ribbon
    pos: { top: "8%", left: "30%" },
    rotate: -2,
  };
}

/**
 * Generate a clip-path polygon that genuinely cuts a torn corner off
 * the card box (border included). Six waypoints along the tear edge
 * give it a jagged paper-rip feel rather than a clean diagonal.
 */
function cornerTearClipPath(
  corner: "top-left" | "bottom-left" | "bottom-right",
  size: "tiny" | "medium",
): string {
  const px = size === "tiny" ? 14 : 22;
  // Helper to format points
  const TL = (x: string, y: string) => `${x} ${y}`;
  switch (corner) {
    case "bottom-left":
      // Tear runs from (px, 100%) up-left to (0, 100% - px)
      return `polygon(
        ${TL("0", "0")},
        ${TL("100%", "0")},
        ${TL("100%", "100%")},
        ${TL(`${px}px`, "100%")},
        ${TL(`${px - 2}px`, "calc(100% - 2px)")},
        ${TL(`${px - 4}px`, "calc(100% - 3px)")},
        ${TL(`${px - 6}px`, "calc(100% - 6px)")},
        ${TL(`${px - 8}px`, "calc(100% - 8px)")},
        ${TL(`${px - 10}px`, "calc(100% - 11px)")},
        ${TL(`${px - 12}px`, "calc(100% - 12px)")},
        ${TL("0", `calc(100% - ${px}px)`)}
      )`;
    case "bottom-right":
      // Tear runs from (100% - px, 100%) up-right to (100%, 100% - px)
      return `polygon(
        ${TL("0", "0")},
        ${TL("100%", "0")},
        ${TL("100%", `calc(100% - ${px}px)`)},
        ${TL(`calc(100% - ${px - 12}px)`, "calc(100% - 12px)")},
        ${TL(`calc(100% - ${px - 10}px)`, "calc(100% - 11px)")},
        ${TL(`calc(100% - ${px - 8}px)`, "calc(100% - 8px)")},
        ${TL(`calc(100% - ${px - 6}px)`, "calc(100% - 6px)")},
        ${TL(`calc(100% - ${px - 4}px)`, "calc(100% - 3px)")},
        ${TL(`calc(100% - ${px - 2}px)`, "calc(100% - 2px)")},
        ${TL(`calc(100% - ${px}px)`, "100%")},
        ${TL("0", "100%")}
      )`;
    case "top-left":
      // Tear runs from (px, 0) down-left to (0, px)
      return `polygon(
        ${TL(`${px}px`, "0")},
        ${TL("100%", "0")},
        ${TL("100%", "100%")},
        ${TL("0", "100%")},
        ${TL("0", `${px}px`)},
        ${TL(`${px - 12}px`, `${px - 12}px`)},
        ${TL(`${px - 10}px`, `${px - 11}px`)},
        ${TL(`${px - 8}px`, `${px - 8}px`)},
        ${TL(`${px - 6}px`, `${px - 6}px`)},
        ${TL(`${px - 4}px`, `${px - 3}px`)},
        ${TL(`${px - 2}px`, `${px - 2}px`)}
      )`;
  }
}

/**
 * Rolodex-style contact card. Visual inspiration: a 1940s rotary card
 * file, updated in DIN brand colors. Each card has:
 *   - a fund-colored top stripe (the "tab" peeking up)
 *   - the fund pictogram on the left
 *   - name in display font with a gold rule under it
 *   - title + company in body font
 *   - a small badge row (contact type + private indicator)
 *   - two "spindle holes" centered on the bottom edge (the rolodex
 *     reference — they line up with the spindle in the cardholder)
 *
 * Cards stack vertically on mobile; on wider viewports they could grid
 * but Day 4 keeps it a single column for readability.
 */

/**
 * Each fund has one or more brand pictograms. Cards pick deterministically
 * from the array using `contact.id`, so any given person always shows the
 * same icon, but the set varies across a list to keep things visually fun.
 */
const FUND_META: Record<
  PrimaryFund,
  {
    icons: BrandIconName[];
    tabClass: string;
    iconBgClass: string;
    label: string;
  }
> = {
  "Critical Minerals": {
    icons: ["rare-earth", "pickaxes"],
    tabClass: "bg-din-red",
    iconBgClass: "bg-din-red/10",
    label: "Critical Minerals",
  },
  Maritime: {
    icons: ["anchor"],
    tabClass: "bg-din-blue",
    iconBgClass: "bg-din-blue/10",
    label: "Maritime",
  },
  Energy: {
    icons: ["lightning-bolt"],
    tabClass: "bg-din-gold",
    iconBgClass: "bg-din-gold/15",
    label: "Energy",
  },
  General: {
    icons: ["flag"],
    tabClass: "bg-din-navy",
    iconBgClass: "bg-din-navy/10",
    label: "General",
  },
};

interface ContactCardProps {
  contact: ContactCardData;
  /** When true, the Ex-Gov pill is shown for ex_government === "Yes".
   *  Default is false — the field is for filtering, not surfacing. */
  showExGov?: boolean;
}

export function ContactCard({ contact, showExGov = false }: ContactCardProps) {
  // Phase 2 Slice 6.5 — partial-reveal preview. The contact is private
  // and owned by a teammate; we only have a subset of fields. Render a
  // distinct, stripped-down card that makes the redacted state obvious
  // and points the viewer to the owner. No patina, no headshot, no fly
  // badge — those signal a fleshed-out card and would be misleading
  // here. The owner pill in the corner stays so the user knows who to
  // ask.
  if (contact.is_redacted) {
    return <RedactedContactCard contact={contact} />;
  }

  const fund = FUND_META[contact.primary_fund] ?? FUND_META.General;
  const iconName = fund.icons[contact.id % fund.icons.length];
  const stainBg = pickStain(contact.id);
  // Honor user overrides if set; otherwise fall back to deterministic
  // auto-pick. Empty list = explicit "no patina." Density scales with
  // fly_status: Must Fly gets the most marks (heavy use), Off Fly List
  // gets none (abandoned card).
  const patinaList = patinaForContact(
    contact.id,
    contact.patina_overrides,
    contact.fly_status,
  );
  const isOffFlyList = contact.fly_status === "Off Fly List";
  // Auto-applied "UPDATED MM/DD/YY" stamp — visible for 14 days after a
  // real edit (updated_at != created_at). System-derived, always shows
  // when applicable; not part of patina_overrides and can't be removed
  // by the user — it expires on its own.
  const updatedStamp = computeUpdatedStamp(
    contact.created_at,
    contact.updated_at,
  );
  // If a corner-tear patina is in the list, build a clip-path so the
  // card box itself (border + content) is genuinely cut at that corner.
  const cornerTear = patinaList.find(
    (p): p is Patina & { kind: "cornerTear" } => p.kind === "cornerTear",
  );
  const cardClipPath = cornerTear
    ? cornerTearClipPath(cornerTear.corner, cornerTear.size)
    : undefined;
  // ~20% of cards are "lined" like an index card. Faint enough not to
  // overpower the brand. Independent of stain/patina dimensions.
  // Lined cards (the index-card-in-the-rolodex look) are LP-only.
  // Capital sources get the lined treatment so they read as the working
  // surface where notes, follow-ups, and ask-amounts get scribbled.
  // Slice 6.11 — Potential LPs (prospects) qualify too; they're capital
  // sources being courted and warrant the same working-surface affordance.
  const isLined =
    contact.contact_type === "LP" || contact.contact_type === "Potential LP";

  // Slice 6.11 — current government employees get a 3-side fund-colored
  // frame. Top stays bare (existing fund-tab stripe lives there). We use
  // the inset box-shadow channel rather than a real border so we don't
  // disrupt the card's existing border + sizing math, and so the colored
  // edges still clip cleanly when a corner-tear patina applies.
  const govBorderShadow = contact.is_gov_employee
    ? `inset ${GOV_BORDER_WIDTH_PX}px 0 0 0 ${
        GOV_BORDER_COLOR_HEX[contact.primary_fund]
      }, ` +
      `inset -${GOV_BORDER_WIDTH_PX}px 0 0 0 ${
        GOV_BORDER_COLOR_HEX[contact.primary_fund]
      }, ` +
      `inset 0 -${GOV_BORDER_WIDTH_PX}px 0 0 ${
        GOV_BORDER_COLOR_HEX[contact.primary_fund]
      }, `
    : "";

  return (
    <div
      className="relative flex h-[216px] w-[384px] shrink-0 flex-col overflow-hidden rounded-sm border border-din-blue/20 bg-din-cream-soft text-din-navy shadow-sm transition-shadow duration-150 hover:shadow-md"
      style={{
        // Soft inner vignette — darker at corners, brighter in the
        // middle. Combined with the grain texture this reads as a
        // gently-worn rolodex card without overdoing it.
        boxShadow:
          govBorderShadow +
          "inset 0 0 0 1px rgba(74, 107, 138, 0.05), " +
          "inset 0 0 24px rgba(26, 35, 50, 0.06), " +
          "0 1px 2px 0 rgba(0, 0, 0, 0.05)",
        // When a corner is "torn off," clip-path actually removes that
        // part of the box — border included — so the rip looks real.
        clipPath: cardClipPath,
      }}
    >
      {/* Fund tab — the colored stripe along the top edge */}
      <div className={`h-1.5 w-full ${fund.tabClass}`} aria-hidden="true" />

      {/* Grain overlay — paper texture per Brand Guide §7.3. Inert to
          clicks; mix-blend picks up the underlying card color. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 opacity-[0.03] mix-blend-multiply"
        style={{ backgroundImage: GRAIN_BG }}
      />

      {/* Worn corners — four radial gradients darkening the extreme
          corners. Pushed harder than the original draft so the cards
          read as genuinely handled, not just gently aged. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 opacity-70"
        style={{
          backgroundImage: [
            "radial-gradient(circle at 0% 0%, rgba(26,35,50,0.32), transparent 26%)",
            "radial-gradient(circle at 100% 0%, rgba(26,35,50,0.30), transparent 28%)",
            "radial-gradient(circle at 0% 100%, rgba(26,35,50,0.34), transparent 30%)",
            "radial-gradient(circle at 100% 100%, rgba(26,35,50,0.28), transparent 24%)",
          ].join(", "),
        }}
      />

      {/* Stain — coffee/tea/water, picked deterministically by id. Roughly
          a third of cards get one. mix-blend-multiply so the stain takes
          on the underlying card tone in light mode; lighter blend in dark. */}
      {stainBg ? (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 mix-blend-multiply"
          style={{ backgroundImage: stainBg }}
        />
      ) : null}

      {/* Index-card lines — faint horizontal rules at 22px intervals.
          ~20% of cards have these; opacity kept low (5-7%) so the brand
          stays primary. Stops 28px from the bottom so it doesn't
          collide with the spindle holes. */}
      {isLined ? (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-0 top-2 bottom-7 opacity-[0.06]"
          style={{
            backgroundImage:
              "repeating-linear-gradient(to bottom, transparent 0, transparent 21px, currentColor 21px, currentColor 22px)",
          }}
        />
      ) : null}

      {/* Fly status — top-right corner, half-size of fund icon. "Off Fly
          List" renders nothing — the empty corner is the signal. */}
      {FLY_STATUS_ICON[contact.fly_status] ? (
        <div className="absolute right-2 top-3" title={contact.fly_status}>
          <BrandIcon
            name={FLY_STATUS_ICON[contact.fly_status]!}
            size={18}
            alt={`Fly status: ${contact.fly_status}`}
          />
        </div>
      ) : null}

      {/* Heroine icon — renders directly below the airplane badge when
          contact_type === "Inspiration." Reserved for honorary/
          inspiration entries in the roster (e.g. an honorary
          inspiration namesake). Same 18px size as the fly badge
          so the pair reads as a stacked corner mark. */}
      {contact.contact_type === "Inspiration" ? (
        <div
          className="absolute right-2 top-9"
          title="Inspiration — the firm's heroine reference"
        >
          <BrandIcon
            name="heroine"
            size={18}
            alt="Inspiration (heroine) contact"
          />
        </div>
      ) : null}

      {/* Ownership pill — top-right corner, stacked under the airplane.
          Only rendered for teammate-owned contacts. Your own contacts
          show no pill (the absence IS the signal: "no badge = mine"),
          which makes other-owned cards visually pop out and removes
          clutter on the cards you already know are yours.
          Always at the same vertical position whether or not the
          airplane is shown (Off Fly List omits the airplane but keeps
          the pill where the airplane would be). If the heroine icon
          is present (Inspiration contacts), the pill slots in below it. */}
      {contact.owner_initials && !contact.is_self_owned ? (
        <div
          className={
            contact.contact_type === "Inspiration"
              ? "absolute right-2 top-14"
              : "absolute right-2 top-9"
          }
          title={
            contact.owner_name
              ? `Owned by ${contact.owner_name}`
              : "Owned by a teammate"
          }
        >
          <span
            className={
              "rounded-full border border-din-navy/25 bg-white px-1.5 " +
              "py-px text-[8px] font-bold uppercase tracking-wide " +
              "text-din-navy/60"
            }
          >
            {contact.owner_initials}
          </span>
        </div>
      ) : null}

      <div className="relative flex flex-1 gap-4 px-4 pt-4 pb-3">
        {/* Headshot — strictly optional. No stand-in when absent; the
            identity block expands to fill the space. */}
        {contact.image_url ? (
          <img
            src={contact.image_url}
            alt={contact.name}
            className="h-14 w-14 shrink-0 rounded object-cover"
            loading="lazy"
          />
        ) : null}

        {/* Identity block — pr-7 reserves space for the absolute fly badge */}
        <div className="min-w-0 flex-1 pr-7">
          <div className="flex items-start justify-between gap-2">
            <h3
              className="truncate font-display text-lg font-bold uppercase tracking-tight text-din-navy"
              title={contact.name}
            >
              {contact.name}
            </h3>
            {contact.is_private ? (
              <Lock
                size={14}
                className="mt-1 shrink-0 text-din-blue"
                aria-label="Private contact"
              />
            ) : null}
          </div>

          <div className="mt-1 h-[2px] w-12 bg-din-gold" />

          {contact.title ? (
            <p className="mt-2 text-sm leading-snug">{contact.title}</p>
          ) : null}
          {contact.company_name ? (
            <p className="flex items-center gap-1.5 text-sm italic text-din-blue">
              <CountryFlag
                code={countryToFlag(contact.country)}
                countryName={contact.country ?? undefined}
              />
              <span className="truncate">{contact.company_name}</span>
            </p>
          ) : null}

          <div className="mt-1.5 flex flex-wrap items-center gap-1 text-[8px] font-bold uppercase tracking-wide">
            <span className="rounded-full border border-din-blue/40 bg-white px-1.5 py-px text-din-blue">
              {/* Slice 6.11 — Potential LP renders as "LP?" so the team
                  can scan for active prospects vs confirmed LPs. */}
              {contact.contact_type === "Potential LP"
                ? "LP?"
                : contact.contact_type}
            </span>
            {showExGov && contact.ex_government === "Yes" ? (
              <span
                className="rounded-full border border-din-red/50 bg-din-red/10 px-1.5 py-px text-din-red"
                title="Ex-government background"
              >
                Ex-Gov
              </span>
            ) : null}
            <span className="inline-flex items-center gap-0.5 rounded-full border border-current/30 px-1.5 py-px opacity-80">
              <BrandIcon name={iconName} size={10} alt="" />
              {fund.label}
            </span>
            {contact.sectors.slice(0, 2).map((s) => (
              <span
                key={s}
                className="rounded-full bg-din-blue/10 px-1.5 py-px normal-case text-din-blue"
              >
                {s}
              </span>
            ))}
          </div>
        </div>
      </div>

      {/* Rolodex spindle holes. Normal: two small light-gray dots.
          Off Fly List: open channels that match the page background,
          so the card looks like it was torn off the spindle, leaving
          the holes ripped through to the bottom edge. */}
      {isOffFlyList ? (
        <RippedChannels />
      ) : (
        <div
          className="flex items-center justify-center gap-8 pb-2"
          aria-hidden="true"
        >
          <span className="block h-2 w-2 rounded-full bg-din-navy/15" />
          <span className="block h-2 w-2 rounded-full bg-din-navy/15" />
        </div>
      )}

      {/* Patina layer — auto-picked (1 max) or user-overridden (up to 3).
          Sits on top so it reads as written-on / stuck-on, not buried
          under the texture. */}
      {patinaList.map((p: Patina, i: number) => (
        <CardPatina key={i} patina={p} />
      ))}

      {/* System-derived UPDATED stamp — independent dimension, expires
          on its own after 14 days. Always shown when applicable, even
          if the user has set patina_overrides. */}
      {updatedStamp ? <CardPatina patina={updatedStamp} /> : null}
    </div>
  );
}

/**
 * "Ripped out" spindle holes — open channels at the bottom edge of the
 * card. Two narrow rectangles with irregular tops (jagged tear lines)
 * filled with the PAGE background color, so they look like actual holes
 * torn through the card all the way to the bottom edge.
 *
 * The page background trick (bg-white only works when
 * the card sits directly on the page background. Today that's true for
 * both /brand/cards and the chat view. If we ever land cards inside a
 * differently-colored container we'll need to revisit.
 */
function RippedChannels() {
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-center gap-8"
    >
      <RippedChannel />
      <RippedChannel flip />
    </div>
  );
}

function RippedChannel({ flip = false }: { flip?: boolean }) {
  // Wider, taller viewBox (20×24) for a more violent tear. The top edge
  // mixes deep gouges with short fibers left behind, and the sides are
  // slightly irregular — no straight lines. Two separate flecks float
  // just above the main channel to suggest bits of paper still hanging.
  return (
    <svg
      width="20"
      height="22"
      viewBox="0 0 20 24"
      className={`fill-white text-white ${flip ? "scale-x-[-1]" : ""}`}
      preserveAspectRatio="none"
    >
      {/* Main torn channel: aggressive zigzag top edge with deep gouges
          (down to y=1) and towering fibers (up to y=8). Side edges wobble
          slightly. Extends to the card bottom. */}
      <path
        d="M0.5,9
           L1.2,5 L2,8 L2.8,3 L3.6,7 L4.5,1.5 L5.2,6
           L6,2 L6.8,6.5 L7.8,1 L8.5,5 L9.5,2.5
           L10.5,7 L11.2,1.8 L12,6 L13,2 L14,7 L14.8,2.5
           L15.8,5 L16.6,1.2 L17.4,6 L18.2,3 L19.2,7 L19.5,9.5
           L19.2,14 L19.6,19 L19,24
           L0.8,24 L0.3,18 L0.6,13 Z"
      />
      {/* Small fleck — a tiny separate bit of card, floating, suggests
          an incomplete tear. */}
      <path d="M4,0.5 L5.5,1 L4.8,2.2 Z" />
      <path d="M14,0 L15.5,0.8 L14.5,2 Z" />
    </svg>
  );
}

interface ContactCardListProps {
  contacts: ContactCardData[];
  truncated?: boolean;
  limit?: number;
  /** Forwarded to each ContactCard. True when the parent search filtered
   *  on ex_government — surfaces the Ex-Gov pill in that context. */
  showExGov?: boolean;
  /** Filter the export endpoints accept — same subset of search params.
   *  Forwarded to ContactListExport so the Export button can reproduce
   *  this list as a Sheet/CSV. Omit to render only the clipboard
   *  affordances. */
  exportFilter?: ExportFilter;
}

export function ContactCardList({
  contacts,
  truncated,
  limit,
  showExGov,
  exportFilter,
}: ContactCardListProps) {
  // Empty result — show explicit feedback rather than nothing. The user
  // searched for something; tell them no contacts matched.
  if (contacts.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-din-blue/30 bg-din-cream-soft/50 px-4 py-3 text-sm italic text-din-blue">
        No contacts matched. Try &ldquo;show me everyone&rdquo; to see
        what&apos;s there, or broaden your filter.
      </div>
    );
  }
  // Slice 6.8 — double-click any non-redacted card to open the
  // expanded view (full notes + change log). Redacted cards skip the
  // affordance entirely since there's nothing extra to show beyond
  // what the inline tile already does. Enter when focused opens the
  // same modal for keyboard / voice-first users.
  const [expanded, setExpanded] = useState<ContactCardData | null>(null);

  return (
    <div className="space-y-3">
      <ContactListExport contacts={contacts} exportFilter={exportFilter} />
      {contacts.map((c) =>
        c.is_redacted ? (
          <ContactCard key={c.id} contact={c} showExGov={showExGov} />
        ) : (
          <div
            key={c.id}
            role="button"
            tabIndex={0}
            title="Double-click to expand (notes + change log)"
            onDoubleClick={() => setExpanded(c)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                setExpanded(c);
              }
            }}
            className="cursor-pointer rounded-sm focus:outline-none focus:ring-2 focus:ring-din-gold/50"
          >
            <ContactCard contact={c} showExGov={showExGov} />
          </div>
        ),
      )}
      {truncated && limit ? (
        <p className="text-center text-xs italic opacity-60">
          Showing first {limit} — narrow your search to see more.
        </p>
      ) : null}
      {expanded ? (
        <ContactCardExpanded
          contact={expanded}
          onClose={() => setExpanded(null)}
        />
      ) : null}
    </div>
  );
}

/**
 * Phase 2 Slice 6.5 — redacted-preview card for a teammate's private
 * contact. Same overall size as a regular ContactCard so the layout
 * doesn't shift when redacted rows appear mid-list, but the contents
 * are stripped:
 *   - name is replaced with a lock icon + "Private contact"
 *   - only the fields the owner explicitly revealed are shown
 *   - the owner's initials pill sits in the corner so the viewer
 *     knows whom to ask for details
 * No patina, no headshot, no fly badge — those would signal a
 * fleshed-out card and mislead the reader.
 */
function RedactedContactCard({ contact }: { contact: ContactCardData }) {
  const initials = contact.owner_initials ?? "?";
  const ownerName = contact.owner_name ?? "a teammate";
  return (
    <div
      className="relative flex h-[216px] w-[384px] shrink-0 flex-col overflow-hidden rounded-sm border border-dashed border-din-navy/30 bg-din-cream-soft/60 text-din-navy/80 shadow-sm"
      title={`Private contact owned by ${ownerName}. Ask them for details.`}
    >
      {/* Owner initials pill — top-right, mirrors the regular card. */}
      <div className="absolute right-2 top-3">
        <span className="rounded-full border border-din-navy/25 bg-white px-1.5 py-px text-[8px] font-bold uppercase tracking-wide text-din-navy/60">
          {initials}
        </span>
      </div>

      <div className="relative flex flex-1 flex-col gap-3 px-4 pt-4 pb-3">
        <div className="flex items-center gap-2">
          <Lock
            size={16}
            className="shrink-0 text-din-blue"
            aria-hidden="true"
          />
          <h3 className="truncate font-display text-base font-bold uppercase tracking-tight text-din-navy/70">
            Private contact
          </h3>
        </div>
        <div className="h-[2px] w-12 bg-din-gold/60" />

        {/* Revealed fields — only the ones the owner opted in to share */}
        <div className="flex flex-col gap-1 text-sm">
          {contact.company_name ? (
            <p className="flex items-center gap-1.5 italic text-din-blue">
              <CountryFlag
                code={countryToFlag(contact.country)}
                countryName={contact.country ?? undefined}
              />
              <span>{contact.company_name}</span>
            </p>
          ) : null}
          {contact.primary_fund ? (
            <p className="text-xs uppercase tracking-wide opacity-70">
              {contact.primary_fund} fund
            </p>
          ) : null}
          {contact.country ? (
            <p className="text-xs opacity-70">{contact.country}</p>
          ) : null}
          {contact.contact_type ? (
            <p className="text-xs uppercase tracking-wide opacity-70">
              {contact.contact_type}
            </p>
          ) : null}
          {contact.sectors && contact.sectors.length > 0 ? (
            <div className="mt-1 flex flex-wrap gap-1">
              {contact.sectors.slice(0, 4).map((s) => (
                <span
                  key={s}
                  className="rounded-full bg-din-blue/10 px-1.5 py-px text-[8px] font-bold uppercase tracking-wide text-din-blue"
                >
                  {s}
                </span>
              ))}
            </div>
          ) : null}
        </div>

        <p className="mt-auto text-xs italic text-din-navy/55">
          Ask {ownerName} for details.
        </p>
      </div>
    </div>
  );
}
