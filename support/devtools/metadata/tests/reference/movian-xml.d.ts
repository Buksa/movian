/**
 * Accepted calibration fixture for movian/xml.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/movian/xml.js, with native/htsmsg calls.
 */
declare module 'movian/xml' {
    /**
     * Source: exports.parse calls native/htsmsg.createFromXML.
     * Returns a Proxy with dynamic property access.
     */
    export function parse(xmlString: string): XmlProxy;

    /**
     * Source: exports.htsmsg wraps a native htsmsg object in a Proxy.
     */
    export function htsmsg(nativeObj: unknown): XmlProxy;

    /**
     * Source: Proxy with dynamic property access via htsmsgHandler.
     */
    interface XmlProxy {
        /**
         * Source: explicit toString branch in htsmsgHandler.get.
         */
        toString(): string;

        /**
         * Source: explicit valueOf branch returns the proxied scalar value.
         */
        valueOf(): unknown;

        /**
         * Source: explicit dump branch calls native/htsmsg.print.
         */
        dump(): void;

        /**
         * Source: explicit filterNodes branch returns matching child values.
         */
        filterNodes(filter: string): unknown[];

        /**
         * Source: explicit length branch calls native/htsmsg.length.
         */
        readonly length: number;

        /**
         * Source: dynamic property access via get handler.
         */
        [key: string]: unknown;
    }
}
