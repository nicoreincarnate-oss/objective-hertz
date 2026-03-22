'use client'

import { useEffect, useState } from 'react'

export function useToken() {
  const [token, setToken] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    // Get token from URL on first load
    const urlParams = new URLSearchParams(window.location.search)
    const urlToken = urlParams.get('token')
    
    console.log('[v0] URL search:', window.location.search)
    console.log('[v0] URL token found:', urlToken)
    
    if (urlToken) {
      setToken(urlToken)
      // Store in sessionStorage for persistence during session
      sessionStorage.setItem('perseus_token', urlToken)
    } else {
      // Try to get from sessionStorage
      const storedToken = sessionStorage.getItem('perseus_token')
      console.log('[v0] Session token found:', storedToken)
      if (storedToken) {
        setToken(storedToken)
      }
    }
    setIsLoading(false)
  }, [])

  return { token, isLoading }
}
