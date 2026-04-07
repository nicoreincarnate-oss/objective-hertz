/**
 * Cinematics — camera fly-to tweens + cortical wave modulation.
 *
 * Phase 43 sub-phase 8. Zero external deps (no GSAP/Tween.js).
 *
 * The fly-to animation mutates camera.position directly via smoothstep lerp.
 * OrbitControls will observe the updated position on its next render; we
 * intentionally do NOT touch controls.target to avoid fighting them.
 */

import * as THREE from 'three'

export interface CinematicsHandle {
  flyToPosition: (
    target: THREE.Vector3,
    lookAt?: THREE.Vector3,
    durationSec?: number,
  ) => void
  startCorticalWaves: () => void
  stopCorticalWaves: () => void
  /** Current wave intensity in [0, 1]. Returns 0 when waves disabled. */
  getCorticalWaveValue: () => number
  tick: (deltaSec: number) => void
}

interface FlyToTween {
  startTimeMs: number
  durationMs: number
  fromPos: THREE.Vector3
  toPos: THREE.Vector3
  fromTarget: THREE.Vector3 | null
  toTarget: THREE.Vector3 | null
}

function smoothstep(t: number): number {
  const c = Math.max(0, Math.min(1, t))
  return c * c * (3 - 2 * c)
}

const TWO_PI = Math.PI * 2
// ~16s period => angular velocity = 2π / 16 ≈ 0.3927. Spec says 0.4, close enough.
const WAVE_ANGULAR_VELOCITY = 0.4

export function createCinematics(camera: THREE.Camera): CinematicsHandle {
  let activeTween: FlyToTween | null = null
  let corticalWavesEnabled = false
  let wavePhase = 0

  const flyToPosition = (
    target: THREE.Vector3,
    lookAt?: THREE.Vector3,
    durationSec = 1.5,
  ): void => {
    const duration = Math.max(0, durationSec)
    if (duration <= 0) {
      // Instant jump — no tween needed.
      camera.position.copy(target)
      if (lookAt) camera.lookAt(lookAt)
      activeTween = null
      return
    }

    // Capture current look direction as a proxy for "from target" if caller
    // provided a lookAt. We reconstruct a point 1 unit ahead of the camera.
    let fromTarget: THREE.Vector3 | null = null
    if (lookAt) {
      const dir = new THREE.Vector3()
      camera.getWorldDirection(dir)
      fromTarget = camera.position.clone().add(dir)
    }

    activeTween = {
      startTimeMs: performance.now(),
      durationMs: duration * 1000,
      fromPos: camera.position.clone(),
      toPos: target.clone(),
      fromTarget,
      toTarget: lookAt ? lookAt.clone() : null,
    }
  }

  const startCorticalWaves = (): void => {
    corticalWavesEnabled = true
  }

  const stopCorticalWaves = (): void => {
    corticalWavesEnabled = false
  }

  const getCorticalWaveValue = (): number => {
    if (!corticalWavesEnabled) return 0
    return Math.sin(wavePhase) * 0.5 + 0.5
  }

  const tick = (deltaSec: number): void => {
    // Fly-to tween progression.
    if (activeTween) {
      const now = performance.now()
      const rawT = (now - activeTween.startTimeMs) / activeTween.durationMs
      const t = smoothstep(rawT)

      camera.position.lerpVectors(activeTween.fromPos, activeTween.toPos, t)

      if (activeTween.fromTarget && activeTween.toTarget) {
        const interpTarget = new THREE.Vector3().lerpVectors(
          activeTween.fromTarget,
          activeTween.toTarget,
          t,
        )
        camera.lookAt(interpTarget)
      }

      if (rawT >= 1) {
        camera.position.copy(activeTween.toPos)
        if (activeTween.toTarget) camera.lookAt(activeTween.toTarget)
        activeTween = null
      }
    }

    // Cortical wave phase advance.
    if (corticalWavesEnabled) {
      wavePhase += deltaSec * WAVE_ANGULAR_VELOCITY
      if (wavePhase >= TWO_PI) wavePhase -= TWO_PI
      if (wavePhase < 0) wavePhase += TWO_PI
    }
  }

  return {
    flyToPosition,
    startCorticalWaves,
    stopCorticalWaves,
    getCorticalWaveValue,
    tick,
  }
}
