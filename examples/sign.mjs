// Sign imgresize-api URLs in Node.js (18+), for example in a Next.js or Express backend.
//   IMGRESIZE_SECRET=... node examples/sign.mjs https://images.example.com/cat.jpg 400
import { createHmac } from "node:crypto";

// encodeURIComponent leaves !'()* unescaped; RFC 3986 (and the server) escapes them.
const encode = (value) =>
  encodeURIComponent(value).replace(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);

export function sign(secret, params, path = "/resize") {
  const query = Object.entries(params)
    .filter(([key]) => key !== "s")
    .map(([key, value]) => [key, String(value)])
    .sort(([a, av], [b, bv]) => (a < b ? -1 : a > b ? 1 : av < bv ? -1 : av > bv ? 1 : 0))
    .map(([key, value]) => `${encode(key)}=${encode(value)}`)
    .join("&");
  const signature = createHmac("sha256", secret).update(`${path}?${query}`).digest("base64url");
  return `${path}?${query}&s=${signature}`;
}

const [source, width] = process.argv.slice(2);
if (source) {
  const expires = Math.floor(Date.now() / 1000) + 3600;
  console.log(sign(process.env.IMGRESIZE_SECRET, { url: source, w: width ?? "400", format: "auto", expires }));
}
