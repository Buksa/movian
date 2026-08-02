/**
 * Accepted calibration fixture for websocket.
 *
 * This is a generator test oracle, not a shipped declaration package.
 * Source: res/ecmascript/modules/websocket.js, with native/websocket calls.
 */
declare module 'websocket' {
    import { DuktapeBuffer } from 'movian/http';

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
         * Source: the native onInput handler pushes an ArrayBuffer for
         * opcode-2 (binary) frames --
         *   duk_push_buffer_object(ctx, -1, 0, t->bufsize,
         *                          DUK_BUFOBJ_ARRAYBUFFER)
         * (src/ecmascript/es_websocket.c) -- and duk_push_lstring for text.
         * websocket.js passes it straight through as `{ data: d }`, so the
         * payload is a union: typing it `string` alone both rejects binary
         * frames the runtime delivers and licenses `.toUpperCase()` on them.
         */
        oninput: (data: { data: string | ArrayBuffer }) => void;

        /**
         * Source: onclose callback for connection close.
         */
        onclose: () => void;

        /**
         * Source: send calls native/websocket.clientSend, whose first act is
         *   buf = duk_get_buffer_data(ctx, 1, &bufsize);
         *   if(buf != NULL) opcode = 2;   // binary
         *   else { buf = duk_to_string(ctx, 1); opcode = 1; }
         * so a buffer argument is a first-class binary send, not an error.
         * That includes a plain Duktape buffer, which is what
         * fs.readFileSync and movian/http response bodies hand back --
         * duk_get_buffer_data accepts a plain buffer, an ArrayBuffer, and any
         * ArrayBuffer *view* -- Uint8Array, DataView and friends -- returning
         * the validated backing slice, so all of them travel as opcode-2.
         */
        send(
            d: string | ArrayBuffer | ArrayBufferView | DuktapeBuffer
        ): void;

        /**
         * Source: close method calls Core.resourceDestroy on the socket.
         */
        close(d?: unknown): void;
    }
}
