/**
 * Accepted calibration fixture for movian/settings.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Sources: res/ecmascript/modules/movian/settings.js, movian/store.js and
 * src/ecmascript/es_kvstore.c (fnlist_kvstore[] and its implementations).
 */
declare module 'movian/settings' {
    import { Property } from 'movian/prop';

    /**
     * Source: createSetting() returns an object whose model descriptor holds
     * the setting-node Property. Its value getter returns model.value (another
     * Property proxy), while its setter assigns the underlying child value.
     */
    interface SettingItem<T> {
        readonly model: Property;
        get value(): Property<T>;
        set value(value: T);
        get enabled(): boolean;
        set enabled(value: boolean);
    }

    /** Source: sp.createMultiOpt invokes toString() on every option id. */
    interface MultiOptId {
        toString(): string;
    }

    /**
     * Source: sp.createMultiOpt only truth-tests the optional default marker,
     * so its runtime type is deliberately unknown.
     */
    type MultiOptOption = readonly [MultiOptId, string, unknown?];

    /**
     * Source: the `sp` object is installed as both constructors' prototype.
     * The two internal getvalue/setvalue functions are constructor
     * bookkeeping and are deliberately not part of this public interface.
     */
    interface SettingsMethods {
        /** Source: sp.destroy. */
        destroy(): void;

        /** Source: sp.dump. */
        dump(): void;

        /** Source: sp.createBool and native/kvstore.getBoolean. */
        createBool(
            id: string,
            title: string,
            defaultValue: boolean,
            callback: (value: boolean) => void,
            persistent?: boolean
        ): SettingItem<boolean>;

        /** Source: sp.createString and native/kvstore.getString. */
        createString(
            id: string,
            title: string,
            defaultValue: string,
            callback: (value: string) => void,
            persistent?: boolean
        ): SettingItem<string>;

        /** Source: sp.createInt, parseInt(), and native/kvstore.getInteger. */
        createInt(
            id: string,
            title: string,
            defaultValue: number,
            min: number,
            max: number,
            step: number,
            unit: string,
            callback: (value: number) => void,
            persistent?: boolean
        ): SettingItem<number>;

        /** Source: sp.createDivider. */
        createDivider(title: string): void;

        /**
         * Source: sp.createInfo never reads id. It remains unknown rather than
         * inferring a narrower type from callers, while preserving JS arity.
         */
        createInfo(id: unknown, icon: string, description: string): void;

        /**
         * Source: sp.createAction never initializes model.value, so the
         * SettingItem value's Property payload remains deliberately unknown.
         */
        createAction(
            id: string,
            title: string,
            callback: () => void
        ): SettingItem<unknown>;

        /**
         * Source: sp.createMultiOpt stringifies option ids before exposing
         * them and indexes options[0], so the option list must be non-empty.
         */
        createMultiOpt(
            id: string,
            title: string,
            options: readonly [MultiOptOption, ...MultiOptOption[]],
            callback: (value: string) => void,
            persistent?: boolean
        ): void;
    }

    /**
     * Source: exports.globalSettings assigns this.id, this.nodes and
     * this.properties, and is invoked with `new` by legacy/api-v1.js.
     */
    export class globalSettings {
        constructor(id: string, title: string, icon: string, desc: string);
        readonly id: string;
        readonly nodes: Property;
        readonly properties: Property;
    }
    export interface globalSettings extends SettingsMethods {}

    /**
     * Source: exports.kvstoreSettings assigns this.nodes and is invoked with
     * `new` by movian/page.js.
     */
    export class kvstoreSettings {
        constructor(nodes: Property, url: string, domain: 'plugin');
        readonly nodes: Property;
    }
    export interface kvstoreSettings extends SettingsMethods {}

    /** Either settings constructor's public instance contract. */
    export type SettingsGroup = globalSettings | kvstoreSettings;
}
