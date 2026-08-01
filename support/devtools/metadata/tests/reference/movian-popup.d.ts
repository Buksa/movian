/**
 * Accepted calibration fixture for movian/popup.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/movian/popup.js.
 */
declare module 'movian/popup' {
    /**
     * Source: exports.notify is an alias to native/popup.notify, whose native
     * table entry has nargs 3 -- but only the text is mandatory. es_notify
     * reads them as
     *     const char *text  = duk_to_string(ctx, 0);
     *     unsigned int delay = duk_to_uint(ctx, 1);
     *     const char *icon  = duk_get_string(ctx, 2);
     * so an omitted delay arrives as 0 and an omitted icon as NULL, neither
     * of which throws. Both are meaningful, not degenerate: notifications.c
     * skips the icon property `if(icon != NULL)` and treats `delay != 0` as
     * the condition for arming auto-dismiss, so delay 0 means "stay until
     * dismissed". Declaring them required rejected `popup.notify('text')`,
     * a valid persistent notification.
     */
    export function notify(
        text: string,
        delay?: number,
        icon?: string
    ): void;
}
