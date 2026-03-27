'use client'

import { useState, useEffect, useRef } from 'react'
import { useToken, authHeaders } from '@/hooks/use-token'
import { GlowCard } from '@/components/ui/spotlight-card'
import { HyperText } from '@/components/ui/hyper-text'
import { Key, Eye, EyeOff, Check, AlertCircle, Shield, Clipboard, X } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'

// ── All API keys the system uses ──────────────────────────────────────

const API_KEYS = [
  // AI & Models
  { id: 'anthropic', label: 'Claude / Anthropic', env: 'ANTHROPIC_API_KEY', required: true, category: 'ai', placeholder: 'sk-ant-...' },
  { id: 'kling_access', label: 'Kling AI (Access Key)', env: 'KLING_ACCESS_KEY', required: false, category: 'ai', placeholder: 'Access key...' },
  { id: 'kling_secret', label: 'Kling AI (Secret Key)', env: 'KLING_SECRET_KEY', required: false, category: 'ai', placeholder: 'Secret key...' },
  { id: 'recraft', label: 'Recraft AI (Images)', env: 'RECRAFT_API_KEY', required: false, category: 'ai', placeholder: 'rc_...' },
  { id: 'vast_ai', label: 'Vast.ai (Cloud GPU)', env: 'VAST_AI_API_KEY', required: false, category: 'ai', placeholder: 'vast_...' },
  { id: 'v0', label: 'v0.dev (Vercel AI)', env: 'V0_API_KEY', required: false, category: 'ai', placeholder: 'v0_...' },
  // Scraping & Research
  { id: 'firecrawl', label: 'Firecrawl (Web Scraping)', env: 'FIRECRAWL_API_KEY', required: true, category: 'scraping', placeholder: 'fc-...' },
  { id: 'firecrawl_self_host', label: 'Firecrawl Self-Host URL', env: 'FIRECRAWL_SELF_HOST_URL', required: false, category: 'scraping', placeholder: 'http://...' },
  // Outreach
  { id: 'instantly', label: 'Instantly (Email Campaigns)', env: 'INSTANTLY_API_KEY', required: true, category: 'outreach', placeholder: 'inst_...' },
  // Payments
  { id: 'stripe', label: 'Stripe (Payments)', env: 'STRIPE_API_KEY', required: true, category: 'payments', placeholder: 'sk_live_...' },
  { id: 'wise', label: 'Wise (Payouts)', env: 'WISE_API_TOKEN', required: false, category: 'payments', placeholder: 'Token...' },
  { id: 'wise_profile', label: 'Wise Profile ID', env: 'WISE_PROFILE_ID', required: false, category: 'payments', placeholder: 'Profile ID...' },
  // Hosting & DNS
  { id: 'netlify', label: 'Netlify (Site Hosting)', env: 'NETLIFY_AUTH_TOKEN', required: true, category: 'hosting', placeholder: 'nfp_...' },
  { id: 'cloudflare', label: 'Cloudflare (DNS)', env: 'CLOUDFLARE_API_TOKEN', required: false, category: 'hosting', placeholder: 'cf_...' },
  { id: 'vercel', label: 'Vercel Token', env: 'VERCEL_TOKEN', required: false, category: 'hosting', placeholder: 'vercel_...' },
  // Communications
  { id: 'telegram_bot', label: 'Telegram Bot Token', env: 'TELEGRAM_BOT_TOKEN', required: true, category: 'comms', placeholder: '123456:ABC-...' },
  { id: 'telegram_chat', label: 'Telegram Chat ID', env: 'TELEGRAM_CHAT_ID', required: true, category: 'comms', placeholder: '-100...' },
  { id: 'telegram_admin', label: 'Telegram Admin Secret', env: 'TELEGRAM_ADMIN_SECRET', required: false, category: 'comms', placeholder: 'Secret...' },
  { id: 'twilio_sid', label: 'Twilio Account SID', env: 'TWILIO_ACCOUNT_SID', required: false, category: 'comms', placeholder: 'AC...' },
  { id: 'twilio_auth', label: 'Twilio Auth Token', env: 'TWILIO_AUTH_TOKEN', required: false, category: 'comms', placeholder: 'Token...' },
  { id: 'telnyx', label: 'Telnyx (Voice)', env: 'TELNYX_API_KEY', required: false, category: 'comms', placeholder: 'KEY...' },
  { id: 'telnyx_conn', label: 'Telnyx Connection ID', env: 'TELNYX_CONNECTION_ID', required: false, category: 'comms', placeholder: 'Connection ID...' },
  { id: 'whatsapp', label: 'WhatsApp Access Token', env: 'WHATSAPP_ACCESS_TOKEN', required: false, category: 'comms', placeholder: 'Token...' },
  // Automation & Tools
  { id: 'n8n_user', label: 'N8N Username', env: 'N8N_USER', required: false, category: 'tools', placeholder: 'admin' },
  { id: 'n8n_password', label: 'N8N Password', env: 'N8N_PASSWORD', required: false, category: 'tools', placeholder: 'Password...' },
  { id: 'composio', label: 'Composio (Gmail/Google)', env: 'COMPOSIO_API_KEY', required: false, category: 'tools', placeholder: 'comp_...' },
  // Observability
  { id: 'sentry', label: 'Sentry DSN', env: 'SENTRY_DSN', required: false, category: 'observability', placeholder: 'https://...@sentry.io/...' },
  // System
  { id: 'dashboard_secret', label: 'War Room Dashboard Secret', env: 'DASHBOARD_SECRET', required: true, category: 'system', placeholder: 'Secret...' },
  // Crypto / Conway
  { id: 'conway_api', label: 'Conway API Key', env: 'CONWAY_API_KEY', required: false, category: 'crypto', placeholder: 'Key...' },
  { id: 'base_rpc', label: 'Base L2 RPC URL', env: 'BASE_RPC_URL', required: false, category: 'crypto', placeholder: 'https://mainnet.base.org' },
]

