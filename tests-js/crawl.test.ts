// crawlOne() against a fake internet: no real sites are contacted.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import { clearRobotsCache, crawlOne, USER_AGENT } from "../netlify/lib/crawl";

const fixture = (name: string) => readFileSync(join(__dirname, "..", "tests", "fixtures", name), "utf8");
const EDC_URL =
  "https://www.edc.dk/erhverv/erhvervslejemaal/kontor/1552-koebenhavn-v/vester-voldgade-104/114058334/";

type Route = { status?: number; body?: string; redirectTo?: string };

function fakeFetch(routes: Record<string, Route>, seen: Request[] = []) {
  return (async (input: string | URL | Request, init?: RequestInit) => {
    const req = new Request(input, init);
    seen.push(req);
    const route = routes[req.url];
    if (!route) return new Response("not found", { status: 404 });
    if (route.status === -1) throw new TypeError("fetch failed");
    const resp = new Response(route.body ?? "", { status: route.status ?? 200 });
    Object.defineProperty(resp, "url", { value: route.redirectTo ?? req.url });
    return resp;
  }) as typeof fetch;
}

const EDC_ROBOTS = { "https://www.edc.dk/robots.txt": { body: "User-agent: *\nAllow: /\nDisallow: /umbraco" } };

beforeEach(() => clearRobotsCache());

describe("crawlOne", () => {
  it("reads a listing and identifies itself honestly", async () => {
    const seen: Request[] = [];
    const result = await crawlOne(
      EDC_URL + "?utm_source=newsletter",
      fakeFetch({ ...EDC_ROBOTS, [EDC_URL]: { body: fixture("edc_listing_sample.html") } }, seen),
    );
    expect(result.status).toBe("ok");
    expect(result.listing?.yearly_rent_min).toBe(382700);
    expect(seen.at(-1)?.headers.get("user-agent")).toBe(USER_AGENT);
    expect(seen.at(-1)?.url).toBe(EDC_URL); // tracking parameters removed
  });

  it("refuses other sites without making any request", async () => {
    const seen: Request[] = [];
    const result = await crawlOne("https://www.boligsiden.dk/adresse/x", fakeFetch({}, seen));
    expect(result.status).toBe("unsupported_site");
    expect(seen).toHaveLength(0);
  });

  it("rejects text that isn't a URL", async () => {
    expect((await crawlOne("Vester Voldgade 104", fakeFetch({}))).status).toBe("invalid_url");
  });

  it("recognises supported sites but non-listing pages", async () => {
    const result = await crawlOne("https://www.edc.dk/erhverv/", fakeFetch({}));
    expect(result.status).toBe("not_a_listing");
  });

  it("respects robots.txt", async () => {
    const result = await crawlOne(
      EDC_URL,
      fakeFetch({ "https://www.edc.dk/robots.txt": { body: "User-agent: *\nDisallow: /erhverv/" } }),
    );
    expect(result.status).toBe("blocked_by_robots");
  });

  it("treats an unreachable robots.txt as 'do not crawl' (RFC 9309)", async () => {
    const result = await crawlOne(EDC_URL, fakeFetch({ "https://www.edc.dk/robots.txt": { status: 503 } }));
    expect(result.status).toBe("blocked_by_robots");
  });

  it("reports removed listings: 404 or a redirect to a search page", async () => {
    expect((await crawlOne(EDC_URL, fakeFetch({ ...EDC_ROBOTS }))).status).toBe("not_found");
    const redirected = await crawlOne(
      EDC_URL,
      fakeFetch({ ...EDC_ROBOTS, [EDC_URL]: { body: "<html></html>", redirectTo: "https://www.edc.dk/erhverv/" } }),
    );
    expect(redirected.status).toBe("not_found");
  });

  it("reports network and server errors without crashing", async () => {
    expect((await crawlOne(EDC_URL, fakeFetch({ ...EDC_ROBOTS, [EDC_URL]: { status: -1 } }))).status).toBe("error");
    const busy = await crawlOne(EDC_URL, fakeFetch({ ...EDC_ROBOTS, [EDC_URL]: { status: 503 } }));
    expect(busy.status).toBe("error");
    expect(busy.message).toContain("503");
  });
});
