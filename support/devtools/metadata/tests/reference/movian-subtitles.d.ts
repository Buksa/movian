/**
 * Accepted calibration fixture for movian/subtitles.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/movian/subtitles.js.
 */
declare module 'movian/subtitles' {
    /**
     * Source: exports.addProvider wraps native/subtitle.addProvider.
     * The callback receives a request object with addSubtitle method.
     */
    export function addProvider(
        callback: (req: SubtitleRequest) => void
    ): void;

    /**
     * Source: exports.getLanguages is an alias to native/subtitle.getLanguages.
     * Native metadata provides the exact zero-argument call shape.
     */
    export function getLanguages(): string[];

    /**
     * Source: callback req object has addSubtitle method.
     */
    interface SubtitleRequest {
        /**
         * Source: the request is `Object.create(query)` (subtitles.js), and
         * the native side builds that query object in
         * src/ecmascript/es_subtitles.c: `title` and `imdb` via es_set_rstr,
         * the rest only when the underlying value is present --
         * `if(ss->ss_season > 0) es_set_int(ctx, -1, "season", ...)` and the
         * same shape for year/episode/duration, filesize via es_set_double,
         * and opensubhash/subdbhash only `if(ss->ss_hash_valid)`. Every one is
         * therefore optional, and they are the primary search inputs a
         * provider callback reads.
         */
        readonly title?: string;
        readonly imdb?: string;
        readonly season?: number;
        readonly year?: number;
        readonly episode?: number;
        readonly filesize?: number;
        readonly duration?: number;
        readonly opensubhash?: string;
        readonly subdbhash?: string;

        /**
         * Source: req.addSubtitle calls native/subtitle.addItem.
         */
        addSubtitle(
            url: string,
            title: string,
            language: string,
            format: string,
            source: string,
            score: number
        ): void;
    }
}
