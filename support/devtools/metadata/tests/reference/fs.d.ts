/**
 * Accepted calibration fixture for fs.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/fs.js, with native/fs calls.
 */
declare module 'fs' {
    /**
     * Source: exports.writeFileSync calls native/fs.open/write/close.
     */
    export function writeFileSync(
        filename: string,
        data: string | unknown,
        opts?: unknown
    ): void;

    /**
     * Source: exports.readFileSync calls native/fs.open/read/close.
     * Returns a Duktape.Buffer.
     */
    export function readFileSync(
        filename: string,
        opts?: unknown
    ): unknown;

    /**
     * Source: exports.readdirSync calls native/fs.readdir.
     */
    export function readdirSync(path: string): string[];

    /**
     * Source: exports.unlinkSync calls native/fs.unlink.
     */
    export function unlinkSync(filename: string): void;

    /**
     * Source: exports.mkdirSync calls native/fs.mkdirs.
     */
    export function mkdirSync(path: string): void;

    /**
     * Source: exports.rmdirSync calls native/fs.rmdir.
     */
    export function rmdirSync(path: string): void;
}
