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
    export function parse(
        urlString: string,
        parseQueryString?: boolean
    ): ParsedUrl;

    /**
     * Source: exports.resolve is an alias to native/string.resolveURL.
     * Native metadata provides the exact two-argument call shape.
     */
    export function resolve(from: string, to: string): string;

    /**
     * Source: the object `exports.format` READS. url.js:6-32 consults
     * protocol, slashes, host, hostname, port, auth, pathname, search, query
     * and hash, falling back where a field is absent (`d.host || (d.hostname
     * + ...)`), so everything except the two it dereferences unguarded is
     * optional here. This is the input shape, and it is NOT the same set that
     * `parse` produces -- see ParsedUrl.
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

    /**
     * Source: the object native/string.parseURL BUILDS
     * (src/ecmascript/es_string.c, es_parseURL). `protocol`, `hostname`,
     * `path` and `pathname` are put unconditionally; `auth` only `if(*auth)`,
     * `port` only `if(port != -1)`, `hash` only `if(hash != NULL)`, and
     * `search`/`query` only when a query string exists (and `query` only when
     * the parseQueryString flag is set).
     *
     * Crucially it never produces `host` or `slashes` -- those are
     * format-side inputs only -- so a single interface for both directions
     * would let `url.parse(s).host` type-check and be undefined at runtime.
     */
    interface ParsedUrl {
        protocol: string;
        hostname: string;
        path: string;
        pathname: string;
        auth?: string;
        port?: number;
        hash?: string;
        search?: string;
        query?: Record<string, string>;
    }
}
