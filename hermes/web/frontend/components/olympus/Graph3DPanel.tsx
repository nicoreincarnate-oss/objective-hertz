'use client'

/**
 * Graph3DPanel — Fullscreen wrapper for the Memory3DGraph (The Pool of Memory).
 *
 * This is NOT a PixelPanel. It's a full-bleed `fixed inset-0` overlay that
 * takes over the screen so the 3D brain has the entire viewport. ESC closes
 * it (and stops propagation so the parent room's ESC handler doesn't ALSO
 * close the room behind it).
 */

import { useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { X } from 'lucide-react'
import { Memory3DGraph } from './Memory3DGraph'

interface Graph3DPanelProps {
  onClose: () => void
  onSelectMemory?: (id: string) => void
  debugFakeData?: boolean
}

export function Graph3DPanel({
  onClose,
  onSelectMemory,
  debugFakeData = false,
}: Graph3DPanelProps) {
  // ESC closes the graph. We stopPropagation so the parent room's ESC
  // handler doesn't fire on the same keypress and close the room too.
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
      }
    }
    window.addEventListener('keydown', handleKey, true)
    return () => window.removeEventListener('keydown', handleKey, true)
  }, [onClose])

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.4, ease: 'easeOut' }}
        className="fixed inset-0 z-[85] bg-[#04080c]"
        style={{ cursor: 'none' }}
      >
        {/* The brain itself — fills the viewport */}
        <div className="absolute inset-0">
          <Memory3DGraph onSelect={onSelectMemory} debugFakeData={debugFakeData} />
        </div>

        {/* Top-right close button */}
        <button
          type="button"
          onClick={onClose}
          aria-label="Close the pool"
          className="absolute top-4 right-4 z-10 flex items-center justify-center w-9 h-9 rounded border border-cyan-400/40 bg-black/70 backdrop-blur-md text-cyan-200 hover:text-cyan-100 hover:border-cyan-300/70 hover:bg-black/85 transition-colors shadow-[0_0_20px_rgba(68,255,238,0.2)]"
          style={{ cursor: 'none' }}
        >
          <X className="w-4 h-4" strokeWidth={2} />
        </button>

        {/* Bottom-center footer tagline */}
        <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-10 text-[9px] text-cyan-300/50 font-mono tracking-[0.4em] uppercase pointer-events-none">
          The Pool of Memory · ESC to Return
        </div>
      </motion.div>
    </AnimatePresence>
  )
}
