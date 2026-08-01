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
         * Source: the valueOf branch returns `obj.value`, and a proxy is only
         * ever built around `{msg: x}` or an htsmsg.get child carrying `msg` --
         * message and list fields push undefined into that `value`, while
         * scalar fields are returned directly and never wrapped in a proxy.
         * So the proxied value is source-definitely undefined.
         */
        valueOf(): undefined;

        /**
         * Source: explicit dump branch calls native/htsmsg.print.
         */
        dump(): void;

        /**
         * Source: filterNodes pushes getfield(...) results, which are either
         * another XmlProxy for a child message or the native field value.
         * es_push_htsmsg_field emits duk_push_string, duk_push_number, or
         * duk_push_undefined in its default branch -- a finite set.
         */
        filterNodes(filter: string): (XmlProxy | string | number |
            undefined)[];

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
