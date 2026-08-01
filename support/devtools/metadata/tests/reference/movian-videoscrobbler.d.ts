/**
 * Accepted calibration fixture for movian/videoscrobbler.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/movian/videoscrobbler.js.
 */
declare module 'movian/videoscrobbler' {
    /**
     * Source: video_scrobble_push_args builds `data` with duk_push_object and
     * copies every htsmsg field into it (src/ecmascript/es_scrobble.c);
     * es_push_htsmsg_field emits a string, a number, or undefined for the
     * default case, so the surface is indexable with that finite value set.
     */
    interface ScrobbleData {
        [field: string]: string | number | undefined;
    }

    import { Property } from 'movian/prop';

    /**
     * Source: exports.VideoScrobbler constructor registers native hook.
     */
    export class VideoScrobbler {
        /**
         * Source: constructor takes no arguments.
         */
        constructor();

        /**
         * Source: onstart callback for 'start' event.
         */
        onstart?: (data: ScrobbleData, prop: Property, origin: Property) => void;

        /**
         * Source: onpause callback for pause transition.
         */
        onpause?: (data: ScrobbleData, prop: Property, origin: Property) => void;

        /**
         * Source: onresume callback for resume transition.
         */
        onresume?: (data: ScrobbleData, prop: Property, origin: Property) => void;

        /**
         * Source: onstop callback for 'stop' event.
         */
        onstop?: (data: ScrobbleData, prop: Property, origin: Property) => void;

        /**
         * Source: destroy method calls Core.resourceDestroy on the hook.
         */
        destroy(): void;
    }
}
