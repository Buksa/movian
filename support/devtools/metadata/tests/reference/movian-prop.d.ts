/**
 * Accepted calibration fixture for movian/prop.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Sources: res/ecmascript/modules/movian/prop.js and
 * src/ecmascript/es_prop.c (fnlist_prop[] and its implementations).
 */
declare module 'movian/prop' {
    /** Source: propHandler.get/set and es_prop_get_value_duk(). */
    interface PropertyCore<T> {
        toString(): string;
        valueOf(): T;
    }

    /**
     * Source: propHandler.get returns a proxied child for every named access.
     * A known object shape preserves each child's value type; the default
     * Record shape deliberately represents arbitrary dynamic child names.
     */
    type Property<T = Record<string, unknown>> = PropertyCore<T> &
        (T extends object ? { [K in keyof T]: Property<T[K]> } : {});

    /** Source: es_prop_subscribe() returns an opaque es_prop_sub resource. */
    interface Subscription {
        readonly __subscriptionBrand: unique symbol;
    }

    /** Source: es_prop_node_filter_create() returns opaque es_native_propnf. */
    interface NodeFilter {
        readonly __nodeFilterBrand: unique symbol;
    }

    /** Source: es_prop_subscribe() reads these control-object fields. */
    interface SubscriptionOptions {
        autoDestroy?: boolean;
        ignoreVoid?: boolean;
        debug?: boolean;
        noInitialUpdate?: boolean;
        earlyChildDelete?: boolean;
        actionAsArray?: boolean;
    }

    /** Source: es_prop_get_value_duk() and es_prop_set_value_duk(). */
    type PropertyValue = string | number | boolean | null;

    /**
     * Source: es_sub_cb(). Event payloads vary by event; unknown marks the
     * deliberately uncorrelated union until event-discriminated types exist.
     */
    type PropertyEventCallback =
        (type: string, value?: unknown, value2?: unknown) => void;

    /** Source: exports.global = makeProp(np.global()). */
    export const global: Property;

    /** Source: makeProp() returns new Proxy(prop, propHandler). */
    export function makeProp<T = Record<string, unknown>>(
        rawProp: unknown
    ): Property<T>;

    /** Source: exports.createRoot(name) wraps np.create(name). */
    export function createRoot<T = Record<string, unknown>>(
        name?: string
    ): Property<T>;

    /**
     * Source: subscribeValue() maps es_sub_cb set/uri values through
     * makeValue(); the second URI string is optional for ordinary set events.
     */
    export function subscribeValue<T>(
        prop: Property<T>,
        callback: (value: T | null, uriTitle?: string) => void,
        ctrl?: SubscriptionOptions
    ): Subscription;

    /** Source: fnlist_prop.print / es_prop_print_duk(). */
    export function print(prop: Property): void;

    /** Source: fnlist_prop.release / es_prop_release_duk(). */
    export function release(prop: Property): void;

    /** Source: fnlist_prop.create / es_prop_create_duk(). */
    export function create<T = Record<string, unknown>>(
        name?: string
    ): Property<T>;

    /** Source: fnlist_prop.getValue / es_prop_get_value_duk(). */
    export function getValue<T>(prop: Property<T>): T;

    /** Source: fnlist_prop.getName / es_prop_get_name_duk(). */
    export function getName(prop: Property): string;

    /**
     * Source: fnlist_prop.getChild / es_prop_get_child_duk(); numeric lookup
     * can run past the child list, hence undefined.
     */
    export function getChild<T, K extends string | number>(
        prop: Property<T>,
        name: K
    ): K extends keyof T ? Property<T[K]> : Property<unknown> | undefined;

    /** Source: fnlist_prop.set / es_prop_set_value_duk(). */
    export function set(
        prop: Property,
        name: string,
        value: PropertyValue
    ): void;

    /** Source: fnlist_prop.setRichStr / es_prop_set_rich_str_duk(). */
    export function setRichStr(
        prop: Property,
        name: string,
        richString: string
    ): void;

    /** Source: fnlist_prop.setParent / es_prop_set_parent_duk(). */
    export function setParent(child: Property, parent: Property): void;

