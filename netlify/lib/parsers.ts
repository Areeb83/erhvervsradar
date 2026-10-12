// Turns one listing page into a Listing. Same approach as the Python adapters:
// read the visible text in order and find values by the labels a human reads.

import * as cheerio from "cheerio/slim"; // parsing only, no network or encoding extras
import type { AnyNode } from "domhandler";
import { cleanText, normalizePropertyType, parseDanishNumber, parseRange, toInt } from "./normalize";

export interface Listing {
  source: "edc" | "savills";
  source_id: string;
  url: string;
  address: string | null;
  zip_code: string | null;
  city: string | null;
  property_type: string;
  size_m2_min: number | null;
  size_m2_max: number | null;
  yearly_rent_min: number | null;
  yearly_rent_max: number | null;
  rent_per_m2: number | null;
  status: "available" | "rented";
}

// https://www.edc.dk/erhverv/erhvervslejemaal/kontor/1552-koebenhavn-v/vester-voldgade-104/114058334/
export const EDC_URL =
  /^https:\/\/(?:www\.)?edc\.dk\/erhverv\/erhvervslejemaal\/(?<type>[^/]+)\/(?<zip>\d{4})-[^/]+\/[^/]+\/(?<id>\d+)\/?$/;
// https://search.savills.com/dk/da/ejendomsoplysninger/dff8a9c0-92ab-4c6e-afce-f1d7e327c393
export const SAVILLS_URL =
  /^https:\/\/search\.savills\.com\/dk\/[a-z]{2}\/ejendomsoplysninger\/(?<id>[0-9a-f-]{36})\/?$/i;

const SKIP_TAGS = new Set(["script", "style", "noscript", "template", "svg", "head"]);

/** Every piece of visible text on the page, in reading order. */
export function visibleTexts(html: string): { texts: string[]; title: string } {
  const $ = cheerio.load(html);
  const title = cleanText($("title").first().text());
  const texts: string[] = [];
  const walk = (nodes: AnyNode[]) => {
    for (const node of nodes) {
      if (node.type === "text") {
        const text = cleanText((node as unknown as { data: string }).data);
        if (text) texts.push(text);
      } else if (node.type === "tag" && !SKIP_TAGS.has(node.name)) {
        walk(node.children);
      }
    }
  };
  walk($.root().children().toArray());
  return { texts, title };
}

/** {"Årlig leje": "382.700 kr./år", ...}: the first value after each label. */
export function valuesAfterLabels(texts: string[], labels: Set<string>): Record<string, string> {
  const found: Record<string, string> = {};
  texts.forEach((text, i) => {
    if (labels.has(text) && !(text in found)) {
      const value = texts.slice(i + 1, i + 3).find((t) => !labels.has(t));
      if (value) found[text] = value;
    }
  });
  return found;
}

function rentPerM2(l: Pick<Listing, "yearly_rent_min" | "size_m2_min" | "size_m2_max">) {
  if (l.yearly_rent_min && l.size_m2_min && l.size_m2_min === l.size_m2_max) {
    return Math.round((l.yearly_rent_min / l.size_m2_min) * 10) / 10;
  }
  return null;
}

// -- EDC ---------------------------------------------------------------------

const EDC_LABELS = new Set([
  "Adresse",
  "Ejendomstype",
  "Etageareal",
  "Areal",
  "Årlig leje",
  "Månedlig leje",
  "Årlig leje pr. m²",
]);
// <title>Vester Voldgade 104 2 TV, Kontor til leje i København V</title>
const EDC_TITLE = /^(?<address>.+?), (?<kind>.+?) til leje i (?<city>.+)$/;

