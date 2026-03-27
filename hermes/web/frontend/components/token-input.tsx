// @ts-nocheck
'use client'

import React, { useState, useMemo, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { cn } from '@/lib/utils'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import * as THREE from 'three'

/* ══════════════════════════════════════════════════════════════════
   CanvasRevealEffect — from 21st.dev sign-in-flow-1 (erikx)
   Dot-matrix WebGL shader with intro/reverse animation
   ══════════════════════════════════════════════════════════════════ */

type Uniforms = {
  [key: string]: { value: number[] | number[][] | number; type: string }
}

interface ShaderProps {
  source: string
  uniforms: Uniforms
  maxFps?: number
}

const CanvasRevealEffect = ({
  animationSpeed = 10,
  opacities = [0.3, 0.3, 0.3, 0.5, 0.5, 0.5, 0.8, 0.8, 0.8, 1],
  colors = [[0, 255, 255]],
  containerClassName,
  dotSize,
  showGradient = true,
  reverse = false,
}: {
  animationSpeed?: number
  opacities?: number[]
  colors?: number[][]
  containerClassName?: string
  dotSize?: number
  showGradient?: boolean
  reverse?: boolean
}) => (
  <div className={cn('h-full relative w-full', containerClassName)}>
    <div className="h-full w-full">
      <DotMatrix
        colors={colors ?? [[0, 255, 255]]}
        dotSize={dotSize ?? 3}
        opacities={opacities ?? [0.3, 0.3, 0.3, 0.5, 0.5, 0.5, 0.8, 0.8, 0.8, 1]}
        shader={`${reverse ? 'u_reverse_active' : 'false'}_; animation_speed_factor_${animationSpeed.toFixed(1)}_; `}
        center={['x', 'y']}
      />
    </div>
    {showGradient && <div className="absolute inset-0 bg-gradient-to-t from-black to-transparent" />}
  </div>
)

interface DotMatrixProps {
  colors?: number[][]
  opacities?: number[]
  totalSize?: number
  dotSize?: number
  shader?: string
  center?: ('x' | 'y')[]
}

const DotMatrix: React.FC<DotMatrixProps> = ({
  colors = [[0, 0, 0]],
  opacities = [0.04, 0.04, 0.04, 0.04, 0.04, 0.08, 0.08, 0.08, 0.08, 0.14],
  totalSize = 20,
  dotSize = 2,
  shader = '',
  center = ['x', 'y'],
}) => {
  const uniforms = useMemo(() => {
    let colorsArray = [colors[0], colors[0], colors[0], colors[0], colors[0], colors[0]]
    if (colors.length === 2) colorsArray = [colors[0], colors[0], colors[0], colors[1], colors[1], colors[1]]
    else if (colors.length === 3) colorsArray = [colors[0], colors[0], colors[1], colors[1], colors[2], colors[2]]
    return {
      u_colors: { value: colorsArray.map(c => [c[0] / 255, c[1] / 255, c[2] / 255]), type: 'uniform3fv' },
      u_opacities: { value: opacities, type: 'uniform1fv' },
      u_total_size: { value: totalSize, type: 'uniform1f' },
      u_dot_size: { value: dotSize, type: 'uniform1f' },
      u_reverse: { value: shader.includes('u_reverse_active') ? 1 : 0, type: 'uniform1i' },
    }
  }, [colors, opacities, totalSize, dotSize, shader])

  return (
    <Shader
      source={`
        precision mediump float;
        in vec2 fragCoord;
        uniform float u_time;
        uniform float u_opacities[10];
        uniform vec3 u_colors[6];
        uniform float u_total_size;
        uniform float u_dot_size;
        uniform vec2 u_resolution;
        uniform int u_reverse;
        out vec4 fragColor;
        float PHI = 1.61803398874989484820459;
        float random(vec2 xy) { return fract(tan(distance(xy * PHI, xy) * 0.5) * xy.x); }
        void main() {
          vec2 st = fragCoord.xy;
          ${center.includes('x') ? "st.x -= abs(floor((mod(u_resolution.x, u_total_size) - u_dot_size) * 0.5));" : ''}
          ${center.includes('y') ? "st.y -= abs(floor((mod(u_resolution.y, u_total_size) - u_dot_size) * 0.5));" : ''}
          float opacity = step(0.0, st.x);
          opacity *= step(0.0, st.y);
          vec2 st2 = vec2(int(st.x / u_total_size), int(st.y / u_total_size));
          float frequency = 5.0;
          float show_offset = random(st2);
          float rand = random(st2 * floor((u_time / frequency) + show_offset + frequency));
          opacity *= u_opacities[int(rand * 10.0)];
          opacity *= 1.0 - step(u_dot_size / u_total_size, fract(st.x / u_total_size));
          opacity *= 1.0 - step(u_dot_size / u_total_size, fract(st.y / u_total_size));
          vec3 color = u_colors[int(show_offset * 6.0)];
          float animation_speed_factor = 0.5;
          vec2 center_grid = u_resolution / 2.0 / u_total_size;
          float dist_from_center = distance(center_grid, st2);
          float timing_offset_intro = dist_from_center * 0.01 + (random(st2) * 0.15);
          float max_grid_dist = distance(center_grid, vec2(0.0, 0.0));
          float timing_offset_outro = (max_grid_dist - dist_from_center) * 0.02 + (random(st2 + 42.0) * 0.2);
          float current_timing_offset;
          if (u_reverse == 1) {
            current_timing_offset = timing_offset_outro;
            opacity *= 1.0 - step(current_timing_offset, u_time * animation_speed_factor);
            opacity *= clamp((step(current_timing_offset + 0.1, u_time * animation_speed_factor)) * 1.25, 1.0, 1.25);
          } else {
            current_timing_offset = timing_offset_intro;
            opacity *= step(current_timing_offset, u_time * animation_speed_factor);
            opacity *= clamp((1.0 - step(current_timing_offset + 0.1, u_time * animation_speed_factor)) * 1.25, 1.0, 1.25);
          }
          fragColor = vec4(color, opacity);
          fragColor.rgb *= fragColor.a;
        }
      `}
      uniforms={uniforms}
      maxFps={60}
    />
  )
}

const ShaderMaterial = ({ source, uniforms, maxFps = 60 }: { source: string; maxFps?: number; uniforms: Uniforms }) => {
  const { size } = useThree()
  const ref = useRef<THREE.Mesh>(null)

  useFrame(({ clock }) => {
    if (!ref.current) return
    const material: any = ref.current.material
    material.uniforms.u_time.value = clock.getElapsedTime()
  })

  const material = useMemo(() => {
    const preparedUniforms: any = {}
    for (const name in uniforms) {
      const u: any = uniforms[name]
      switch (u.type) {
        case 'uniform1f': preparedUniforms[name] = { value: u.value, type: '1f' }; break
        case 'uniform1i': preparedUniforms[name] = { value: u.value, type: '1i' }; break
        case 'uniform3f': preparedUniforms[name] = { value: new THREE.Vector3().fromArray(u.value), type: '3f' }; break
        case 'uniform1fv': preparedUniforms[name] = { value: u.value, type: '1fv' }; break
        case 'uniform3fv': preparedUniforms[name] = { value: u.value.map((v: number[]) => new THREE.Vector3().fromArray(v)), type: '3fv' }; break
        case 'uniform2f': preparedUniforms[name] = { value: new THREE.Vector2().fromArray(u.value), type: '2f' }; break
      }
    }
    preparedUniforms['u_time'] = { value: 0, type: '1f' }
    preparedUniforms['u_resolution'] = { value: new THREE.Vector2(size.width * 2, size.height * 2) }
    return new THREE.ShaderMaterial({
      vertexShader: `
        precision mediump float;
        in vec2 coordinates;
        uniform vec2 u_resolution;
        out vec2 fragCoord;
        void main(){
          gl_Position = vec4(position.x, position.y, 0.0, 1.0);
          fragCoord = (position.xy + vec2(1.0)) * 0.5 * u_resolution;
          fragCoord.y = u_resolution.y - fragCoord.y;
        }
      `,
      fragmentShader: source,
      uniforms: preparedUniforms,
      glslVersion: THREE.GLSL3,
      blending: THREE.CustomBlending,
      blendSrc: THREE.SrcAlphaFactor,
      blendDst: THREE.OneFactor,
    })
  }, [size.width, size.height, source])

  return (
    <mesh ref={ref as any}>
      <planeGeometry args={[2, 2]} />
      <primitive object={material} attach="material" />
    </mesh>
  )
}

const Shader: React.FC<ShaderProps> = ({ source, uniforms, maxFps = 60 }) => (
  <Canvas className="absolute inset-0 h-full w-full">
    <ShaderMaterial source={source} uniforms={uniforms} maxFps={maxFps} />
  </Canvas>
)

/* ══════════════════════════════════════════════════════════════════
   TokenInput — PERSEUS War Room login
   Adapted from 21st.dev sign-in-flow-1 with CanvasRevealEffect
   ══════════════════════════════════════════════════════════════════ */

interface TokenInputProps {
  onTokenSubmit: (token: string) => void
}

export function TokenInput({ onTokenSubmit }: TokenInputProps) {
  const [inputToken, setInputToken] = useState('')
  const [initialCanvasVisible, setInitialCanvasVisible] = useState(true)
  const [reverseCanvasVisible, setReverseCanvasVisible] = useState(false)
  const [step, setStep] = useState<'token' | 'entering'>('token')

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!inputToken.trim()) return

    // Trigger reverse dot animation, then submit
    setReverseCanvasVisible(true)
    setStep('entering')
    setTimeout(() => setInitialCanvasVisible(false), 50)
    setTimeout(() => onTokenSubmit(inputToken.trim()), 1800)
  }

  return (
    <div className="flex w-full flex-col min-h-screen bg-black relative">
      {/* ── Dot-matrix WebGL background ────────────────────────── */}
      <div className="absolute inset-0 z-0">
        {initialCanvasVisible && (
          <div className="absolute inset-0">
            <CanvasRevealEffect
              animationSpeed={3}
              containerClassName="bg-black"
              colors={[[88, 224, 255], [255, 179, 71]]}
              dotSize={5}
              reverse={false}
            />
          </div>
        )}
        {reverseCanvasVisible && (
          <div className="absolute inset-0">
            <CanvasRevealEffect
              animationSpeed={4}
              containerClassName="bg-black"
              colors={[[88, 224, 255], [255, 179, 71]]}
              dotSize={5}
              reverse={true}
            />
          </div>
        )}
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,_rgba(0,0,0,1)_0%,_transparent_100%)]" />
        <div className="absolute top-0 left-0 right-0 h-1/3 bg-gradient-to-b from-black to-transparent" />
      </div>

      {/* ── Login form ─────────────────────────────────────────── */}
      <div className="relative z-10 flex flex-1 flex-col justify-center items-center">
        <div className="w-full max-w-sm px-6">
          <AnimatePresence mode="wait">
            {step === 'token' ? (
              <motion.div
                key="token-step"
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -20 }}
                transition={{ duration: 0.4, ease: 'easeOut' }}
                className="space-y-8 text-center"
              >
                {/* PERSEUS logo + badge */}
                <div className="flex flex-col items-center gap-4">
                  <img src="/assets/perseus-logo.png" alt="Perseus" className="w-16 h-16" />
                  <div className="flex items-center gap-2 px-4 py-1.5 rounded-full bg-white/5 border border-white/10">
                    <span className="w-1.5 h-1.5 rounded-full bg-[#58e0ff] animate-pulse" />
                    <span className="text-xs font-bold tracking-[0.25em] text-white/80">PERSEUS</span>
                  </div>
                </div>

                <div className="space-y-2">
                  <h1 className="text-[2.5rem] font-bold leading-[1.1] tracking-tight text-white">
                    War Room
                  </h1>
                  <p className="text-[1.1rem] text-white/50 font-light">
                    Enter your access token
                  </p>
                </div>

                <form onSubmit={handleSubmit} className="space-y-4">
                  <div className="relative">
                    <input
                      type="password"
                      placeholder="Access token"
                      value={inputToken}
                      onChange={(e) => setInputToken(e.target.value)}
                      className="w-full backdrop-blur-[1px] text-white border border-white/10 rounded-full py-3.5 px-5 focus:outline-none focus:border-white/30 text-center bg-transparent placeholder:text-white/25"
                      autoFocus
                      autoComplete="current-password"
                    />
                    <button
                      type="submit"
                      disabled={!inputToken.trim()}
                      className="absolute right-1.5 top-1.5 text-white w-9 h-9 flex items-center justify-center rounded-full bg-white/10 hover:bg-white/20 disabled:opacity-30 transition-colors group overflow-hidden cursor-pointer"
                    >
                      <span className="relative w-full h-full block overflow-hidden">
                        <span className="absolute inset-0 flex items-center justify-center transition-transform duration-300 group-hover:translate-x-full">
                          &rarr;
                        </span>
                        <span className="absolute inset-0 flex items-center justify-center transition-transform duration-300 -translate-x-full group-hover:translate-x-0">
                          &rarr;
                        </span>
                      </span>
                    </button>
                  </div>
                </form>

                <p className="text-[10px] text-white/25 pt-4">
                  Autonomous AI Revenue System
                </p>
              </motion.div>
            ) : (
              <motion.div
                key="entering-step"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ duration: 0.4, ease: 'easeOut' }}
                className="space-y-4 text-center"
              >
                <h1 className="text-[2rem] font-bold tracking-tight text-white">
                  Entering War Room...
                </h1>
                <p className="text-white/40 text-sm">Authenticating</p>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  )
}
