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
         * Source: query forwards its arguments to native/sqlite.query after
         * `args.unshift(this.db)`, and the native side requires the handle
         * PLUS a query string --
         *     int argc = duk_get_top(ctx);
         *     if(argc < 2) return DUK_RET_TYPE_ERROR;
         * (src/ecmascript/es_sqlite.c) -- so `db.query()` with no argument is
         * a guaranteed runtime TypeError, not a permitted variadic call. The
         * SQL text is therefore a required prefix, not part of the rest.
         */
        query(sql: string, ...args: unknown[]): void;

        /**
         * Source: step method calls native/sqlite.step.
         */
        step(): unknown;

        /**
         * Source: es_db_upgrade_schema pushes nothing and ends
         * `return 0;` -- it either succeeds with no result or raises
         * through duk_error, so the wrapper's value is always
         * undefined. upgradeSchema method calls native/sqlite.upgradeSchema.
         */
        upgradeSchema(path: string): void;

        /**
         * Source: lastRowId getter calls native/sqlite.lastRowId.
         */
        readonly lastRowId: number;

        /**
         * Source: lastErrorString getter calls native/sqlite.lastErrorString.
         */
        readonly lastErrorString: string;

        /**
         * Source: lastErrorCode getter calls native/sqlite.lastErrorCode.
         */
        readonly lastErrorCode: number;
    }
}