export function parseEdc(html: string, url: string): Listing | null {
  const match = url.match(EDC_URL);
  if (!match?.groups) return null;
  const { texts: all, title } = visibleTexts(html);
  // Stop before the "similar properties" widget so its prices don't leak in.
  const stop = all.indexOf("Lignende ejendomme");
  const texts = stop >= 0 ? all.slice(0, stop) : all;
  const fields = valuesAfterLabels(texts, EDC_LABELS);
  const titleMatch = title.match(EDC_TITLE)?.groups;

  const size = parseDanishNumber(fields["Etageareal"] ?? fields["Areal"]);
  let yearly = parseDanishNumber(fields["Årlig leje"]);
  if (yearly === null && fields["Månedlig leje"]) {
    const monthly = parseDanishNumber(fields["Månedlig leje"]);
    yearly = monthly === null ? null : monthly * 12;
  }

  const listing = {
    source: "edc" as const,
    source_id: match.groups.id,
    url,
    address: fields["Adresse"] ?? titleMatch?.address ?? null,
    zip_code: match.groups.zip,
    city: titleMatch?.city ?? null,
    property_type: normalizePropertyType(fields["Ejendomstype"] ?? match.groups.type),
    size_m2_min: toInt(size),
    size_m2_max: toInt(size),
    yearly_rent_min: toInt(yearly),
    yearly_rent_max: toInt(yearly),
    status: texts.includes("Udlejet") ? ("rented" as const) : ("available" as const),
  };
  return { ...listing, rent_per_m2: rentPerM2(listing) };
}

// -- Savills -----------------------------------------------------------------

// <title>Rygårds Allé 133, 2900 Hellerup | Ejendom til leje | Savills</title>
const SAVILLS_TITLE = /^(?<address>.+?),\s*(?<zip>\d{4})\s+(?<city>[^|]+?)\s*\|/;
const SIZE = /(\d[\d.]*)(?:\s*til\s*(\d[\d.]*))?\s*m²/;
const PRICE = /Udbudspris\s*(\d[\d.]*)\s*kr\.?(?:\s*-\s*(\d[\d.]*)\s*kr\.?)?/;
// Type tags as Savills writes them on the listing itself.
const SAVILLS_TYPES = new Set([
  "Kontor",
  "Klinik",
  "Logistik og industri",
  "Lager",
  "Detail",
  "Butik",
  "Investering",
  "Projekt og Erhvervsgrund",
  "Øvrige ejendomstyper",
]);
// Where the listing ends and site-wide sections begin. Not "Kontakt": the real page
// has a "Kontakt" button between the address and the size and price.
const SAVILLS_END = ["Lokal information", "Erhvervsejendomme"];

export function parseSavills(html: string, url: string): Listing | null {
  const match = url.match(SAVILLS_URL);
  if (!match?.groups) return null;
  const { texts: all, title } = visibleTexts(html);
  const titleMatch = title.match(SAVILLS_TITLE)?.groups;
  const address = titleMatch ? cleanText(titleMatch.address) : null;

  // Only read the listing itself: from its heading to the contact section.
  // The site menu above it says "Kontorejendomme", "Lager & logistik" etc.
  let start = address ? all.findIndex((t) => t.startsWith(address)) : 0;
  if (start < 0) start = 0;
  let end = all.length;
  for (const marker of SAVILLS_END) {
    const i = all.indexOf(marker, start + 1);
    if (i > start && i < end) end = i;
  }
  const texts = all.slice(start, end);
  const joined = texts.join(" ");

  const sizeMatch = joined.match(SIZE);
  const [sizeMin, sizeMax] = sizeMatch ? parseRange(sizeMatch[0]) : [null, null];
  const priceMatch = joined.match(PRICE);
  let [rentMin, rentMax] = priceMatch ? parseRange(priceMatch[0]) : [null, null];
  if (texts.includes("Månedlig leje")) {
    rentMin = rentMin === null ? null : rentMin * 12;
    rentMax = rentMax === null ? null : rentMax * 12;
  }

  const listing = {
    source: "savills" as const,
    source_id: match.groups.id.toLowerCase(),
    url,
    address,
    zip_code: titleMatch?.zip ?? null,
    city: titleMatch ? cleanText(titleMatch.city) : null,
    property_type: normalizePropertyType(texts.find((t) => SAVILLS_TYPES.has(t)) ?? ""),
    size_m2_min: toInt(sizeMin),
    size_m2_max: toInt(sizeMax),
    yearly_rent_min: toInt(rentMin),
    yearly_rent_max: toInt(rentMax),
    status: texts.includes("Udlejet") ? ("rented" as const) : ("available" as const),
  };
  return { ...listing, rent_per_m2: rentPerM2(listing) };
}

/** Which parser handles this URL, if any. */
export function parserFor(url: string) {
  if (EDC_URL.test(url)) return parseEdc;
  if (SAVILLS_URL.test(url)) return parseSavills;
  return null;
}
