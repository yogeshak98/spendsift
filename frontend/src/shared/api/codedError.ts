/** Reading the backend's coded-error envelope off a thrown read/write error.
 *
 * `backend/app/server/middleware/errors.py` renders every `CodedError` as
 * `{detail, code, description, resolution, resolvedByRefresh}`. `api.ts` throws
 * `Error(\`${status} ${bodyText}\`)` and `useReport` stores `String(e)`, so by
 * the time a screen sees it the envelope is a JSON blob embedded in a string.
 *
 * This lives at the top level rather than inside one screen's folder because
 * the strings it recovers (description, and above all resolution -- what the
 * user should actually DO) are the whole point of the error framework, and a
 * screen that re-derives them by hand tends to drop the resolution.
 */
export type CodedErrorBody = {
  code: string | null;
  description: string;
  resolution: string | null;
};

export function parseCodedError(raw: unknown): CodedErrorBody | null {
  const match = String(raw).match(/\{.*\}/s);
  if (!match) return null;
  try {
    const parsed = JSON.parse(match[0]) as {
      code?: unknown;
      description?: unknown;
      detail?: unknown;
      resolution?: unknown;
    };
    const description =
      typeof parsed.description === "string"
        ? parsed.description
        : typeof parsed.detail === "string"
          ? parsed.detail
          : null;
    if (description === null) return null;
    return {
      code: typeof parsed.code === "string" ? parsed.code : null,
      description,
      resolution: typeof parsed.resolution === "string" ? parsed.resolution : null,
    };
  } catch {
    return null;
  }
}

/** One line for an error note: `"E1087: <description> <resolution>"` when the
 * failure carries the coded envelope -- either a thrown read error's string
 * (what `useReport` stores) or the parsed body openapi-fetch returns as
 * `error` -- else the raw text. */
export function codedMessage(raw: unknown): string {
  const text =
    raw !== null && typeof raw === "object" && !(raw instanceof Error) ? JSON.stringify(raw) : String(raw);
  const coded = parseCodedError(text);
  if (!coded) return text;
  return [coded.code ? `${coded.code}:` : "", coded.description, coded.resolution ?? ""].filter(Boolean).join(" ");
}
