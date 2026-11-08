import createClient from "openapi-fetch";
import type { paths } from "./schema";

// Typed API client generated from the backend's OpenAPI schema.
// Same-origin (loopback) base URL: the SPA is served by the FastAPI app on
// 127.0.0.1, so requests go to the same host that served the page.
export const client = createClient<paths>({ baseUrl: "" });
