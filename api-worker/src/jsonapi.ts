import { ApiError } from "./errors";
import type { FeedMetadata } from "./feed";
import type { FuelWatchProductId } from "./fuelwatch";
import type { ProductFeed } from "./snapshot";
import {
    normaliseStation,
    type Station,
    type StationEnrichment,
    stationKey,
} from "./station";

/** JSON:API forbids adding charset to this media type. No extensions or profiles are applied. */
export const JSON_API_MEDIA_TYPE = "application/vnd.api+json";

/** One immutable station/product/date identity; a corrected price updates this same resource. */
export interface FuelPriceResource {
    type: "fuelPrices";
    id: string;
    attributes: Station & { product: FuelWatchProductId };
}

/** JSON:API collection document; upstream RSS channel fields never cross this boundary. */
export interface FuelPriceDocument {
    jsonapi: { version: "1.1" };
    meta: FeedMetadata;
    data: FuelPriceResource[];
}

/**
 * Build JSON:API resources using FuelWatch-owned identity, fuel and date rather than provider IDs.
 * @remarks Hash each station only once per document, even when several requested fuels share it.
 * Names, brands, prices and enrichment updates do not change resource IDs. The existing integration
 * continues deriving its station selector separately so saved user selections survive this API change.
 */
export async function jsonApiDocument(
    feed: ProductFeed,
    meta: FeedMetadata,
    profiles: ReadonlyMap<string, StationEnrichment> = new Map(),
    now = Date.now(),
): Promise<FuelPriceDocument> {
    const identities = new Map<string, Promise<string>>();
    const data = await Promise.all(
        feed.items.map(async (item) => {
            const key = stationKey(item);
            let identity = identities.get(key);
            if (!identity) {
                identity = crypto.subtle
                    .digest("SHA-256", new TextEncoder().encode(key))
                    .then((hash) =>
                        Array.from(new Uint8Array(hash), (byte) =>
                            byte.toString(16).padStart(2, "0"),
                        ).join(""),
                    );
                identities.set(key, identity);
            }
            return {
                type: "fuelPrices" as const,
                id: `${meta.sourceDate}:${item.product}:${await identity}`,
                attributes: {
                    ...normaliseStation(item, profiles.get(key), now),
                    product: item.product,
                },
            };
        }),
    );
    return { jsonapi: { version: "1.1" }, meta, data };
}

/** Errors are documents too; keep diagnostic detail safe and HTTP statuses as JSON:API strings. */
export function jsonApiError(error: ApiError) {
    return {
        jsonapi: { version: "1.1" },
        errors: [
            {
                status: String(error.status),
                code: error.code,
                detail: error.message,
            },
        ],
    };
}

/** Split HTTP list/parameter separators without splitting quoted profile URIs or escaped quotes. */
function splitHeader(value: string, separator: string): string[] | undefined {
    const parts: string[] = [];
    let start = 0;
    let quoted = false;
    let escaped = false;
    for (let i = 0; i < value.length; i++) {
        const character = value[i];
        if (escaped) escaped = false;
        else if (quoted && character === "\\") escaped = true;
        else if (character === '"') quoted = !quoted;
        else if (!quoted && character === separator) {
            parts.push(value.slice(start, i).trim());
            start = i + 1;
        }
    }
    if (quoted || escaped) return undefined;
    parts.push(value.slice(start).trim());
    return parts;
}

/** Parse only HTTP media-range syntax; quality values are Accept weights, not media parameters. */
function mediaRange(
    value: string,
    accept: boolean,
):
    | {
          type: string;
          parameters: Map<string, string>;
          quality: number;
      }
    | undefined {
    const parts = splitHeader(value, ";");
    if (!parts?.length) return undefined;
    const type = parts[0].toLowerCase();
    if (!/^[!#$%&'*+.^_`|~\w-]+\/[!#$%&'*+.^_`|~\w-]+$/.test(type))
        return undefined;
    const parameters = new Map<string, string>();
    for (const part of parts.slice(1)) {
        const match =
            /^([!#$%&'*+.^_`|~\w-]+)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([!#$%&'*+.^_`|~\w-]+))$/.exec(
                part,
            );
        if (!match) return undefined;
        const key = match[1].toLowerCase();
        if (parameters.has(key)) return undefined;
        parameters.set(key, (match[2] ?? match[3]).replace(/\\(.)/g, "$1"));
    }
    let quality = 1;
    if (accept && parameters.has("q")) {
        const weight = parameters.get("q") ?? "";
        if (!/^(?:0(?:\.\d{0,3})?|1(?:\.0{0,3})?)$/.test(weight))
            return undefined;
        quality = Number(weight);
        parameters.delete("q");
    }
    return { type, parameters, quality };
}

/** We apply no extensions and ignore unrecognized profiles, as required by JSON:API 1.1. */
function supported(range: ReturnType<typeof mediaRange>): boolean {
    return (
        !!range &&
        !range.parameters.has("ext") &&
        [...range.parameters.keys()].every((key) => key === "profile")
    );
}

/**
 * Enforce JSON:API media negotiation BEFORE cache lookup, including conditional and HEAD requests.
 * @throws ApiError with 415 for unsupported Content-Type parameters or 406 for unacceptable responses.
 * @remarks Unknown profiles are ignored. An acceptable unmodified alternative can accompany an
 * unsupported extension. Short query aliases are a separate, documented compatibility choice.
 */
export function validateJsonApiHeaders(headers: Headers): void {
    const contentType = headers.get("Content-Type");
    if (
        contentType?.split(";")[0].trim().toLowerCase() ===
            JSON_API_MEDIA_TYPE &&
        !supported(mediaRange(contentType, false))
    ) {
        throw new ApiError(
            415,
            "unsupported_media_type",
            "Unsupported JSON:API Content-Type parameters.",
        );
    }
    const accept = headers.get("Accept");
    if (!accept) return;
    const entries =
        accept.length <= 8192 ? splitHeader(accept, ",") : undefined;
    const ranges = entries?.map((value) => mediaRange(value, true)) ?? [];
    const explicit =
        entries?.filter(
            (value) =>
                value.split(";")[0].trim().toLowerCase() ===
                JSON_API_MEDIA_TYPE,
        ) ?? [];
    // A more specific exclusion cannot be overridden by a broader positive wildcard.
    const wildcard = ranges.some(
        (range) =>
            range?.type === "application/*" && range.parameters.size === 0,
    )
        ? "application/*"
        : "*/*";
    const acceptable = explicit.length
        ? ranges.some(
              (range) =>
                  range?.type === JSON_API_MEDIA_TYPE &&
                  range.quality > 0 &&
                  supported(range),
          )
        : ranges.some(
              (range) =>
                  range &&
                  range.quality > 0 &&
                  range.type === wildcard &&
                  range.parameters.size === 0,
          );
    if (!acceptable)
        throw new ApiError(
            406,
            "not_acceptable",
            "Request application/vnd.api+json without unsupported extensions or media parameters.",
        );
}
