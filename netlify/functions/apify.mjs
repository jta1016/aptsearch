/**
 * Proxy all Apify API calls server-side so the token is never exposed in the browser.
 * Frontend calls: /api/apify?path=/acts/jta93~aptsearch/runs
 */
export default async (req) => {
  const token = process.env.APIFY_TOKEN;
  if (!token) return new Response('APIFY_TOKEN not set', { status: 500 });

  const url = new URL(req.url);
  const path = url.searchParams.get('path');
  if (!path) return new Response('Missing path param', { status: 400 });

  // Forward any extra query params (except 'path')
  const params = new URLSearchParams();
  for (const [k, v] of url.searchParams) {
    if (k !== 'path') params.set(k, v);
  }
  params.set('token', token);

  const apifyUrl = `https://api.apify.com/v2${path}?${params}`;

  const body = req.method !== 'GET' && req.method !== 'HEAD'
    ? await req.text()
    : undefined;

  const resp = await fetch(apifyUrl, {
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
