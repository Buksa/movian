/**
 * Accepted calibration fixture for http.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/http.js, with native/io and native/string calls.
 */
declare module 'http' {
    /**
     * Source: exports.request creates a Request instance.
     * Accepts either a URL string or an options object.
     * The callback and https parameters are accepted but not used.
     */
    export function request(
        opts: string | RequestOptions,
        callback?: unknown,
        https?: unknown
    ): HttpRequest;

    /**
     * Source: exports.get calls request and ends it.
     */
    export function get(
        opts: string | RequestOptions,
        callback?: unknown,
        https?: unknown
    ): HttpRequest;

    /**
     * Source: Request constructor.
     */
    interface HttpRequest {
        /**
         * Source: url property stores the request URL.
         */
        readonly url: string;

        /**
         * Source: headers array for request headers.
         */
        headers: unknown[];

        /**
         * Source: end method triggers the native HTTP request.
         */
        end(): void;

        /**
         * Source: on method registers event handlers.
         */
        on(event: 'response' | 'error', callback: (arg: unknown) => void): void;
    }

    /**
     * Source: Response constructor wraps native response.
     */
    interface HttpResponse {
        /**
         * Source: statusCode from native response.
         */
        readonly statusCode: number;

        /**
         * Source: encoding property (default 'utf8').
         */
        encoding: string;

        /**
         * Source: bytes buffer from native response.
         */
        readonly bytes: unknown;

        /**
         * Source: setEncoding method.
         */
        setEncoding(enc: string): void;

        /**
         * Source: on method registers data/end handlers.
         */
        on(event: 'data' | 'end', callback: (arg: unknown) => void): void;
    }

    /**
     * Source: options object format (subset of Node.js URL format).
     */
    interface RequestOptions {
        protocol: string;
        slashes?: boolean;
        host?: string;
        hostname?: string;
        port?: string;
        pathname: string;
        search?: string;
        query?: Record<string, string>;
        hash?: string;
        auth?: string;
    }
}
