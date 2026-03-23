'use client'

type CinematicBackdropProps = {
  priority?: boolean
  className?: string
}

export function CinematicBackdrop({
  priority = false,
  className = '',
}: CinematicBackdropProps) {
  return (
    <div
      aria-hidden="true"
      className={`pointer-events-none absolute inset-0 overflow-hidden ${className}`.trim()}
    >
      <video
        autoPlay
        muted
        loop
        playsInline
        preload={priority ? 'auto' : 'metadata'}
        className="perseus-video-backdrop"
      >
        <source src="/perseus-startup.mp4" type="video/mp4" />
      </video>
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top,rgba(209,169,104,0.16),transparent_42%)]" />
      <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(5,6,7,0.26),rgba(5,6,7,0.76)_45%,rgba(5,6,7,0.92))]" />
      <div className="absolute inset-0 bg-[linear-gradient(90deg,rgba(8,9,10,0.72),rgba(8,9,10,0.18)_28%,rgba(8,9,10,0.18)_72%,rgba(8,9,10,0.78))]" />
    </div>
  )
}
