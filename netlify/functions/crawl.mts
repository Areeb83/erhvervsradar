// GET /api/crawl?url=<listing url>  ->  JSON CrawlResult
// Crawls one listing per call. The upload page in web/ calls it once per row.

import type { Config } from "@netlify/functions";
import { crawlOne } from "../lib/crawl";

export default async (req: Request): Promise<Response> => {
  const target = new URL(req.url).searchParams.get("url") ?? "";
  const result = await crawlOne(target);

  const headers: Record<string, string> = { "Content-Type": "application/json; charset=utf-8" };
  if (result.status === "ok" || result.status === "not_found") {
    // Cache good answers on Netlify's CDN for an hour, keyed by the listing URL,
    // so many visitors testing the same example sheet don't hit the sites again.
    headers["Netlify-CDN-Cache-Control"] = "public, durable, s-maxage=3600";
    headers["Netlify-Vary"] = "query=url";
    headers["Cache-Control"] = "public, max-age=0, must-revalidate";
  } else {
    headers["Cache-Control"] = "no-store";
  }
  return new Response(JSON.stringify(result), { headers });
};

export const config: Config = {
  path: "/api/crawl",
  method: "GET",
};