const CATEGORIES = [
  { id: 'ai', label: 'AI & Models' },
  { id: 'scraping', label: 'Scraping & Research' },
  { id: 'outreach', label: 'Outreach' },
  { id: 'payments', label: 'Payments' },
  { id: 'hosting', label: 'Hosting & DNS' },
  { id: 'comms', label: 'Communications' },
  { id: 'tools', label: 'Automation & Tools' },
  { id: 'observability', label: 'Observability' },
  { id: 'system', label: 'System' },
  { id: 'crypto', label: 'Crypto / Conway' },
]

interface KeyState {
  configured: boolean
  masked: string
  source: 'dashboard' | 'env' | 'none'
}

// ── Paste Input Component ─────────────────────────────────────────────

function KeyInput({
  keyId,
  placeholder,
  onSave,
  onCancel,
  saving,
}: {
  keyId: string
  placeholder: string
  onSave: (value: string) => void
  onCancel: () => void
  saving: boolean
}) {
  const [value, setValue] = useState('')
  const [showValue, setShowValue] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const handlePaste = async () => {
    try {
      const text = await navigator.clipboard.readText()
      if (text) {
        setValue(text.trim())
        setShowValue(true)
        // Auto-hide after 2s
        setTimeout(() => setShowValue(false), 2000)
      }
    } catch {
      // Clipboard API not available
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: 'auto' }}
      exit={{ opacity: 0, height: 0 }}
      className="mt-3"
    >
      <div className="flex items-center gap-2">
        {/* Main input with paste-friendly design */}
        <div className="relative flex-1">
          <div className="flex items-center rounded-xl border-2 border-gold/30 bg-black/40 overflow-hidden focus-within:border-gold/60 transition-colors">
            {/* Segmented prefix indicator */}
            <div className="shrink-0 px-3 py-2.5 bg-gold/5 border-r border-gold/20">
              <Key className="w-3.5 h-3.5 text-gold/60" />
            </div>

            <input
              ref={inputRef}
              type={showValue ? 'text' : 'password'}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && value.trim()) onSave(value.trim())
                if (e.key === 'Escape') onCancel()
              }}
              placeholder={placeholder}
              className="flex-1 bg-transparent px-3 py-2.5 text-xs font-mono text-foreground placeholder:text-muted-foreground/30 outline-none"
            />

            {/* Show/hide toggle */}
            <button
              onClick={() => setShowValue(!showValue)}
              className="shrink-0 px-2 text-muted-foreground/30 hover:text-muted-foreground cursor-pointer transition-colors"
            >
              {showValue ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
            </button>

            {/* Paste button */}
            <button
              onClick={handlePaste}
              className="shrink-0 px-3 py-2.5 bg-gold/5 border-l border-gold/20 text-gold/60 hover:text-gold hover:bg-gold/10 cursor-pointer transition-colors"
              title="Paste from clipboard"
            >
              <Clipboard className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        {/* Save */}
        <button
          onClick={() => value.trim() && onSave(value.trim())}
          disabled={!value.trim() || saving}
          className="px-4 py-2.5 rounded-xl bg-green-500/20 text-green-400 text-xs font-semibold hover:bg-green-500/30 cursor-pointer disabled:opacity-30 transition-colors"
        >
          {saving ? '...' : 'Save'}
        </button>

        {/* Cancel */}
        <button
          onClick={onCancel}
          className="p-2.5 rounded-xl bg-white/5 text-muted-foreground/60 hover:text-foreground hover:bg-white/10 cursor-pointer transition-colors"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
    </motion.div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────

export default function SettingsPage() {
  const { token } = useToken()
  const [keyStates, setKeyStates] = useState<Record<string, KeyState>>({})
  const [editing, setEditing] = useState<string | null>(null)
  const [saving, setSaving] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  // Fetch current key states from backend
  useEffect(() => {
    if (!token) return
    fetch('/api/keys', { headers: authHeaders(token) })
      .then(r => r.ok ? r.json() : {})
      .then(data => {
        const states: Record<string, KeyState> = {}
        for (const [id, info] of Object.entries(data)) {
          const k = info as { configured: boolean; masked: string; source: string }
          states[id] = { configured: k.configured, masked: k.masked, source: k.source as KeyState['source'] }
        }
        setKeyStates(states)
      })
      .catch(() => {})
  }, [token])

  const handleSave = async (keyId: string, value: string) => {
    if (!token) return
    setSaving(keyId)
    try {
      const res = await fetch('/api/keys', {
        method: 'POST',
        headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
        body: JSON.stringify({ key_id: keyId, value }),
      })
      const data = await res.json()
      if (data.success) {
        setKeyStates(prev => ({
          ...prev,
          [keyId]: { configured: true, masked: data.masked, source: 'dashboard' },
        }))
        setSaved(keyId)
        setTimeout(() => setSaved(null), 2500)
        setEditing(null)
      }
    } finally {
      setSaving(null)
    }
  }

  return (
    <div className="space-y-10">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2.5 mb-1.5">
            <Key className="w-6 h-6 text-gold" />
            <HyperText text="Settings" className="text-2xl font-bold text-foreground" />
          </div>
          <p className="text-sm text-muted-foreground max-w-lg">
            Manage API keys for all external services. Click any key to paste a new value.
          </p>
        </div>
        <div className="flex items-center gap-2 text-[10px] font-mono text-muted-foreground/60 uppercase tracking-widest shrink-0 mt-1">
          <Shield className="w-3.5 h-3.5" />
          SECURE
        </div>
      </div>

      {/* Notice */}
      <div className="flex items-start gap-3 p-4 rounded-xl bg-amber-500/5 border border-amber-500/20">
        <AlertCircle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
        <div>
          <p className="text-xs text-amber-200">Keys saved here override .env values immediately and persist in the database.</p>
          <p className="text-[10px] text-muted-foreground/50 mt-1">Original .env values are kept as fallback. Full keys are never displayed — only the last 4 characters.</p>
        </div>
      </div>

      {/* Keys by category */}
      {CATEGORIES.map(cat => {
        const catKeys = API_KEYS.filter(k => k.category === cat.id)
        if (catKeys.length === 0) return null

        return (
          <div key={cat.id}>
            <p className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-[0.2em] mb-4">
              {cat.label}
            </p>
            <GlowCard customSize glowColor="blue" className="w-full p-0 bg-transparent border-0 shadow-none">
              <div className="glass-card hud-panel rounded-xl p-6 space-y-1">
                {catKeys.map(keyDef => {
                  const state = keyStates[keyDef.id]
                  const isConfigured = state?.configured ?? false
                  const isEditing = editing === keyDef.id
                  const isSaved = saved === keyDef.id

                  return (
                    <div key={keyDef.id} className="group">
                      <div
                        className="flex items-center gap-4 p-4 rounded-xl hover:bg-white/[0.03] transition-colors cursor-pointer"
                        onClick={() => {
                          if (!isEditing) {
                            setEditing(keyDef.id)
                          }
                        }}
                      >
                        {/* Status dot */}
                        <div className="shrink-0">
                          {isSaved ? (
                            <motion.div initial={{ scale: 0 }} animate={{ scale: 1 }}>
                              <Check className="w-4 h-4 text-green-400" />
                            </motion.div>
                          ) : isConfigured ? (
                            <div className="w-2.5 h-2.5 rounded-full bg-green-400" />
                          ) : keyDef.required ? (
                            <div className="w-2.5 h-2.5 rounded-full bg-red-400 animate-pulse" />
                          ) : (
                            <div className="w-2.5 h-2.5 rounded-full bg-white/10" />
                          )}
                        </div>

                        {/* Label */}
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-sm text-foreground font-medium">{keyDef.label}</span>
                            {keyDef.required && !isConfigured && (
                              <span className="text-[9px] px-1.5 py-0.5 rounded bg-red-500/10 text-red-400">required</span>
                            )}
                          </div>
                          <span className="text-[10px] text-muted-foreground/30 font-mono">{keyDef.env}</span>
                        </div>

                        {/* Masked value + source */}
                        <div className="flex items-center gap-3 shrink-0">
                          {isConfigured && state?.masked && (
                            <span className="text-xs font-mono text-muted-foreground/40">{state.masked}</span>
                          )}
                          {state?.source === 'dashboard' && (
                            <span className="text-[9px] px-1.5 py-0.5 rounded bg-gold/10 text-gold">dashboard</span>
                          )}
                          {state?.source === 'env' && (
                            <span className="text-[9px] px-1.5 py-0.5 rounded bg-teal/10 text-teal">.env</span>
                          )}
                          {!isEditing && (
                            <span className="text-[10px] text-muted-foreground/20 group-hover:text-muted-foreground/50 transition-colors">
                              {isConfigured ? 'click to update' : 'click to set'}
                            </span>
                          )}
                        </div>
                      </div>

                      {/* Expandable input */}
                      <AnimatePresence>
                        {isEditing && (
                          <div className="px-4 pb-4">
                            <KeyInput
                              keyId={keyDef.id}
                              placeholder={keyDef.placeholder}
                              onSave={(val) => handleSave(keyDef.id, val)}
                              onCancel={() => setEditing(null)}
                              saving={saving === keyDef.id}
                            />
                          </div>
                        )}
                      </AnimatePresence>
                    </div>
                  )
                })}
              </div>
            </GlowCard>
          </div>
        )
      })}
    </div>
  )
}
