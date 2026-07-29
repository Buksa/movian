/**
 * Accepted calibration fixture for movian/itemhook.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/movian/itemhook.js.
 */
declare module 'movian/itemhook' {
    import { Property } from 'movian/prop';

    /**
     * Source: exports.create takes a config object and returns a handle.
     */
    export function create(config: ItemHookConfig): ItemHookHandle;

    /**
     * Source: config object for itemhook creation.
     */
    interface ItemHookConfig {
        itemtype: string;
        title: string;
        icon: string;
        handler: (item: Property, nav: NavigationObject) => void;
    }

    /**
     * Source: returned handle with destroy method.
     */
    interface ItemHookHandle {
        destroy(): void;
    }

    /**
     * Source: navigation object passed to handler.
     */
    interface NavigationObject {
        /**
         * Source: openURL sends openurl event via prop.sendEvent.
         */
        openURL(url: string): void;
    }
}
