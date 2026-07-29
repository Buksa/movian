/**
 * Accepted calibration fixture for url.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/url.js, with native/string calls.
 */
declare module 'url' {
    /**
     * Source: exports.format builds a URL string from a URL object.
     */
    export function format(urlObj: UrlObject): string;

    /**
     * Source: exports.parse is an alias to native/string.parseURL.
     * Native metadata provides the exact two-argument call shape.
     */
    export function parse(urlString: string, parseQueryString?: boolean): UrlObject;

    /**
     * Source: exports.resolve is an alias to native/string.resolveURL.
     * Native metadata provides the exact two-argument call shape.
     */
    export function resolve(from: string, to: string): string;

    /**
     * Source: URL object format (subset of Node.js URL format).
     */
    interface UrlObject {
        protocol: string;
        slashes?: boolean;
        host?: string;
        hostname?: string;
        port?: number;
        pathname: string;
        search?: string;
        query?: Record<string, string>;
        hash?: string;
        auth?: string;
        path?: string;
    }
}
