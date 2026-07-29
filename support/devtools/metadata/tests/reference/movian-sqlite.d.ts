/**
 * Accepted calibration fixture for movian/sqlite.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/movian/sqlite.js, with native/sqlite calls.
 */
declare module 'movian/sqlite' {
    /**
     * Source: DB constructor calls native/sqlite.create.
     * The checker does not understand constructor patterns.
     */
    export function DB(dbname: string): unknown;
}
