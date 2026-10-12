// The TypeScript parsers read the same sample pages as the Python tests,
// so both versions must agree on every field.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { normalizePropertyType, parseDanishNumber, parseRange } from "../netlify/lib/normalize";
import { parseEdc, parserFor, parseSavills } from "../netlify/lib/parsers";

const fixture = (name: string) => readFileSync(join(__dirname, "..", "tests", "fixtures", name), "utf8");

const EDC_URL =
  "https://www.edc.dk/erhverv/erhvervslejemaal/kontor/1552-koebenhavn-v/vester-voldgade-104/114058334/";
const SAVILLS_URL =
  "https://search.savills.com/dk/da/ejendomsoplysninger/dff8a9c0-92ab-4c6e-afce-f1d7e327c393";

describe("normalize", () => {
  it.each([
    ["382.700 kr./år", 382700],
    ["1.281 m²", 1281],
    ["2.150,50 kr.", 2150.5],
    ["Afregnes overfor forsyningsselskabet", null],
  ])("parseDanishNumber(%s)", (text, expected) => {
    expect(parseDanishNumber(text)).toBe(expected);
  });

  it("parses ranges, including invisible characters", () => {
    expect(parseRange("601 til 1.281 m²")).toEqual([601, 1281]);
    expect(parseRange("Udbudspris 450.750 kr. -​ 1.402.695 kr.")).toEqual([450750, 1402695]);
    expect(parseRange("")).toEqual([null, null]);
  });

  it("maps Danish property types", () => {
    expect(normalizePropertyType("Kontor")).toBe("office");
    expect(normalizePropertyType("lager-produktion")).toBe("warehouse_industrial");
    expect(normalizePropertyType("Butik")).toBe("retail");
    expect(normalizePropertyType("")).toBe("other");
  });
});

describe("EDC", () => {
  it("reads every field from a listing page", () => {
    expect(parseEdc(fixture("edc_listing_sample.html"), EDC_URL)).toEqual({
      source: "edc",
      source_id: "114058334",
      url: EDC_URL,
      address: "Vester Voldgade 104 2 TV",
      zip_code: "1552",
      city: "København V",
      property_type: "office",
      size_m2_min: 178,
      size_m2_max: 178,
      yearly_rent_min: 382700,
      yearly_rent_max: 382700,
      rent_per_m2: 2150,
      status: "available",
    });
  });

  it("detects a rented listing and ignores the similar-listings widget", () => {
    const url =
      "https://www.edc.dk/erhverv/erhvervslejemaal/lager-produktion/6230-roedekro/kometvej-10/62001228/";
    const listing = parseEdc(fixture("edc_rented_sample.html"), url)!;
    expect(listing.status).toBe("rented");
    expect(listing.size_m2_min).toBe(1250);
    expect(listing.yearly_rent_min).toBeNull();
    expect(listing.property_type).toBe("warehouse_industrial");
  });
});

describe("Savills", () => {
  it("reads a listing page without picking up the site menu or footer", () => {
    expect(parseSavills(fixture("savills_detail_sample.html"), SAVILLS_URL)).toEqual({
      source: "savills",
      source_id: "dff8a9c0-92ab-4c6e-afce-f1d7e327c393",
      url: SAVILLS_URL,
      address: "Rygårds Allé 133",
      zip_code: "2900",
      city: "Hellerup",
      property_type: "office",
      size_m2_min: 696,
      size_m2_max: 696,
      yearly_rent_min: 1009200,
      yearly_rent_max: 1009200,
      rent_per_m2: 1450,
      status: "available",
    });
  });
});

describe("parserFor", () => {
  it("picks the right parser by URL", () => {
    expect(parserFor(EDC_URL)).toBe(parseEdc);
    expect(parserFor(SAVILLS_URL)).toBe(parseSavills);
    expect(parserFor("https://www.edc.dk/alle-boliger/villa/7600-struer/granvej-8/70501996/")).toBeNull();
    expect(parserFor("https://example.com/")).toBeNull();
  });
});
