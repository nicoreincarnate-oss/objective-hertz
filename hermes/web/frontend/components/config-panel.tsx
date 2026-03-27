'use client'

import { useState, useEffect } from 'react'
import { useToken, authHeaders } from '@/hooks/use-token'
import { GlowCard } from '@/components/ui/spotlight-card'
import { Settings, Cpu, Check } from 'lucide-react'

interface ConfigValues {
  review_mode: boolean | null
  pipeline_pause: boolean | null
  daily_email_cap: number | null
  monthly_budget_cap: number | null
  model_primary: string | null
  model_fast: string | null
  model_genius: string | null
  model_local: string | null
  model_local_small: string | null
  model_embed: string | null
  [key: string]: unknown
}

const MODEL_OPTIONS = {
  claude: [
    'claude-opus-4',
    'claude-opus-4-6',
    'claude-sonnet-4-6',
    'claude-sonnet-4-5-20241022',
    'claude-haiku-4-5-20251001',
    'claude-3-5-sonnet-20241022',
    'claude-3-5-haiku-20241022',
  ],
  ollama: [
    'qwen2.5:14b-instruct-q4_K_M',
    'qwen2.5:7b-instruct-q4_K_M',
    'llama3.2:3b',
    'llama3.1:8b',
    'mistral:7b',
    'gemma2:9b',
    'deepseek-r1:14b',
    'phi3:14b',
  ],
  embed: [
    'nomic-embed-text',
    'mxbai-embed-large',
    'all-minilm',
    'snowflake-arctic-embed',
  ],
}

const MODEL_SLOTS = [
  { key: 'model_genius', label: 'Genius (Opus)', desc: 'Architecture, planning', options: MODEL_OPTIONS.claude },
  { key: 'model_primary', label: 'Primary (Sonnet)', desc: 'Standard execution', options: MODEL_OPTIONS.claude },
  { key: 'model_fast', label: 'Fast (Haiku)', desc: 'Simple tasks, cheap', options: MODEL_OPTIONS.claude },
  { key: 'model_local', label: 'Local Primary', desc: 'Ollama offline', options: MODEL_OPTIONS.ollama },
  { key: 'model_local_small', label: 'Local Small', desc: 'Ollama fallback', options: MODEL_OPTIONS.ollama },
  { key: 'model_embed', label: 'Embedding', desc: 'Vector embeddings', options: MODEL_OPTIONS.embed },
]

const DEFAULTS: Record<string, string> = {
  model_genius: 'claude-opus-4',
  model_primary: 'claude-sonnet-4-6',
  model_fast: 'claude-haiku-4-5-20251001',
  model_local: 'qwen2.5:14b-instruct-q4_K_M',
  model_local_small: 'llama3.2:3b',
  model_embed: 'nomic-embed-text',
}

