/**
 * Vercel serverless function — proxies Apify API calls server-side
 * so the token is never exposed in the browser.
 * Frontend calls: /api/apify?path=/acts/jta93~aptsearch/runs
 */
export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') {
    return res.status(204).end();
  }

  const token = process.env.APIFY_TOKEN;
  if (!token) {
    return res.status(500).json({ error: 'APIFY_TOKEN not configured' });
  }

  const pathParam = req.query.path;
  if (!pathParam) {
    return res.status(400).json({ error: 'Missing path param' });
  }

  const apifyBase = new URL(`https://api.apify.com/v2${pathParam}`);
  const params = new URLSearchParams(apifyBase.search);
  for (const [k, v] of Object.entries(req.query)) {
    if (k !== 'path') params.set(k, v);
  }
  params.set('token', token);
  apifyBase.search = params.toString();

  const body = req.method !== 'GET' && req.method !== 'HEAD'
    ? JSON.stringify(req.body)
    : undefined;

  let resp;
  try {
    resp = await fetch(apifyBase.href, {
      method: req.method,
      headers: { 'Content-Type': 'application/json' },
      body,
    });
  } catch (err) {
    return res.status(502).json({ error: `Fetch failed: ${err.message}` });
  }

  const text = await resp.text();
  res.status(resp.status).setHeader('Content-Type', 'application/json').send(text);
}
