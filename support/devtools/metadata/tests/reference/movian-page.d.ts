/**
 * Accepted calibration fixture for movian/page.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/movian/page.js, with route callback payloads
 * cross-checked against src/ecmascript/es_route.c.
 */
declare module 'movian/page' {
    import { Property, Subscription } from 'movian/prop';

    /** Source: Page.prototype.appendItem/appendPassiveItem proxy assignment. */
    interface ItemMetadata {
        title?: string;
        icon?: string;
        description?: string;
        [key: string]: unknown;
    }

    interface Item {
        /** Source: Item() creates the proxied root with prop.createRoot(). */
        readonly root: Property;

        /** Source: Item() stores its Page argument as a read-only descriptor. */
        readonly page: Page;

        /** Source: Item.prototype.enable. */
        enable(): void;

        /** Source: Item.prototype.disable. */
        disable(): void;

        /** Source: Item.prototype.destroy. */
        destroy(): void;

        /** Source: Item.prototype.moveBefore. */
        moveBefore(before: Item | null): void;

        /** Source: Item.prototype.addOptAction. */
        addOptAction(
            title: string,
            callback: () => void,
            subtype?: string
        ): Property;

        /** Source: Item.prototype.addOptURL. */
        addOptURL(title: string, url: string, subtype?: string): void;

        /** Source: Item.prototype.addOptSeparator. */
        addOptSeparator(title?: string): void;

        /** Source: Item.prototype.destroyOption. */
        destroyOption(item: Property): void;

        /**
         * Source: Item.prototype.bindVideoMetadata passes obj to native
         * videoMetadataBind; that native contract is outside this fixture.
         */
        bindVideoMetadata(obj: unknown): void;

        /**
         * Source: Item.prototype.unbindVideoMetadata declares but does not use
         * obj, so its accepted type remains unknown while preserving arity.
         */
        unbindVideoMetadata(obj: unknown): void;

        /** Source: Item.prototype.onEvent dispatches the action string. */
        onEvent(type: string, callback: (eventType: string) => void): void;

        /** Source: Item.prototype.dump. */
        dump(): void;

        /** Source: Item.prototype.toString. */
        toString(): string;
    }

    interface Page {
        /** Source: Page() initializes this.items to an Array of Item objects. */
        readonly items: Item[];

        /** Source: Page() receives sync from es_route.c as a boolean. */
        readonly sync: boolean;

        /** Source: Page() receives a proxied root Property. */
        readonly root: Property;

        /** Source: Page() selects root or root.model, both proxied Properties. */
        readonly model: Property;

        /** Source: Page() type accessor and propHandler get/set. */
        get type(): Property<string>;
        set type(value: string);

        /** Source: Page() metadata getter returns model.metadata. */
        readonly metadata: Property;

        /** Source: Page() loading accessor and propHandler get/set. */
        get loading(): Property<boolean>;
        set loading(value: boolean);

        /** Source: Page() source accessor and propHandler get/set. */
        get source(): Property<string>;
        set source(value: string);

        /** Source: Page() entries accessor and propHandler get/set. */
        get entries(): Property<number>;
        set entries(value: number);

        /** Source: Page() paginator accessor; it is undefined until assigned. */
        paginator: (() => boolean) | undefined;

        /** Source: Page() checks this optional plugin-assigned callback. */
        asyncPaginator?: () => void;

        /** Source: Page() checks this optional plugin-assigned callback. */
        reorderer?: (item: Item, before: Item | null) => void;

        /**
         * Source: Page() creates settings.kvstoreSettings only for non-flat
         * pages; its object contract belongs to movian/settings, not this scope.
         */
        readonly options?: unknown;

        /** Source: Page() stores the prop.subscribe() resource. */
        readonly nodesub: Subscription;

        /** Source: Page.prototype.haveMore. */
        haveMore(value: boolean): void;

        /** Source: Page.prototype.findItemByProp and prop.isSame. */
        findItemByProp(value: Property): number;

        /** Source: Page.prototype.error calls msg.toString(). */
        error(message: { toString(): string }): void;

        /** Source: Page.prototype.getItems returns this.items.slice(0). */
        getItems(): Item[];

        /** Source: Page.prototype.appendItem returns its newly created Item. */
        appendItem(
            url: string,
            type: string,
            metadata: ItemMetadata
        ): Item;

        /** Source: Page.prototype.appendAction returns its new Item. */
        appendAction(
            title: string,
            callback: () => void,
            subtype?: string
        ): Item;

        /** Source: Page.prototype.appendPassiveItem returns its new Item. */
        appendPassiveItem(
            type: string,
            data: unknown,
            metadata: ItemMetadata
        ): Item;

        /** Source: Page.prototype.dump. */
        dump(): void;

        /** Source: Page.prototype.flush. */
        flush(): void;

        /** Source: Page.prototype.redirect. */
        redirect(url: string): void;

        /** Source: Page.prototype.onEvent dispatches the action string. */
        onEvent(type: string, callback: (eventType: string) => void): void;
    }

    /**
     * Source: exports.Route callback.apply receives Page followed by capture
     * strings populated in ecmascript_openuri() in src/ecmascript/es_route.c.
     */
    type RouteCallback = (page: Page, ...captures: string[]) => void;

    class Route {
        /**
         * Source: exports.Route(re, callback) and es_route_create(), whose
         * duk_safe_to_string conversion accepts string or RegExp patterns.
         */
        constructor(pattern: string | RegExp, callback: RouteCallback);

        /** Source: exports.Route.prototype.destroy. */
        destroy(): void;
    }

    /**
     * Source: exports.Searcher callback(page, query), with query pushed as a
     * string by searcher_push_args() in src/ecmascript/es_searcher.c.
     */
    type SearcherCallback = (page: Page, query: string) => void;

    class Searcher {
        /** Source: exports.Searcher(title, icon, callback). */
        constructor(
            title: string,
            icon: string,
            callback: SearcherCallback
        );

        /** Source: exports.Searcher.prototype.destroy. */
        destroy(): void;
    }
}
