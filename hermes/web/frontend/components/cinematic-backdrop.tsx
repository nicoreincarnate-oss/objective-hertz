'use client'

import { BackgroundGradientAnimation } from '@/components/ui/background-gradient-animation'
import { DotPattern } from '@/components/ui/dot-pattern'

type CinematicBackdropProps = {
  priority?: boolean
  className?: string
}

/**
 * Phase 9: Cinematic backdrop using 21st.dev BackgroundGradientAnimation.
 * Provides the animated gradient background for the entire War Room.
 * Colors matched to Perseus design system (gold/teal/cyan).
 */
export function CinematicBackdrop({
  priority = false,
  className = '',
}: CinematicBackdropProps) {
  return (
    <div
      aria-hidden="true"
      className={`pointer-events-none absolute inset-0 overflow-hidden ${className}`.trim()}
    >
      {/* 21st.dev Background Gradient Animation — Perseus themed, cursor-interactive */}
      <BackgroundGradientAnimation
        gradientBackgroundStart="rgb(4, 8, 12)"
        gradientBackgroundEnd="rgb(4, 14, 22)"
        firstColor="88, 224, 255"
        secondColor="57, 243, 226"
        thirdColor="16, 133, 255"
        fourthColor="98, 241, 181"
        fifthColor="88, 224, 255"
        pointerColor="57, 243, 226"
        size="80%"
        blendingValue="hard-light"
        interactive={false}
        containerClassName="absolute inset-0 opacity-40"
      >
        <></>
      </BackgroundGradientAnimation>

      {/* 21st.dev DotPattern — replaces CSS grid overlay */}
      <DotPattern
        width={72}
        height={72}
        cx={1}
        cy={1}
        cr={1}
        className="absolute inset-0 fill-neutral-400/20"
        style={{
          maskImage: 'radial-gradient(circle at center, black 28%, transparent 88%)',
          opacity: 0.2,
        }}
      />

      {/* Top vignette */}
      <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(4,8,12,0.3),rgba(4,8,12,0.7)_50%,rgba(4,8,12,0.9))]" />

      {/* Side vignettes */}
      <div className="absolute inset-0 bg-[linear-gradient(90deg,rgba(4,8,12,0.6),transparent_25%,transparent_75%,rgba(4,8,12,0.6))]" />
    </div>
  )
}
