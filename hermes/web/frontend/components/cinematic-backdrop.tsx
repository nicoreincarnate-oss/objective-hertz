'use client'

import { motion } from 'framer-motion'
import { useWarRoom } from '@/contexts/war-room-context'

type CinematicBackdropProps = {
  priority?: boolean
  className?: string
}

/**
 * Phase 9: Cinematic backdrop with animated gradients and liquid glass effects.
 * Replaces the old video backdrop with a pure CSS/motion animated background.
 * Reacts to system health via aurora color shifts.
 * Falls back to video if `useVideo` prop is provided (removed for now).
 */
export function CinematicBackdrop({
  priority = false,
  className = '',
}: CinematicBackdropProps) {
  // Try to get health status — may not be available if used outside provider (e.g. login page)
  let healthStatus: 'green' | 'amber' | 'red' = 'green'
  try {
    const ctx = useWarRoom()
    healthStatus = ctx.healthStatus
  } catch {
    // Outside WarRoomProvider (login screen) — use default
  }

  const auroraColors = {
    green: {
      c1: 'rgba(98, 241, 181, 0.12)',
      c2: 'rgba(57, 243, 226, 0.10)',
      c3: 'rgba(88, 224, 255, 0.08)',
    },
    amber: {
      c1: 'rgba(255, 179, 71, 0.14)',
      c2: 'rgba(255, 213, 79, 0.10)',
      c3: 'rgba(255, 152, 0, 0.06)',
    },
    red: {
      c1: 'rgba(255, 90, 122, 0.16)',
      c2: 'rgba(255, 82, 82, 0.12)',
      c3: 'rgba(244, 67, 54, 0.08)',
    },
  }

  const colors = auroraColors[healthStatus]

  return (
    <div
      aria-hidden="true"
      className={`pointer-events-none absolute inset-0 overflow-hidden ${className}`.trim()}
    >
      {/* Liquid gradient base */}
      <div className="absolute inset-0 liquid-gradient-bg" />

      {/* Animated aurora blobs */}
      <motion.div
        className="absolute inset-0"
        animate={{
          background: [
            `radial-gradient(ellipse 80% 50% at 20% 30%, ${colors.c1} 0%, transparent 52%), radial-gradient(ellipse 60% 40% at 80% 20%, ${colors.c2} 0%, transparent 48%), radial-gradient(ellipse 50% 35% at 50% 80%, ${colors.c3} 0%, transparent 48%)`,
            `radial-gradient(ellipse 80% 50% at 80% 70%, ${colors.c1} 0%, transparent 52%), radial-gradient(ellipse 60% 40% at 20% 80%, ${colors.c2} 0%, transparent 48%), radial-gradient(ellipse 50% 35% at 50% 20%, ${colors.c3} 0%, transparent 48%)`,
            `radial-gradient(ellipse 80% 50% at 20% 30%, ${colors.c1} 0%, transparent 52%), radial-gradient(ellipse 60% 40% at 80% 20%, ${colors.c2} 0%, transparent 48%), radial-gradient(ellipse 50% 35% at 50% 80%, ${colors.c3} 0%, transparent 48%)`,
          ],
        }}
        transition={{
          duration: 20,
          repeat: Infinity,
          ease: 'easeInOut',
        }}
      />

      {/* Floating light orbs */}
      <motion.div
        className="absolute w-96 h-96 rounded-full"
        style={{
          background: `radial-gradient(circle, ${colors.c1}, transparent 70%)`,
          filter: 'blur(80px)',
        }}
        animate={{
          x: ['-10%', '60%', '30%', '-10%'],
          y: ['20%', '60%', '10%', '20%'],
        }}
        transition={{ duration: 30, repeat: Infinity, ease: 'easeInOut' }}
      />
      <motion.div
        className="absolute w-72 h-72 rounded-full"
        style={{
          background: `radial-gradient(circle, ${colors.c2}, transparent 70%)`,
          filter: 'blur(60px)',
        }}
        animate={{
          x: ['70%', '10%', '50%', '70%'],
          y: ['60%', '20%', '70%', '60%'],
        }}
        transition={{ duration: 25, repeat: Infinity, ease: 'easeInOut' }}
      />

      {/* HUD grid overlay */}
      <div
        className="absolute inset-0"
        style={{
          backgroundImage:
            'linear-gradient(var(--hud-grid) 1px, transparent 1px), linear-gradient(90deg, var(--hud-grid) 1px, transparent 1px)',
          backgroundSize: '72px 72px',
          maskImage: 'radial-gradient(circle at center, black 28%, transparent 88%)',
          opacity: 0.2,
        }}
      />

      {/* Noise texture */}
      <div className="noise-overlay" />

      {/* Top vignette */}
      <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(4,8,12,0.3),rgba(4,8,12,0.7)_50%,rgba(4,8,12,0.9))]" />

      {/* Side vignettes */}
      <div className="absolute inset-0 bg-[linear-gradient(90deg,rgba(4,8,12,0.6),transparent_25%,transparent_75%,rgba(4,8,12,0.6))]" />
    </div>
  )
}
