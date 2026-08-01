/**
 * Accepted calibration fixture for fs.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/fs.js, with native/fs calls.
 */
declare module 'fs' {
    /**
     * Source: exports.writeFileSync calls native/fs.open/write and destroys
     * the resource through Core.resourceDestroy.
     *
     * `data` is the payload native/fs.write coerces with duk_to_buffer
     * (src/ecmascript/es_fs.c, es_file_write), so it is declared as a string
     * rather than `unknown` -- the union `string | unknown` collapses to
     * `unknown` and accepts every argument, which leaves nothing to falsify.
     *
     * `opts` is accepted by the wrapper and never read, so no shape is
     * source-defensible for it.
     */
    export function writeFileSync(
        filename: string,
        data: string,
        opts?: unknown
    ): void;

    /**
     * Source: exports.readFileSync calls native/fs.open/read and destroys
     * the resource through Core.resourceDestroy.
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