    /** Source: fnlist_prop.subscribe / es_prop_subscribe(). */
    export function subscribe(
        prop: Property,
        callback: PropertyEventCallback,
        ctrl?: SubscriptionOptions
    ): Subscription;

    /** Source: fnlist_prop.haveMore / es_prop_have_more(). */
    export function haveMore(nodes: Property, haveMore: boolean): void;

    /** Source: fnlist_prop.makeUrl / es_prop_make_url(). */
    export function makeUrl(prop: Property): string;

    /** Source: fnlist_prop.enumerate / es_prop_enum_duk(). */
    export function enumerate(prop: Property): Array<string | number>;

    /** Source: fnlist_prop.has / es_prop_has_duk(). */
    export function has(prop: Property, name: string): boolean;

    /** Source: fnlist_prop.deleteChild / es_prop_delete_child_duk(). */
    export function deleteChild(prop: Property, name: string): boolean;

    /** Source: fnlist_prop.deleteChilds / es_prop_delete_childs_duk(). */
    export function deleteChilds(prop: Property): void;

    /** Source: fnlist_prop.destroy / es_prop_destroy_duk(). */
    export function destroy(prop: Property): void;

    /** Source: fnlist_prop.select / es_prop_select(); it returns no value. */
    export function select(prop: Property): void;

    /** Source: fnlist_prop.link / es_prop_link(). */
    export function link(source: Property, destination: Property): void;

    /** Source: fnlist_prop.unlink / es_prop_unlink(). */
    export function unlink(prop: Property): void;

    /**
     * Source: fnlist_prop.sendEvent / es_prop_send_event(); payload shape is
     * event-type-specific, so it remains unknown at this calibration layer.
     */
    export function sendEvent(
        eventSink: Property,
        type: 'redirect' | 'openurl',
        value: unknown
    ): void;

    /** Source: fnlist_prop.isValue / es_prop_is_value(). */
    export function isValue(value: unknown): boolean;

    /** Source: fnlist_prop.atomicAdd / es_prop_atomic_add(). */
    export function atomicAdd(prop: Property<number>, delta: number): void;

    /** Source: fnlist_prop.isSame / es_prop_is_same(). */
    export function isSame(a: Property, b: Property): boolean;

    /** Source: fnlist_prop.moveBefore / es_prop_move_before(). */
    export function moveBefore(
        prop: Property,
        before: Property | null
    ): void;

    /** Source: fnlist_prop.unloadDestroy / es_prop_unload_destroy(). */
    export function unloadDestroy(prop: Property): void;

    /** Source: fnlist_prop.isZombie / es_prop_is_zombie(). */
    export function isZombie(prop: Property): boolean;

    /** Source: fnlist_prop.setClipRange / es_prop_set_clip_range(). */
    export function setClipRange(
        prop: Property<number>,
        start: number,
        end: number
    ): void;

    /** Source: fnlist_prop.tagSet / es_prop_tag_set(). */
    export function tagSet(
        subscription: Subscription,
        prop: Property,
        value: unknown
    ): void;

    /** Source: fnlist_prop.tagClear / es_prop_tag_clear(). */
    export function tagClear(
        subscription: Subscription,
        prop: Property
    ): unknown;

    /** Source: fnlist_prop.tagGet / es_prop_tag_get(). */
    export function tagGet(
        subscription: Subscription,
        prop: Property
    ): unknown;

    /** Source: fnlist_prop.nodeFilterCreate / es_prop_node_filter_create(). */
    export function nodeFilterCreate(
        source: Property,
        destination: Property
    ): NodeFilter;

    /** Source: fnlist_prop.nodeFilterAddPred / es_prop_node_filter_add_pred(). */
    export function nodeFilterAddPred(
        filter: NodeFilter,
        path: string,
        comparison: 'eq' | 'neq',
        value: string | number,
        enabled: Property | null,
        mode: 'include' | 'exclude'
    ): number;

    /** Source: fnlist_prop.nodeFilterDelPred / es_prop_node_filter_del_pred(). */
    export function nodeFilterDelPred(
        filter: NodeFilter,
        predicateId: number
    ): void;
}
