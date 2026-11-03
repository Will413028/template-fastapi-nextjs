import createClient, { type Middleware } from "openapi-fetch";
import { apiUrl, originSecret } from "../env";
import type { paths } from "./schema";

// The API is served on the page's own origin under /api (Vercel's rewrite in
// production, vercel.json; the next dev rewrite in development), so the browser
// sends the session cookie without CORS. Server-side code has no page origin and
// calls the backend directly at API_URL.
//
// Only the headers a caller passes are sent, plus the origin secret on the
// server: the backend refuses an /api request without it in production. It
// takes X-Real-IP only from requests with the secret and no server-side call
// sets one, so such a call is rate-limited as the server's own address.
function baseUrl(): string {
  if (typeof window !== "undefined") return `${window.location.origin}/api`;
  return `${apiUrl()}/api`;
}

// A 401 means the session is gone. Sessions are opaque and never refreshed, so
// the only remedy is to sign in again. Signing in itself answers 401 for a
// wrong password, which the form shows instead.
const redirectOnUnauthenticated: Middleware = {
  onResponse({ request, response, schemaPath }) {
    if (response.status !== 401 || typeof window === "undefined") return;
    if (request.method === "POST" && schemaPath === "/sessions") return;
    window.location.href = "/login";
  },
};

// Server-side only: in the browser the env value is never inlined (it has no
// NEXT_PUBLIC_ prefix), and Vercel's rewrite adds the header instead.
const sendOriginSecret: Middleware = {
  onRequest({ request }) {
    if (typeof window !== "undefined") return;
    const secret = originSecret();
    if (secret) request.headers.set("X-Origin-Secret", secret);
    return request;
  },
};

export const api = createClient<paths>({ baseUrl: baseUrl() });
api.use(sendOriginSecret, redirectOnUnauthenticated);
