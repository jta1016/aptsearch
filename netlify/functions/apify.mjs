/**
 * Proxy all Apify API calls server-side so the token is never exposed in the browser.
 * Frontend calls: /api/apify?path=/acts/jta93~aptsearch/runs
 */
export default async (req) => {
  const token = process.env.APIFY_TOKEN;
  if (!token) return new Response('APIFY_TOKEN not set', { status: 500 });

  const url = new URL(req.url);
  const pathParam = url.searchParams.get('path');
  if (!pathParam) return new Response('Missing path param', { status: 400 });

  // The path param may contain embedded query strings (e.g. /acts/.../runs?limit=20&desc=1).
  // Parse them out and merge into a single clean query string with the token.
  const apifyBase = new URL(`https://api.apify.com/v2${pathParam}`);
  // Merge any query params that were embedded in the path
  const params = new URLSearchParams(apifyBase.search);
  // Forward any extra query params from the original request (except 'path')
  for (const [k, v] of url.searchParams) {
    if (k !== 'path') params.set(k, v);
  }
  params.set('token', token);
  apifyBase.search = params.toString();

  const body = req.method !== 'GET' && req.method !== 'HEAD'
    ? await req.text()
    : undefined;

  const resp = await fetch(apifyBase.href, {
    method: req.method,
    headers: { 'Content-Type': 'application/json' },
    body,
  });

  const text = await resp.text();
  return new Response(text, {
    status: resp.status,
    headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' },
  });
};

export const config = { path: '/api/apify' };
