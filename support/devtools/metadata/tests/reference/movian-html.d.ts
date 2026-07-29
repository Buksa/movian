/**
 * Accepted calibration fixture for movian/html.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/movian/html.js, with native/gumbo calls.
 */
declare module 'movian/html' {
    /**
     * Source: exports.parse returns an object with document and root Node instances.
     */
    export function parse(html: string): ParsedDocument;

    /**
     * Source: Node constructor wraps a native gumbo node.
     */
    interface Node {
        /**
         * Source: getElementById calls gumbo.findById.
         */
        getElementById(id: string): Node | null;

        /**
         * Source: getElementByClassName calls gumbo.findByClassName.
         */
        getElementByClassName(className: string): Node[];

        /**
         * Source: getElementsByClassName is an alias to getElementByClassName.
         */
        getElementsByClassName(className: string): Node[];

        /**
         * Source: getElementByTagName calls gumbo.findByTagName.
         */
        getElementByTagName(tagName: string): Node[];

        /**
         * Source: getElementsByTagName is an alias to getElementByTagName.
         */
        getElementsByTagName(tagName: string): Node[];
    }

    /**
     * Source: parse return value.
     */
    interface ParsedDocument {
        readonly document: Node;
        readonly root: Node;
    }
}
