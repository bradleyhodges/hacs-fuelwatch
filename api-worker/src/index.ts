import {
    createFuelWatchParser,
    type FuelWatchApiResponse,
    forwardFuelWatchQuery,
} from "./fuelwatch";
import { corsHeaders, handleOptions } from "./httpOptions";

const cloudflareCache = caches.default;
const fuelWatchParser = createFuelWatchParser();

export default {
    async fetch(
        request: Request,
        env: Env,
        _ctx: ExecutionContext,
    ): Promise<Response> {
        // Handle HTTP OPTIONS requests for CORS preflight
        if (request.method === "OPTIONS") return handleOptions(request);

        // Check if the response is cached
        const cachedResponse = await cloudflareCache.match(request);

        // If the response is cached, return the cached response
        if (cachedResponse) return cachedResponse;

        // Fetch the original response from the fuelwatch URL
        const requestUrl = new URL(request.url);
        const fuelWatchUrl = forwardFuelWatchQuery(
            env.FUELWATCH_URL,
            requestUrl.searchParams,
        );
        const originalResponse = await fetch(fuelWatchUrl);

        // Get the original body from the original response
        const originalBody = await originalResponse.text();

        // Parse the original body as JSON
        const payload = {
            feed: await fuelWatchParser.parseString(originalBody),
        } satisfies FuelWatchApiResponse;
        const body = JSON.stringify(payload);

        // Create a new response with the original response headers
        const response = new Response(body, {
            status: 200,
            statusText: "Successful",
            headers: originalResponse.headers,
        });

        // Set the CORS headers
        response.headers.set(
            "Access-Control-Allow-Origin",
            corsHeaders["Access-Control-Allow-Origin"],
        );
        response.headers.set(
            "Access-Control-Allow-Methods",
            corsHeaders["Access-Control-Allow-Methods"],
        );
        response.headers.set("Content-Type", "application/json;charset=UTF-8");

        // Cache the response
        return cacheResponse(request, response);
    },
} satisfies ExportedHandler<Env>;

/**
 * Caches the response in the Cloudflare cache.
 *
 * @param request The incoming HTTP request object.
 * @param response The response object.
 *
 * @returns The response object.
 */
const cacheResponse = (
    request: Request,
    response: Response,
): Promise<Response> => {
    return cloudflareCache.put(request, response.clone()).then(() => response);
};
