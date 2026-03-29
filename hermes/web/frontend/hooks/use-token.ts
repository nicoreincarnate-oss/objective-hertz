'use client'

import { useEffect, useState } from 'react'

export function useToken() {
  const [token, setTokenState] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    // Read token ONLY from sessionStorage — never from query params.
    // Query-param tokens leak into browser history, Referer headers,
    // server logs, and analytics. The backend explicitly forbids them.
    const storedToken = sessionStorage.getItem('perseus_token')
    if (storedToken) {
      setTokenState(storedToken)
    }
    setIsLoading(false)
  }, [])

  const setToken = (newToken: string) => {
    sessionStorage.setItem('perseus_token', newToken)
    setTokenState(newToken)
  }

  const clearToken = () => {
    sessionStorage.removeItem('perseus_token')
    setTokenState(null)
  }

  return { token, isLoading, setToken, clearToken }
}

/**
 * Build fetch headers with Bearer auth for backend requests.
 * All API calls must use this instead of query-string tokens.
 */
export function authHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}` }
}
