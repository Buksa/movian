/**
 * Accepted calibration fixture for movian/xmlrpc.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/movian/xmlrpc.js.
 */
declare module 'movian/xmlrpc' {
    import { XmlProxy } from 'movian/xml';

    /**
     * Source: exports.call takes URL, method, and rest arguments.
     * Body uses arguments[0], arguments[1], and rest from arguments[2].
     *
     * The return is source-definite, not opaque: xmlrpc.js ends with
     *   return require('movian/xml').htsmsg(x);
     * so the result is exactly the XmlProxy that movian/xml declares.
     * Leaving it `unknown` discards a guarantee the source gives and hides
     * filterNodes/valueOf and the dynamic properties from callers.
     */
    export function call(
        url: string,
        method: string,
        ...args: unknown[]
    ): XmlProxy;
}
