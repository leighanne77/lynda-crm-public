/**
 * Canonical country-name → ISO 3166-1 alpha-2 code (lowercase) mapping.
 *
 * The contact.country field is free-text, but the chat system prompt
 * tells Goddess to use a fixed set of canonical names (e.g. "United
 * States", "Saudi Arabia", "Canada"). This map mirrors that grammar.
 *
 * Unknown country names return null — the CountryFlag component
 * handles null gracefully (renders nothing), so a contact in a
 * country we haven't yet added a flag for simply shows no flag.
 *
 * To add a new country:
 *   1. Append the ISO code to scripts/fetch_country_flags.py
 *      COUNTRY_CODES and re-run it (downloads the SVG).
 *   2. Run `make sync-country-codes` to refresh countryCodes.ts.
 *   3. Add the canonical name(s) → code mapping below.
 */

import type { CountryCode } from "./countryCodes";

const CANONICAL_TO_ISO: Record<string, CountryCode> = {
  // North America
  "United States": "us",
  Canada: "ca",
  Mexico: "mx",
  // Europe
  "United Kingdom": "gb",
  France: "fr",
  Germany: "de",
  Italy: "it",
  Spain: "es",
  Netherlands: "nl",
  Switzerland: "ch",
  Sweden: "se",
  "European Union": "european_union",
  // Middle East
  "Saudi Arabia": "sa",
  "United Arab Emirates": "ae",
  Qatar: "qa",
  Kuwait: "kw",
  // Asia-Pacific
  Japan: "jp",
  China: "cn",
  "South Korea": "kr",
  Singapore: "sg",
  Australia: "au",
  "New Zealand": "nz",
  India: "in",
  "Hong Kong": "hk",
  Taiwan: "tw",
  // Latin America
  Brazil: "br",
};

/**
 * Look up a flag code for a canonical country name. Case-insensitive on
 * the input — matches Goddess's grammar even if she returns "united
 * states" instead of "United States". Returns null if no flag is
 * catalogued for the country (CountryFlag renders nothing).
 */
export function countryToFlag(
  country: string | null | undefined,
): CountryCode | null {
  if (!country) return null;
  const trimmed = country.trim();
  if (!trimmed) return null;
  // Exact canonical match first.
  if (trimmed in CANONICAL_TO_ISO) return CANONICAL_TO_ISO[trimmed];
  // Case-insensitive fallback so "united states" still maps.
  const lower = trimmed.toLowerCase();
  for (const [name, code] of Object.entries(CANONICAL_TO_ISO)) {
    if (name.toLowerCase() === lower) return code;
  }
  return null;
}
