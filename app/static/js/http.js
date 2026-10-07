/* The one place the fetch contract lives. */

function requestHeaders(options) {
  const headers = new Headers(options.headers || undefined);
  // Only default the type for a string body. FormData must set its own
  // multipart boundary, and a Blob already carries a type.
  if (typeof options.body === 'string' && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  return headers;
}

function describeError(body, status) {
  if (!body) return `HTTP ${status}`;
  const detail = body.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    // FastAPI/Pydantic validation errors arrive as [{loc, msg, type}].
    return detail
      .map((d) => {
        const field = Array.isArray(d.loc) ? d.loc.slice(1).join('.') : '';
        return field ? `${field}: ${d.msg}` : d.msg;
      })
      .join('; ');
  }
  if (body.message) return body.message;
  return `HTTP ${status}`;
}

async function api(path, options = {}) {
  const res = await fetch(path, { ...options, headers: requestHeaders(options) });
  if (res.ok) {
    try { return await res.json(); } catch { return {}; }
  }
  const text = await res.text();
  let body;
  try { body = text ? JSON.parse(text) : {}; } catch { body = { detail: text }; }
  throw new Error(describeError(body, res.status));
}
