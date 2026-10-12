// TypeScript port of src/erhvervsradar/normalize.py, used by the Netlify function.
// Both versions are tested against the same sample pages in tests/fixtures/.

const INVISIBLE = /[​‌‍﻿­]/g;
const NUMBER = /\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?/g;

export function cleanText(text: string | null | undefined): string {
  if (!text) return "";
  return text.replace(INVISIBLE, "").replace(/ /g, " ").replace(/\s+/g, " ").trim();
}

/** "382.700 kr./år" -> 382700, "2.150,50" -> 2150.5, "178 m²" -> 178 */
export function parseDanishNumber(text: string | null | undefined): number | null {
  const match = cleanText(text).match(NUMBER);
  if (!match) return null;
  return Number(match[0].replace(/\./g, "").replace(",", "."));
}

/** "601 til 1.281 m²" -> [601, 1281]; "178 m²" -> [178, 178] */
export function parseRange(text: string | null | undefined): [number | null, number | null] {
  const numbers = (cleanText(text).match(NUMBER) ?? [])
    .slice(0, 2)
    .map((n) => parseDanishNumber(n))
    .filter((n): n is number => n !== null);
  if (!numbers.length) return [null, null];
  return [Math.min(...numbers), Math.max(...numbers)];
}

export const toInt = (value: number | null): number | null =>
  value === null ? null : Math.round(value);

const PROPERTY_TYPES: Record<string, string[]> = {
  office: ["kontor", "klinik", "kontorhotel", "coworking"],
  warehouse_industrial: ["lager", "produktion", "logistik", "industri", "værksted"],
  retail: ["butik", "detail", "retail", "showroom"],
  hospitality: ["hotel", "restaurant", "cafe"],
  mixed: ["blandet"],
};

export function normalizePropertyType(raw: string | null | undefined): string {
  const text = cleanText(raw).toLowerCase();
  for (const [normalized, keywords] of Object.entries(PROPERTY_TYPES)) {
    if (keywords.some((k) => text.includes(k))) return normalized;
  }
  return "other";
}
