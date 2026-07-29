/**
 * Accepted calibration fixture for movian/videoscrobbler.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/movian/videoscrobbler.js.
 */
declare module 'movian/videoscrobbler' {
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
        onstart?: (data: unknown, prop: Property, origin: Property) => void;

        /**
         * Source: onpause callback for pause transition.
         */
        onpause?: (data: unknown, prop: Property, origin: Property) => void;

        /**
         * Source: onresume callback for resume transition.
         */
        onresume?: (data: unknown, prop: Property, origin: Property) => void;

        /**
         * Source: onstop callback for 'stop' event.
         */
        onstop?: (data: unknown, prop: Property, origin: Property) => void;

        /**
         * Source: destroy method calls Core.resourceDestroy on the hook.
         */
        destroy(): void;
    }
}
