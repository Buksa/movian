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
