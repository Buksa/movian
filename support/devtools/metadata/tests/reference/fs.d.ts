/**
 * Accepted calibration fixture for fs.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/fs.js, with native/fs calls.
 *
 * CORPUS CONVENTION — coercion is not contract.
 *
 * Almost every native entry point reads its arguments with duk_to_string,
 * duk_safe_to_string or duk_to_buffer, all of which coerce anything. Taken
 * literally that would make every string parameter in this corpus `unknown`,
 * and an oracle that accepts everything calibrates nothing. So a declaration
 * states the type a caller is meant to pass, not the full set Duktape will
 * silently convert.
 *
 * The line is drawn at whether the coercion serves a distinct use case:
 *
 *   declared   a Duktape buffer for writeFileSync/readFileSync (file copy)
 *              and for websocket send (a real binary frame, opcode 2)
 *   not        number/boolean/null reaching a string parameter, where the
 *              runtime merely stringifies and no caller wants that typed
 *
 * A negative fixture case for such a parameter asserts what TypeScript does
 * with the declared type. It is not a claim that the runtime throws.
 */
declare module 'fs' {
    import { DuktapeBuffer } from 'movian/http';

    /**
     * Source: exports.writeFileSync calls native/fs.open/write and destroys
     * the resource through Core.resourceDestroy.
     *
     * `data` is the payload native/fs.write coerces with duk_to_buffer
     * (src/ecmascript/es_fs.c, es_file_write). That accepts a string OR a
     * Duktape buffer -- including the one this module's own readFileSync
     * returns -- so a plain `string` would reject the file-copy round trip
     * `writeFileSync(dst, readFileSync(src))` that the runtime supports.
     * `unknown` is equally wrong: `string | unknown` collapses to `unknown`
     * and leaves nothing to falsify.
     *
     * `opts` is accepted by the wrapper and never read, so no shape is
     * source-defensible for it.
     */
    export function writeFileSync(
        filename: string,
        data: string | DuktapeBuffer,
        opts?: unknown
    ): void;

    /**
     * Source: exports.readFileSync calls native/fs.open/read and destroys
     * the resource through Core.resourceDestroy.
     * Returns a Duktape.Buffer, the same shape native/fs.write accepts back.
     */
    export function readFileSync(
        filename: string,
        opts?: unknown
    ): DuktapeBuffer;


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
