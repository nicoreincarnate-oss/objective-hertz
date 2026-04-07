'use client'

/**
 * MemoryDetailDrawer — slides in from the right when a neuron is clicked
 * inside the Oracle's Pool 3D brain. Fetches the matching memory by id from
 * /api/memory/search and renders its full body in pixel-perfect HUD style.
 *
 * Same-origin, no auth header — the /api/memory/search route handles
 * propagation of the operator session itself.
 */

import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { X } from 'lucide-react'

interface MemoryItem {
  id: string
  name?: string
  type?: string
  confidence?: number
  daemon?: string
  domain?: string
  date?: string
  pinned?: boolean
  version?: number
  summary?: string
  body?: string
  file?: string
  color?: string
}

interface MemoryDetailDrawerProps {
  memoryId: string | null
  onClose: () => void
}

type LoadState = 'idle' | 'loading' | 'ready' | 'error' | 'notfound'

export function MemoryDetailDrawer({ memoryId, onClose }: MemoryDetailDrawerProps) {
  const [item, setItem] = useState<MemoryItem | null>(null)
  const [state, setState] = useState<LoadState>('idle')

  useEffect(() => {
    if (!memoryId) {
      setItem(null)
      setState('idle')
      return
    }

    let cancelled = false
    setState('loading')
    setItem(null)

    const fetchMemory = async () => {
      try {
        const res = await fetch(
          `/api/memory/search?q=${encodeURIComponent(memoryId)}`,
          { cache: 'no-store' },
        )
        if (cancelled) return
        if (!res.ok) {
          setState('error')
          return
        }
        const data = (await res.json()) as unknown
        if (cancelled) return
        if (!Array.isArray(data)) {
          setState('error')
          return
        }
        const list = data as MemoryItem[]
        const match = list.find((m) => m.id === memoryId) ?? null
        if (!match) {
          setState('notfound')
          return
        }
        setItem(match)
        setState('ready')
      } catch {
        if (!cancelled) setState('error')
      }
    }

    fetchMemory()
    return () => {
      cancelled = true
    }
  }, [memoryId])

  // ESC closes the drawer
  useEffect(() => {
    if (!memoryId) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [memoryId, onClose])

  return (
    <AnimatePresence>
      {memoryId && (
        <motion.aside
          key="memory-detail-drawer"
          initial={{ x: 420 }}
          animate={{ x: 0 }}
          exit={{ x: 420 }}
          transition={{ type: 'tween', duration: 0.28, ease: [0.2, 0.8, 0.2, 1] }}
          style={{ cursor: 'none' }}
          className="fixed top-0 right-0 z-[90] h-full w-[420px] max-w-[90vw] bg-black/85 backdrop-blur-lg border-l border-cyan-400/40 shadow-[-8px_0_40px_rgba(68,255,238,0.2)] flex flex-col"
        >
          {/* Close button */}
          <button
            type="button"
            onClick={onClose}
            aria-label="Close memory detail"
            style={{ cursor: 'none' }}
            className="absolute top-3 right-3 z-[2] w-8 h-8 flex items-center justify-center rounded border border-cyan-400/40 bg-black/60 hover:bg-cyan-400/10 hover:border-cyan-300 text-cyan-200 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>

          {/* Header */}
          <div className="px-5 pt-5 pb-3 border-b border-cyan-400/20">
            <div className="text-[9px] text-cyan-300/50 tracking-[0.25em] uppercase font-pixel-body mb-1">
              Memory Fragment
            </div>
            <div
              className="font-pixel-header text-cyan-100 text-[11px] leading-relaxed pr-8 break-words"
              style={{ textShadow: '0 0 12px rgba(68,255,238,0.4)' }}
            >
              {state === 'loading' && 'READING...'}
              {state === 'error' && 'POOL UNRESPONSIVE'}
              {state === 'notfound' && 'FORGOTTEN'}
              {state === 'ready' && item && (item.name ?? item.id)}
            </div>
          </div>

          {/* Body */}
          <div className="flex-1 overflow-y-auto px-5 py-4 font-pixel-body text-cyan-100/90 text-sm leading-snug">
            {state === 'loading' && (
              <div className="h-full flex items-center justify-center">
                <div className="text-cyan-300/70 text-xs tracking-[0.3em] font-mono animate-pulse">
                  READING THE MEMORY...
                </div>
              </div>
            )}

            {state === 'error' && (
              <div className="h-full flex items-center justify-center">
                <div className="text-amber-300/80 text-xs tracking-[0.2em] font-mono text-center">
                  ⚠ THE POOL REFUSES TO REMEMBER
                </div>
              </div>
            )}

            {state === 'notfound' && (
              <div className="h-full flex items-center justify-center">
                <div className="text-cyan-300/60 text-xs tracking-[0.3em] font-mono">
                  NO TRACE FOUND
                </div>
              </div>
            )}

            {state === 'ready' && item && (
              <div className="space-y-4">
                {/* Metadata grid */}
                <div className="grid grid-cols-2 gap-2 text-[12px]">
                  <MetaRow label="ID" value={item.id} mono />
                  {item.daemon && <MetaRow label="DAEMON" value={item.daemon} />}
                  {item.type && <MetaRow label="TYPE" value={item.type} />}
                  {typeof item.confidence === 'number' && (
                    <MetaRow
                      label="CONFIDENCE"
                      value={item.confidence.toFixed(2)}
                    />
                  )}
                  {item.domain && <MetaRow label="DOMAIN" value={item.domain} />}
                  {item.date && <MetaRow label="DATE" value={item.date} />}
                  {typeof item.version === 'number' && (
                    <MetaRow label="VERSION" value={`v${item.version}`} />
                  )}
                  {item.pinned && (
                    <MetaRow label="PINNED" value="● YES" highlight />
                  )}
                </div>

                {item.summary && (
                  <div className="pt-3 border-t border-cyan-400/20">
                    <div className="text-[9px] text-cyan-300/50 tracking-[0.25em] uppercase mb-1">
                      Summary
                    </div>
                    <div className="text-cyan-100/95 text-[15px] leading-relaxed">
                      {item.summary}
                    </div>
                  </div>
                )}

                {item.body && (
                  <div className="pt-3 border-t border-cyan-400/20">
                    <div className="text-[9px] text-cyan-300/50 tracking-[0.25em] uppercase mb-1">
                      Body
                    </div>
                    <pre className="whitespace-pre-wrap text-cyan-100/85 text-[14px] leading-relaxed font-pixel-body">
                      {item.body}
                    </pre>
                  </div>
                )}

                {item.file && (
                  <div className="pt-3 border-t border-cyan-400/20">
                    <div className="text-[9px] text-cyan-300/50 tracking-[0.25em] uppercase mb-1">
                      Source
                    </div>
                    <div className="text-cyan-300/70 text-[11px] font-mono break-all">
                      {item.file}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="px-5 py-2 border-t border-cyan-400/20 text-[9px] text-cyan-300/40 font-mono tracking-widest text-center">
            ESC · CLOSE
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  )
}

function MetaRow({
  label,
  value,
  mono,
  highlight,
}: {
  label: string
  value: string
  mono?: boolean
  highlight?: boolean
}) {
  return (
    <div className="flex flex-col">
      <span className="text-[9px] text-cyan-300/50 tracking-[0.2em] uppercase">
        {label}
      </span>
      <span
        className={`${mono ? 'font-mono text-[10px] break-all' : 'text-[12px]'} ${
          highlight ? 'text-amber-300' : 'text-cyan-100'
        }`}
      >
        {value}
      </span>
    </div>
  )
}
