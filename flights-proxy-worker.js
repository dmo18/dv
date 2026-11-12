/*
 * Flight feed proxy for flights.html (Cloudflare Worker, free plan is enough).
 *
 * Why: the free ADS-B feeds (adsb.lol, adsb.fi, airplanes.live) no longer send
 * CORS headers, so a page on GitHub Pages cannot read them directly. This Worker
 * fetches the feed server side and adds the CORS header.
 *
 * Deploy (about 2 minutes):
 *   1. https://dash.cloudflare.com -> Workers & Pages -> Create -> Create Worker
 *   2. Name it (for example "flights-proxy"), click Deploy, then "Edit code"
 *   3. Replace the code with this whole file, click Deploy
 *   4. Copy the URL, e.g. https://flights-proxy.<you>.workers.dev
 *   5. Put it in FEED_PROXY_URL in flights.html (or open flights.html?proxy=<url>)
 *
 * Only one request shape is accepted, so it cannot be used as an open proxy:
 *   GET /point/<lat>/<lon>/<radiusNm>
 */

const NUM = "(-?\\d{1,3}(?:\\.\\d+)?)";
const POINT_RE = new RegExp(`^/point/${NUM}/${NUM}/${NUM}/?$`);
const UPSTREAM_TIMEOUT_MS = 4000;

function upstreams(lat, lon, r) {
  return [
    ["adsb.lol",        `https://api.adsb.lol/v2/point/${lat}/${lon}/${r}`],
    ["adsb.fi",         `https://opendata.adsb.fi/api/v3/lat/${lat}/lon/${lon}/dist/${r}`],
    ["airplanes.live",  `https://api.airplanes.live/v2/point/${lat}/${lon}/${r}`],
  ];
}

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "*",
  "Access-Control-Expose-Headers": "X-Feed-Source",
};

function json(body, status, extra = {}) {
  return new Response(typeof body === "string" ? body : JSON.stringify(body), {
    status,
    headers: { ...CORS, "Content-Type": "application/json", "Cache-Control": "no-store", ...extra },
  });
}

export default {
  async fetch(request) {
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
    if (request.method !== "GET") return json({ error: "GET only" }, 405);

    const m = new URL(request.url).pathname.match(POINT_RE);
    if (!m) return json({ error: "Use /point/<lat>/<lon>/<radiusNm>" }, 400);

    const lat = Number(m[1]), lon = Number(m[2]);
    const r = Math.min(Math.max(Number(m[3]), 0.1), 250);
    if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return json({ error: "Bad coordinates" }, 400);

    const errors = [];
    for (const [name, url] of upstreams(lat, lon, r)) {
      try {
        const res = await fetch(url, {
          headers: { "Accept": "application/json", "User-Agent": "flights-wallboard-proxy/1.0" },
          signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
        });
        if (!res.ok) { errors.push(`${name}: HTTP ${res.status}`); continue; }
        const text = await res.text();
        const data = JSON.parse(text);
        if (!data || !Array.isArray(data.ac || data.aircraft)) { errors.push(`${name}: bad payload`); continue; }
        return json(text, 200, { "X-Feed-Source": name });
      } catch (e) {
        errors.push(`${name}: ${e && e.name === "TimeoutError" ? "timeout" : (e && e.message) || "error"}`);
      }
    }
    return json({ error: "All upstream feeds failed", details: errors }, 502);
  },
};
