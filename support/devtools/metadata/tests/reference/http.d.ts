/**
 * Accepted calibration fixture for http.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/http.js, with native/io and native/string
 * calls.
 */
declare module 'http' {
    import { UrlObject } from 'url';
    import { DuktapeBuffer } from 'movian/http';

    /**
     * Source: exports.request creates a Request instance.
     * Accepts either a URL string or an options object.
     *
     * `callback` is accepted and never invoked anywhere in http.js, so no
     * signature is source-defensible and it stays `unknown` on purpose --
     * do not "fix" it into a function type the runtime never calls. The
     * falsifiable callback surface of this module is HttpRequest.on and
     * HttpResponse.on below, which source does invoke with known arguments.
     *
     * `https` is boolean: https.js calls http.request(opts, cb, true).
     */
    export function request(
        opts: string | RequestOptions,
        callback?: unknown,
        https?: boolean
    ): HttpRequest;

    /**
     * Source: exports.get calls request and ends it.
     */
    export function get(
        opts: string | RequestOptions,
        callback?: unknown,
        https?: boolean
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
         * Source: Request.prototype.on stores the handler, and end()'s
         * io.httpReq callback invokes onResponse(new Response(res)) on
         * success and onError(err) on failure.
         */
        on(
            event: 'response',
            callback: (response: HttpResponse) => void
        ): void;
        on(event: 'error', callback: (error: unknown) => void): void;
    }

    /**
     * Source: Response constructor wraps native response. Reachable through
     * HttpRequest.on('response', ...) above.
     */
    interface HttpResponse {
        /**
         * Source: statusCode from native response (res.statuscode).
         */
        readonly statusCode: number;

        /**
         * Source: encoding property (default 'utf8').
         */
        encoding: string;

        /**
         * Source: `this.bytes = res.buffer` (http.js), and es_io.c fills that
         * property with `duk_push_fixed_buffer` only `if(ehr->ehr_result !=
         * NULL)`, so it is the same indexed-byte buffer movian/http already
         * declares, and it is absent when the response carried no body.
         */
        readonly bytes: DuktapeBuffer | undefined;

        /**
         * Source: setEncoding method.
         */
        setEncoding(enc: string): void;

        /**
         * Source: the Response constructor's deferred callback invokes
         * onData(string.utf8FromBytes(bytes, encoding)) -- a string -- and
         * then onEnd() with no arguments.
         */
        on(event: 'data', callback: (chunk: string) => void): void;
        on(event: 'end', callback: () => void): void;
    }

    /**
     * Source: a non-string `opts` is passed straight into
     * require('url').format(opts), so this options object IS the url
     * module's UrlObject. Declared by extension rather than copied so the
     * two fixtures cannot drift -- notably `port`, which
     * native/string.parseURL pushes with duk_push_int
     * (src/ecmascript/es_string.c:319) and is therefore a number.
     */
    interface RequestOptions extends UrlObject {
    }
}
