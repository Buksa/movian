/**
 * Accepted calibration fixture for https.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/https.js, which re-exports from ./http.
 */
declare module 'https' {
    /**
     * Source: exports.request calls http.request with https=true flag.
     * This is a re-export of the http module with HTTPS semantics.
     */
    export function request(
        opts: string | Record<string, unknown>,
        callback?: (res: Record<string, unknown>) => void
    ): Record<string, unknown>;

    /**
     * Source: exports.get calls http.get with https=true flag.
     */
    export function get(
        opts: string | Record<string, unknown>,
        callback?: (res: Record<string, unknown>) => void
    ): Record<string, unknown>;
}
