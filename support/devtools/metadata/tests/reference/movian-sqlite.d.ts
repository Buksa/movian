/**
 * Accepted calibration fixture for movian/sqlite.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/movian/sqlite.js, with native/sqlite calls.
 */
declare module 'movian/sqlite' {
    /**
     * Source: exports.DB constructor calls native/sqlite.create.
     */
    export class DB {
        /**
         * Source: DB constructor takes database name.
         */
        constructor(dbname: string);

        /**
         * Source: close method calls Core.resourceDestroy on the db handle.
         */
        close(): void;

        /**
         * Source: query method forwards arguments to native/sqlite.query.
         */
        query(...args: unknown[]): void;

        /**
         * Source: step method calls native/sqlite.step.
         */
        step(): unknown;

        /**
         * Source: upgradeSchema method calls native/sqlite.upgradeSchema.
         */
        upgradeSchema(path: string): unknown;
    }
}
