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
     */
    export function request(
        opts: string | RequestOptions,
        callback?: (res: HttpResponse) => void,
        https?: boolean
    ): HttpRequest;

    /**
     * Source: exports.get calls request and ends it.
     */
    export function get(
        opts: string | RequestOptions,
        callback?: (res: HttpResponse) => void,
        https?: boolean
    ): HttpRequest;

    /**
     * Source: Request constructor.
     */
    interface HttpRequest {
        /**
         * Source: end method triggers the native HTTP request.
         */
        end(): void;
    }

    /**
     * Source: Response constructor wraps native response.
     */
    interface HttpResponse {
        /**
         * Source: setEncoding method.
         */
        setEncoding(enc: string): void;
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
