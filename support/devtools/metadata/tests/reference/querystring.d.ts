/**
 * Accepted calibration fixture for querystring.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/querystring.js.
 */
declare module 'querystring' {
    /**
     * Source: exports.parse is an alias to native/string.queryStringSplit.
     * Native metadata provides the exact one-argument call shape.
     */
    export function parse(query: string): Record<string, string>;
}
