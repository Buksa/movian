/**
 * Accepted calibration fixture for websocket.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/websocket.js, with native/websocket calls.
 */
declare module 'websocket' {
    /**
     * Source: exports.w3cwebsocket constructor creates a WebSocket client.
     */
    export class w3cwebsocket {
        /**
         * Source: constructor takes URL and optional protocol.
         */
        constructor(URL: string, protocol?: string);

        /**
         * Source: onopen callback for connection open.
         */
        onopen: () => void;

        /**
         * Source: oninput callback for data received.
         */
        oninput: (data: { data: string }) => void;

        /**
         * Source: onclose callback for connection close.
         */
        onclose: () => void;

        /**
         * Source: send method calls native/websocket.clientSend.
         */
        send(d: string): void;

        /**
         * Source: close method calls Core.resourceDestroy on the socket.
         */
        close(d?: unknown): void;
    }
}
