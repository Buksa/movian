/**
 * Accepted calibration fixture for https.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/https.js, which wraps ./http.
 */
declare module 'https' {
    import { HttpRequest, RequestOptions } from 'http';

    /**
     * Source: exports.request calls http.request(opts, cb, true) -- the
     * wrapper injects the HTTPS flag and reuses the HTTP surface, so the
     * returned object is an http HttpRequest and its callback families
     * ('response' / 'error') are the http ones.
     */
    export function request(
        opts: string | RequestOptions,
        callback?: unknown
    ): HttpRequest;

    /**
     * Source: exports.get calls http.get(opts, cb, true).
     */
    export function get(
        opts: string | RequestOptions,
        callback?: unknown
    ): HttpRequest;
}
