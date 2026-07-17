/**
 * WebSocket Hook — Real-time dashboard data streaming.
 *
 * Connects to /ws/dashboard and manages subscriptions.
 * Auto-reconnects on disconnect.
 * Provides typed data for each channel.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

export type DashboardChannel =
  | 'positions'
  | 'orders'
  | 'signals'
  | 'risk'
  | 'allocation'
  | 'arbitrage'
  | 'health'
  | 'alerts'
  | 'portfolio'
  | 'exchanges'
  | 'execution'
  | 'live_signal'
  | 'signal_alert'
  | 'market_ticker'
  | 'signal_stats'

interface WebSocketMessage {
  channel: string
  data: any
  timestamp: number
}

interface UseDashboardWSOptions {
  url?: string
  reconnectInterval?: number
  maxReconnects?: number
}

export function useDashboardWS(options: UseDashboardWSOptions = {}) {
  const {
    url = `ws://${window.location.hostname}:8000/ws/dashboard`,
    reconnectInterval = 3000,
    maxReconnects = 10,
  } = options

  const wsRef = useRef<WebSocket | null>(null)
  const reconnectCount = useRef(0)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>()

  const [connected, setConnected] = useState(false)
  const [lastMessage, setLastMessage] = useState<WebSocketMessage | null>(null)
  const [channelData, setChannelData] = useState<Record<string, any>>({})
  const [latency, setLatency] = useState(0)

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN ||
        wsRef.current?.readyState === WebSocket.CONNECTING) return

    try {
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => {
        setConnected(true)
        reconnectCount.current = 0
        // Subscribe to all channels
        ws.send(JSON.stringify({
          action: 'subscribe',
          channels: [
            'positions', 'orders', 'signals', 'risk',
            'allocation', 'arbitrage', 'health', 'alerts',
            'portfolio', 'exchanges', 'execution',
            'live_signal', 'signal_alert', 'market_ticker', 'signal_stats',
          ],
        }))
      }

      ws.onmessage = (event) => {
        try {
          const msg: WebSocketMessage = JSON.parse(event.data)
          setLastMessage(msg)
          if (msg.channel) {
            // Accumulate live signals and alerts (prepend new items)
            if (msg.channel === 'live_signal' && msg.data?.signal) {
              setChannelData((prev) => {
                const existing = prev.live_signals || []
                return {
                  ...prev,
                  live_signals: [msg.data.signal, ...existing].slice(0, 100),
                }
              })
              return
            }
            if (msg.channel === 'signal_alert' && msg.data?.alert) {
              setChannelData((prev) => {
                const existing = prev.signal_alerts || []
                return {
                  ...prev,
                  signal_alerts: [msg.data.alert, ...existing].slice(0, 50),
                }
              })
              return
            }
            setChannelData((prev) => ({
              ...prev,
              [msg.channel]: msg.data,
            }))
          }
        } catch {
          // Ignore parse errors
        }
      }

      ws.onclose = () => {
        setConnected(false)
        if (reconnectCount.current < maxReconnects) {
          reconnectTimer.current = setTimeout(() => {
            reconnectCount.current++
            connect()
          }, reconnectInterval)
        }
      }

      ws.onerror = () => {
        ws.close()
      }
    } catch {
      // Connection failed
    }
  }, [url, reconnectInterval, maxReconnects])

  const disconnect = useCallback(() => {
    if (reconnectTimer.current) {
      clearTimeout(reconnectTimer.current)
    }
    wsRef.current?.close()
    wsRef.current = null
    setConnected(false)
  }, [])

  const send = useCallback((data: any) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data))
    }
  }, [])

  const ping = useCallback(() => {
    const start = Date.now()
    send({ action: 'ping' })
    // Latency will be calculated on pong
    const handler = () => setLatency(Date.now() - start)
    setTimeout(handler, 1)
  }, [send])

  useEffect(() => {
    connect()
    return () => disconnect()
  }, [connect, disconnect])

  // Periodic ping for latency
  useEffect(() => {
    const interval = setInterval(ping, 5000)
    return () => clearInterval(interval)
  }, [ping])

  return {
    connected,
    lastMessage,
    channelData,
    latency,
    send,
    connect,
    disconnect,
  }
}
