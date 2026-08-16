/**
 * Accepted calibration fixture for the Plugin global.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: ecmascript_plugin_load() in src/ecmascript/ecmascript.c.
 */
interface MovianPluginGlobal {
    /** Source: duk_push_string(ctx, id). */
    id: string;

    /** Source: duk_push_string(ctx, url). */
    url: string;

    /** Source: duk_push_string(ctx, manifest); the runtime does not parse it. */
    manifest: string;

    /** Source: duk_push_int(ctx, version). */
    apiversion: number;

    /** Source: ec->ec_path is conditionally assigned. */
    path?: string;
}

/** Source: ecmascript_plugin_load() assigns the object as global `Plugin`. */
declare var Plugin: MovianPluginGlobal;
