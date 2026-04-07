'use client'

/**
 * Graph3DPanel — Fullscreen wrapper for the Memory3DGraph (The Pool of Memory).
 *
 * This is NOT a PixelPanel. It's a full-bleed `fixed inset-0` overlay that
 * takes over the screen so the 3D brain has the entire viewport. ESC closes
 * it (and stops propagation so the parent room's ESC handler doesn't ALSO
 * close the room behind it).
 *
 * Phase 41 sub-phase 5b — cinematic polish:
 *  - Concentric loader during initial 600ms mount
 *  - Black overlay "camera dive" fade for first 1.2s
 *  - Title + subtitle at top, system stats bottom-left
 *  - Smoother framer-motion transitions, ESC badge on close button
 */

import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { X } from 'lucide-react'
import { Memory3DGraph } from './Memory3DGraph'
import ConcentricLoader from '@/components/ui/concentric-loader'

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
  const [loaderVisible, setLoaderVisible] = useState(true)
  const [diveComplete, setDiveComplete] = useState(false)

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

  // Concentric loader visible for first 600ms before graph "fades in"
  useEffect(() => {
    const t = setTimeout(() => setLoaderVisible(false), 600)
    return () => clearTimeout(t)
  }, [])

  // Camera-dive black overlay completes after 1.2s — gates HUD chrome fade-in
  useEffect(() => {
    const t = setTimeout(() => setDiveComplete(true), 1200)
    return () => clearTimeout(t)
  }, [])

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.6, ease: 'easeOut' }}
        className="fixed inset-0 z-[85] bg-[#04080c]"
        style={{ cursor: 'none' }}
      >
        {/* The brain itself — fills the viewport, fades in after loader */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: loaderVisible ? 0 : 1 }}
          transition={{ duration: 0.8, ease: 'easeOut' }}
          className="absolute inset-0"
        >
          <Memory3DGraph onSelect={onSelectMemory} debugFakeData={debugFakeData} />
        </motion.div>

        {/* Camera-dive black overlay — full opacity → transparent over 1.2s */}
        <motion.div
          initial={{ opacity: 1 }}
          animate={{ opacity: 0 }}
          transition={{ duration: 1.2, ease: 'easeInOut' }}
          className="absolute inset-0 bg-black pointer-events-none z-[5]"
        />

        {/* Concentric loader centered, visible for first 600ms */}
        <AnimatePresence>
          {loaderVisible && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.3 }}
              className="absolute inset-0 flex items-center justify-center pointer-events-none z-[8]"
            >
              <ConcentricLoader />
            </motion.div>
          )}
        </AnimatePresence>

        {/* Top center — title + subtitle (fades in after camera dive) */}
        <motion.div
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: diveComplete ? 1 : 0, y: diveComplete ? 0 : -8 }}
          transition={{ duration: 0.8, ease: 'easeOut' }}
          className="absolute top-6 left-1/2 -translate-x-1/2 z-10 flex flex-col items-center pointer-events-none"
        >
          <div className="font-pixel-header text-amber-100 tracking-[0.4em] text-sm">
            THE POOL OF MEMORY
          </div>
          <div className="font-pixel-body text-cyan-300/60 tracking-[0.3em] text-[9px] mt-1">
            REAL-TIME NEURAL ACTIVITY
          </div>
        </motion.div>

        {/* Top-right close button + ESC badge */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: diveComplete ? 1 : 0 }}
          transition={{ duration: 0.5, delay: 0.1 }}
          className="absolute top-4 right-4 z-10 flex items-center gap-2"
        >
          <span className="text-[9px] font-mono tracking-[0.2em] text-cyan-300/70 bg-black/60 border border-cyan-400/30 rounded px-1.5 py-0.5 backdrop-blur-md pointer-events-none">
            [ESC]
          </span>
          <motion.button
            type="button"
            onClick={onClose}
            aria-label="Close the pool"
            whileHover={{ scale: 1.1, borderColor: '#88ddff' }}
            transition={{ duration: 0.15 }}
            className="flex items-center justify-center w-9 h-9 rounded border border-cyan-400/40 bg-black/70 backdrop-blur-md text-cyan-200 hover:text-cyan-100 hover:bg-black/85 shadow-[0_0_20px_rgba(68,255,238,0.2)]"
            style={{ cursor: 'none' }}
          >
            <X className="w-4 h-4" strokeWidth={2} />
          </motion.button>
        </motion.div>

        {/* Bottom-left — system stats */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: diveComplete ? 1 : 0 }}
          transition={{ duration: 0.6, delay: 0.2 }}
          className="absolute bottom-4 left-4 z-10 text-[10px] font-mono tracking-[0.2em] text-cyan-300/70 pointer-events-none"
        >
          MAGMA CORE · TIER 1+2 VISIBLE
        </motion.div>

        {/* Bottom-center footer tagline — delayed fade-in */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.6, delay: 0.8 }}
          className="absolute bottom-4 left-1/2 -translate-x-1/2 z-10 text-[9px] text-cyan-300/50 font-mono tracking-[0.4em] uppercase pointer-events-none"
        >
          The Pool of Memory · ESC to Return
        </motion.div>
      </motion.div>
    </AnimatePresence>
  )
}
