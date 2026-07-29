/**
 * Accepted calibration fixture for https.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/https.js, which re-exports from ./http.
 */
declare module 'https' {
    /**
     * Source: exports.request calls http.request with https=true flag.
     * The https parameter is added internally.
     */
    export function request(
        opts: string | {
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
        },
        callback?: unknown
    ): {
        readonly url: string;
        headers: unknown[];
        end(): void;
        on(event: 'response' | 'error', callback: (arg: unknown) => void): void;
    };

    /**
     * Source: exports.get calls http.get with https=true flag.
     */
    export function get(
        opts: string | {
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
        },
        callback?: unknown
    ): {
        readonly url: string;
        headers: unknown[];
        end(): void;
        on(event: 'response' | 'error', callback: (arg: unknown) => void): void;
    };
}
