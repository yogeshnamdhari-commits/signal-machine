import { io, Socket } from 'socket.io-client';

const SOCKET_URL = import.meta.env.DEV ? 'http://localhost:3001' : '';

export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'error';
type StatusCallback = (status: ConnectionStatus) => void;

class SocketService {
  private socket: Socket | null = null;
  private listeners: Map<string, Set<(...args: any[]) => void>> = new Map();
  private statusListeners: Set<StatusCallback> = new Set();
  private _status: ConnectionStatus = 'disconnected';
  private _subscribedSymbols: string[] = [];

  get status(): ConnectionStatus {
    return this._status;
  }

  private setStatus(s: ConnectionStatus) {
    this._status = s;
    this.statusListeners.forEach((cb) => cb(s));
  }

  /** Subscribe to connection status changes. Returns unsubscribe function. */
  onStatus(cb: StatusCallback): () => void {
    this.statusListeners.add(cb);
    cb(this._status); // immediate current value
    return () => this.statusListeners.delete(cb);
  }

  connect() {
    if (this.socket?.connected) return;

    this.setStatus('connecting');
    this.socket = io(SOCKET_URL, {
      transports: ['websocket', 'polling'],
      reconnection: true,
      reconnectionAttempts: Infinity,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 10000,
      timeout: 10000,
    });

    this.socket.on('connect', () => {
      console.log('[WS] Connected:', this.socket?.id);
      this.setStatus('connected');
      // Re-register all stored listeners on the socket
      this.listeners.forEach((callbacks, event) => {
        callbacks.forEach((cb) => this.socket?.on(event, cb));
      });
      // Re-subscribe to symbols if we had any
      this._resubscribe();
    });

    this.socket.on('disconnect', (reason) => {
      console.log('[WS] Disconnected:', reason);
      this.setStatus('disconnected');
    });

    this.socket.on('connect_error', (error) => {
      console.error('[WS] Connection error:', error.message);
      this.setStatus('error');
    });

    this.socket.io.on('reconnect_attempt', (attempt) => {
      console.log(`[WS] Reconnecting... attempt ${attempt}`);
      this.setStatus('connecting');
    });

    this.socket.io.on('reconnect', () => {
      console.log('[WS] Reconnected');
      this.setStatus('connected');
    });
  }

  disconnect() {
    this.socket?.disconnect();
    this.socket = null;
    this.setStatus('disconnected');
  }

  on(event: string, callback: (...args: any[]) => void) {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    this.listeners.get(event)!.add(callback);
    this.socket?.on(event, callback);
  }

  off(event: string, callback: (...args: any[]) => void) {
    this.listeners.get(event)?.delete(callback);
    this.socket?.off(event, callback);
  }

  emit(event: string, ...args: any[]) {
    this.socket?.emit(event, ...args);
  }

  subscribe(symbols: string[]) {
    this._subscribedSymbols = [...new Set([...this._subscribedSymbols, ...symbols])];
    this.socket?.emit('subscribe', symbols);
  }

  unsubscribe(symbols: string[]) {
    this._subscribedSymbols = this._subscribedSymbols.filter((s) => !symbols.includes(s));
    this.socket?.emit('unsubscribe', symbols);
  }

  private _resubscribe() {
    if (this._subscribedSymbols.length > 0) {
      this.socket?.emit('subscribe', this._subscribedSymbols);
    }
  }

  getSocket(): Socket | null {
    return this.socket;
  }
}

export const socketService = new SocketService();
export default socketService;