export function ConfigPanel() {
  const { token } = useToken()
  const [config, setConfig] = useState<ConfigValues>({
    review_mode: null,
    pipeline_pause: null,
    daily_email_cap: null,
    monthly_budget_cap: null,
    model_primary: null,
    model_fast: null,
    model_genius: null,
    model_local: null,
    model_local_small: null,
    model_embed: null,
  })
  const [saving, setSaving] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  useEffect(() => {
    if (!token) return
    fetch('/api/config', { headers: authHeaders(token) })
      .then(r => r.json())
      .then(data => setConfig(prev => ({ ...prev, ...data })))
      .catch(() => {})
  }, [token])

  const updateConfig = async (key: string, value: unknown) => {
    if (!token) return
    setSaving(key)
    setConfig(prev => ({ ...prev, [key]: value }))
    try {
      await fetch('/api/config', {
        method: 'POST',
        headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, value }),
      })
      setSaved(key)
      setTimeout(() => setSaved(null), 1500)
    } finally {
      setSaving(null)
    }
  }

  const selectClass = "w-full bg-white/5 border border-border/50 rounded-lg px-3 py-2 text-xs text-foreground outline-none focus:border-gold/40 appearance-none cursor-pointer"
  const inputClass = "w-20 bg-white/5 border border-border/50 rounded-lg px-3 py-1.5 text-sm text-foreground outline-none focus:border-gold/40"

  return (
    <GlowCard customSize glowColor="blue" className="w-full p-0 bg-transparent border-0 shadow-none">
      <div className="glass-card hud-panel rounded-xl p-8 space-y-8">
        {/* ── Operations ─────────────────────────── */}
        <div>
          <div className="flex items-center gap-2 mb-5">
            <Settings className="w-4 h-4 text-gold" />
            <span className="text-sm font-semibold text-foreground">Operations</span>
          </div>

          <div className="grid grid-cols-2 lg:grid-cols-4 gap-8">
            {/* Review Mode */}
            <div className="space-y-3">
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground block">Review Mode</span>
              <div className="flex items-center gap-3">
                <button
                  onClick={() => updateConfig('review_mode', !config.review_mode)}
                  className={`relative shrink-0 w-12 h-7 rounded-full transition-colors cursor-pointer ${
                    config.review_mode ? 'bg-amber-500' : 'bg-green-500'
                  }`}
                >
                  <span className={`absolute top-1 left-1 w-5 h-5 rounded-full bg-white shadow-md transition-transform ${
                    config.review_mode ? '' : 'translate-x-5'
                  }`} />
                </button>
                <span className="text-[10px] text-muted-foreground">
                  {config.review_mode ? 'Review' : 'Auto'}
                </span>
              </div>
            </div>

            {/* Pipeline */}
            <div className="space-y-3">
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground block">Pipeline</span>
              <div className="flex items-center gap-3">
                <button
                  onClick={() => updateConfig('pipeline_pause', !config.pipeline_pause)}
                  className={`relative shrink-0 w-12 h-7 rounded-full transition-colors cursor-pointer ${
                    config.pipeline_pause ? 'bg-red-500' : 'bg-green-500'
                  }`}
                >
                  <span className={`absolute top-1 left-1 w-5 h-5 rounded-full bg-white shadow-md transition-transform ${
                    config.pipeline_pause ? '' : 'translate-x-5'
                  }`} />
                </button>
                <span className="text-[10px] text-muted-foreground">
                  {config.pipeline_pause ? 'Paused' : 'Running'}
                </span>
              </div>
            </div>

            {/* Email Cap */}
            <div className="space-y-3">
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground block">Email Cap / Day</span>
              <div className="flex items-center gap-2">
                <input
                  type="number"
                  value={config.daily_email_cap ?? 50}
                  onChange={(e) => setConfig(prev => ({ ...prev, daily_email_cap: parseInt(e.target.value) || 0 }))}
                  onBlur={(e) => updateConfig('daily_email_cap', parseInt(e.target.value) || 50)}
                  className={inputClass}
                />
                {saved === 'daily_email_cap' && <Check className="w-3.5 h-3.5 text-green-400" />}
              </div>
            </div>

            {/* Budget Cap */}
            <div className="space-y-3">
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground block">Budget Cap / Mo</span>
              <div className="flex items-center gap-2">
                <span className="text-sm text-muted-foreground">$</span>
                <input
                  type="number"
                  value={config.monthly_budget_cap ?? 800}
                  onChange={(e) => setConfig(prev => ({ ...prev, monthly_budget_cap: parseInt(e.target.value) || 0 }))}
                  onBlur={(e) => updateConfig('monthly_budget_cap', parseInt(e.target.value) || 800)}
                  className={inputClass}
                />
                {saved === 'monthly_budget_cap' && <Check className="w-3.5 h-3.5 text-green-400" />}
              </div>
            </div>
          </div>
        </div>

        {/* ── Model Selection ────────────────────── */}
        <div>
          <div className="flex items-center gap-2 mb-5">
            <Cpu className="w-4 h-4 text-teal" />
            <span className="text-sm font-semibold text-foreground">Models</span>
            <span className="text-[10px] text-muted-foreground/50 ml-auto">Changes apply system-wide</span>
          </div>

          <div className="grid grid-cols-2 lg:grid-cols-3 gap-5">
            {MODEL_SLOTS.map(({ key, label, desc, options }) => {
              const current = (config[key] as string) || DEFAULTS[key] || ''
              return (
                <div key={key} className="space-y-2">
                  <div>
                    <span className="text-[10px] uppercase tracking-wider text-muted-foreground block">{label}</span>
                    <span className="text-[9px] text-muted-foreground/40">{desc}</span>
                  </div>
                  <div className="relative">
                    <select
                      value={current}
                      onChange={(e) => updateConfig(key, e.target.value)}
                      className={selectClass}
                    >
                      {options.map(m => (
                        <option key={m} value={m}>{m}</option>
                      ))}
                      {/* Allow custom model not in the list */}
                      {!options.includes(current) && current && (
                        <option value={current}>{current} (custom)</option>
                      )}
                    </select>
                    {saved === key && (
                      <Check className="absolute right-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-green-400" />
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </GlowCard>
  )
}
