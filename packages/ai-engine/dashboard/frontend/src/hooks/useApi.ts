/**
 * API Hook — Fetches data from REST endpoints.
 */
import { useCallback, useEffect, useState } from 'react'

const API_BASE = '/api/v1'

interface FetchState<T> {
  data: T | null
  loading: boolean
  error: string | null
  refetch: () => void
}

export function useApi<T>(endpoint: string, options?: RequestInit): FetchState<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [trigger, setTrigger] = useState(0)

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API_BASE}${endpoint}`, options)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      setData(json)
    } catch (err: any) {
      setError(err.message || 'Fetch failed')
    } finally {
      setLoading(false)
    }
  }, [endpoint, trigger])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const refetch = useCallback(() => setTrigger((t) => t + 1), [])

  return { data, loading, error, refetch }
}

export async function apiPost<T>(endpoint: string, body: any): Promise<T> {
  const res = await fetch(`${API_BASE}${endpoint}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}
