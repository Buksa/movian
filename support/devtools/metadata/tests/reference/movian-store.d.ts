/**
 * Accepted calibration fixture for movian/store.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/movian/store.js, with its `fs` wrapper
 * resolved through res/ecmascript/modules/fs.js to src/ecmascript/es_fs.c.
 */
declare module 'movian/store' {
    /**
     * Source: storeproxy.get/set accepts any property name and unconstrained
     * JS value, while JSON.parse() may produce any JSON object shape. `unknown`
     * therefore preserves the dynamic runtime contract instead of guessing a
     * value type from a single consumer.
     */
    export type Store<
        T extends Record<string, unknown> = Record<string, unknown>
    > = T;

    /** Source: exports.create builds Core.storagePath/store/<name>. */
    export function create<
        T extends Record<string, unknown> = Record<string, unknown>
    >(name: string): Store<T>;

    /** Source: exports.createFromPath reads and writes the supplied filename. */
    export function createFromPath<
        T extends Record<string, unknown> = Record<string, unknown>
    >(path: string): Store<T>;
}
