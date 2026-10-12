// Crawl exactly one URL: check it's allowed, download it politely, parse it.
// The browser calls this once per spreadsheet row, so each call stays well
// under Netlify's ~10 second function limit.

import robotsParser from "robots-parser";
import { type Listing, parserFor } from "./parsers";

export const USER_AGENT =
  "ErhvervsradarBot/0.2 (+https://github.com/Areeb83/erhvervsradar; " +
  "student portfolio project; contact: areebabbasx@gmail.com)";

// Only these sites are crawled. This also stops anyone from using the
// function as an open proxy to fetch arbitrary pages.
export const ALLOWED_HOSTS = new Set(["www.edc.dk", "edc.dk", "search.savills.com"]);

export type CrawlStatus =
  | "ok" // listing read
  | "invalid_url" // not a URL at all
  | "unsupported_site" // a site we don't crawl
  | "not_a_listing" // right site, but not a listing page
  | "not_found" // listing page no longer exists
  | "blocked_by_robots" // the site's robots.txt asks us not to fetch it
  | "error"; // network or server problem; worth retrying later

export interface CrawlResult {
  status: CrawlStatus;
  url: string;
  message?: string;
  listing?: Listing;
  checked_at: string;
}

type FetchFn = typeof fetch;
type Robots = { isAllowed(url: string, ua?: string): boolean | undefined };

const robotsCache = new Map<string, { robots: Robots | "allow_all" | "deny_all"; expires: number }>();
const ROBOTS_TTL_MS = 60 * 60 * 1000;
const TIMEOUT_MS = 8000;

/** RFC 9309: a missing robots.txt (4xx) allows everything; an unreachable one allows nothing. */
async function robotsAllows(url: URL, fetchFn: FetchFn): Promise<boolean> {
  const cached = robotsCache.get(url.host);
  let entry = cached && cached.expires > Date.now() ? cached.robots : undefined;
  if (!entry) {
    const robotsUrl = `${url.protocol}//${url.host}/robots.txt`;
    try {
      const resp = await fetchFn(robotsUrl, {
        headers: { "User-Agent": USER_AGENT },
        signal: AbortSignal.timeout(TIMEOUT_MS),
      });
      if (resp.ok) entry = robotsParser(robotsUrl, await resp.text()) as Robots;
      else entry = resp.status >= 400 && resp.status < 500 ? "allow_all" : "deny_all";
    } catch {
      entry = "deny_all";
    }
    robotsCache.set(url.host, { robots: entry, expires: Date.now() + ROBOTS_TTL_MS });
  }
  if (entry === "allow_all") return true;
  if (entry === "deny_all") return false;
  return entry.isAllowed(url.href, USER_AGENT) !== false;
}

export function clearRobotsCache() {
  robotsCache.clear();
}

export async function crawlOne(raw: string, fetchFn: FetchFn = fetch): Promise<CrawlResult> {
  const checked_at = new Date().toISOString();
  const done = (status: CrawlStatus, message?: string, listing?: Listing): CrawlResult => ({
    status,
    url: raw,
    checked_at,
    ...(message ? { message } : {}),
    ...(listing ? { listing } : {}),
  });

  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    return done("invalid_url", "This is not a valid web address.");
  }
  url.protocol = "https:";
  url.hash = "";
  url.search = ""; // tracking parameters like ?utm_source=… are not part of the listing

  if (!ALLOWED_HOSTS.has(url.host)) {
    return done("unsupported_site", "Only edc.dk and search.savills.com listings are supported.");
  }
  if (!parserFor(url.href)) {
    return done("not_a_listing", "This page is on a supported site, but it isn't a single listing.");
  }
  if (!(await robotsAllows(url, fetchFn))) {
    return done("blocked_by_robots", "The site's robots.txt asks crawlers not to fetch this page.");
  }

  let resp: Response;
  try {
    resp = await fetchFn(url.href, {
      headers: { "User-Agent": USER_AGENT, "Accept-Language": "da,en;q=0.8" },
      redirect: "follow",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch (err) {
    const timedOut = err instanceof Error && err.name === "TimeoutError";
    return done("error", timedOut ? "The site took too long to answer." : "Could not reach the site.");
  }

  if (resp.status === 404 || resp.status === 410) {
    return done("not_found", "The listing no longer exists.");
  }
  if (!resp.ok) {
    return done("error", `The site answered with HTTP ${resp.status}.`);
  }

  // Sites often redirect a removed listing to a search page.
  const finalUrl = resp.url || url.href;
  const parse = parserFor(finalUrl);
  if (!parse) {
    return done("not_found", "The site redirected away from this listing, so it is probably gone.");
  }
  const listing = parse(await resp.text(), finalUrl);
  if (!listing) return done("error", "The page could not be read.");
  return done("ok", undefined, listing);
}
