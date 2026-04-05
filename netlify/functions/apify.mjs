/**
 * Proxy all Apify API calls server-side so the token is never exposed in the browser.
 * Frontend calls: /api/apify?path=/acts/comfy-classmate~aptsearch/runs
 */
export default async (req) => {
  // Handle CORS preflight
  if (req.method === 'OPTIONS') {
    return new Response(null, {
      status: 204,
      headers: {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
      },
    });
  }

  const token = process.env.APIFY_TOKEN;
  if (!token) {
    return new Response(JSON.stringify({ error: 'APIFY_TOKEN not configured in Netlify environment variables' }), {
      status: 500,
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' },
    });
  }

  const url = new URL(req.url);
  const pathParam = url.searchParams.get('path');
  if (!pathParam) {
    return new Response(JSON.stringify({ error: 'Missing path param' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' },
    });
  }

  // The path param may contain embedded query strings (e.g. /acts/.../runs?limit=20&desc=1).
  // Parse them out and merge into a single clean query string with the token.
  const apifyBase = new URL(`https://api.apify.com/v2${pathParam}`);
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

  let resp;
  try {
    resp = await fetch(apifyBase.href, {
      method: req.method,
      headers: { 'Content-Type': 'application/json' },
      body,
    });
  } catch (err) {
    return new Response(JSON.stringify({ error: `Fetch failed: ${err.message}` }), {
      status: 502,
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' },
    });
  }

  const text = await resp.text();
  return new Response(text, {
    status: resp.status,
    headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' },
  });
};

export const config = { path: '/api/apify' };
