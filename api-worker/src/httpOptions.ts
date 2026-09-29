export const corsHeaders: Record<string, string> = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,HEAD,POST,OPTIONS",
    "Access-Control-Max-Age": "86400",
};

/**
 * Handles HTTP OPTIONS requests for CORS preflight.
 *
 * @param request The incoming HTTP request object.
 *
 * @returns The response object.
 */
export const handleOptions = (request: Request): Response => {
    // Get the headers from the request
    const headers = request.headers;

    // Check if the request has the necessary headers for CORS preflight
    if (
        headers.get("Origin") !== null &&
        headers.get("Access-Control-Request-Method") !== null &&
        headers.get("Access-Control-Request-Headers") !== null
    ) {
        // If the request has the necessary headers for CORS preflight, return a response with the necessary headers
        const respHeaders = {
            ...corsHeaders,
            "Access-Control-Allow-Headers":
                request.headers.get("Access-Control-Request-Headers") ?? "",
        };

        // Return a response with the necessary headers
        return new Response(null, { headers: respHeaders });
    }

    // If the request does not have the necessary headers for CORS preflight, return a response with the necessary headers
    return new Response(null, {
        headers: {
            Allow: "GET, HEAD, POST, OPTIONS",
        },
    });
};
