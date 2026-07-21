/**
 * Accepted calibration fixture for movian/http.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Sources: res/ecmascript/modules/movian/http.js and
 * src/ecmascript/es_io.c (es_http_req/httpReq).
 */
declare module 'movian/http' {
    /**
     * Source: es_http_push_result() uses duk_push_fixed_buffer; the documented
     * Duktape 1.8 runtime contract exposes indexed bytes, length and toString.
     */
    interface DuktapeBuffer {
        readonly length: number;
        readonly [index: number]: number;
        toString(): string;
    }

    /** Source: es_http_req() reads precisely these control-object fields. */
    interface HTTPRequestOptions {
        debug?: boolean;
        noFollow?: boolean;
        compression?: boolean;
        noAuth?: boolean;
        noFail?: boolean;
        verifySSL?: boolean;
        headRequest?: boolean;
        cacheTime?: number;
        caching?: boolean;
        method?: string;

        /** Source: es_http_req() string-coerces enumerable header values. */
        headers?: Record<string, string | number | boolean>;

        /**
         * Source: es_http_req() accepts a Duktape buffer, form object, or
         * string. Object values are string-coerced, so they remain unknown.
         */
        postdata?: string | DuktapeBuffer | Record<string, unknown>;

        /**
         * Source: movian/http request() merges an Array before es_http_req()
         * enumerates the resulting args object.
         */
        args?: Record<string, unknown> |
            ReadonlyArray<Record<string, unknown>>;
    }

    interface HttpResponse {
        /**
         * Source: es_http_push_result() omits buffer for a HEAD/no-body result;
         * HttpResponse stores res.buffer and Object.freeze(this).
         */
        readonly bytes: DuktapeBuffer | undefined;

        /** Source: es_http_push_result() creates alternating string entries. */
        readonly allheaders: readonly string[];

        /** Source: HttpResponse() builds and freezes the header maps. */
        readonly headers: Readonly<Record<string, string>>;
        readonly headers_lc: Readonly<Record<string, string>>;
        readonly multiheaders: Readonly<Record<string, string[]>>;
        readonly multiheaders_lc: Readonly<Record<string, string[]>>;

        /** Source: es_http_push_result() pushes ehr_http_status as an int. */
        readonly statuscode: number;

        /** Source: HttpResponse() reads a possibly absent content-type header. */
        readonly contenttype: string | undefined;

        /** Source: HttpResponse.prototype.toString. */
        toString(): string;

        /** Source: HttpResponse.prototype.convertFromEncoding. */
        convertFromEncoding(encoding: string): string;
    }

    /**
     * Source: ehr_task() supplies an error string or false plus a native
     * result; movian/http request() normalizes the success error to null and
     * wraps a successful result in HttpResponse.
     */
    type HttpCallback =
        (error: string | null, response: HttpResponse | null) => void;

    /** Source: exports.request sync branch returns new HttpResponse(res). */
    export function request(
        url: string,
        ctrl?: HTTPRequestOptions
    ): HttpResponse;

    /** Source: exports.request async branch returns without a value. */
    export function request(
        url: string,
        ctrl: HTTPRequestOptions | undefined,
        callback: HttpCallback
    ): void;
}
