/**
 * Accepted calibration fixture for movian/service.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Sources: res/ecmascript/modules/movian/service.js and
 * src/ecmascript/es_service.c (fnlist_service[] and its implementations).
 */
declare module 'movian/service' {
    interface Service {
        /**
         * Source: Service() installs the opaque es_resource_push() result as a
         * value field; unknown deliberately avoids inventing its native shape.
         */
        readonly id: unknown;

        /**
         * Source: the setter calls native/service.enable(id, boolean).
         * The current JS getter calls enable(id) without returning its boolean
         * result, so reads are deliberately typed as undefined.
         */
        get enabled(): undefined;
        set enabled(value: boolean);

        /** Source: Service.prototype.destroy. */
        destroy(): void;
    }

    /**
     * Source: exports.create prepends Plugin.id, then passes all six arguments
     * to fnlist_service.create. es_service_create() accepts no icon when the
     * sixth native argument is absent or not a string.
     */
    export function create(
        title: string,
        url: string,
        type: string,
        enabled: boolean,
        icon?: string
    ): Service;
}
