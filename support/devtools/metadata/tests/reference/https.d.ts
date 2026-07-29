/**
 * Accepted calibration fixture for https.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/https.js, which wraps ./http.
 */
declare module 'https' {
    /**
     * Source: exports.request calls http.request with https parameter added internally.
     * The wrapper injects the HTTPS flag but reuses HTTP surface.
     */
    import { HttpRequest, RequestOptions } from 'http';

    export function request(
        opts: string | RequestOptions,
        callback?: unknown
    ): HttpRequest;

    /**
     * Source: exports.get calls http.get with https parameter added internally.
     */
    export function get(
        opts: string | RequestOptions,
        callback?: unknown
    ): HttpRequest;
}
