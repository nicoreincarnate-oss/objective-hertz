'use client'

import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Send, Bot, Zap, Shield, AlertCircle, CheckCircle2, Clock } from 'lucide-react'
import useSWR, { mutate } from 'swr'

interface Message {
  id: string
  target_agent: string
  priority: string
  message: string
  created_at: string
  acknowledged: boolean
  response?: string
  response_at?: string
}

const agents = [
  { id: 'perseus', name: 'Perseus', description: 'Lead discovery & outreach', icon: <Zap className="w-4 h-4" /> },
  { id: 'titan', name: 'Titan', description: 'Demo building & proposals', icon: <Bot className="w-4 h-4" /> },
  { id: 'clawdbot', name: 'ClawdBot', description: 'Security & compliance', icon: <Shield className="w-4 h-4" /> }
]

const priorities = [
  { id: 'urgent', name: 'Urgent', color: 'red' },
  { id: 'priority', name: 'Priority', color: 'amber' },
  { id: 'routine', name: 'Routine', color: 'muted' }
]

export function AgentChat({ token }: { token: string | null }) {
  const [selectedAgent, setSelectedAgent] = useState('perseus')
  const [selectedPriority, setSelectedPriority] = useState('routine')
  const [message, setMessage] = useState('')
  const [isSending, setIsSending] = useState(false)

  const fetcher = (url: string) => fetch(url).then(res => res.json())
  const { data: messages = [] } = useSWR<Message[]>(
    token ? `/api/operator-chat?token=${token}` : null,
    fetcher,
    { refreshInterval: 5000 }
  )

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!token || !message.trim() || isSending) return

    setIsSending(true)
    try {
      const formData = new FormData()
      formData.append('target_agent', selectedAgent)
      formData.append('priority', selectedPriority)
      formData.append('message', message)

      await fetch(`/api/operator-chat?token=${token}`, {
        method: 'POST',
        body: formData
      })

      setMessage('')
      mutate(`/api/operator-chat?token=${token}`)
    } finally {
      setIsSending(false)
    }
  }

  const formatTime = (dateString: string) => {
    const date = new Date(dateString)
    return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
  }

  const getPriorityColor = (priority: string) => {
    switch (priority) {
      case 'urgent': return 'border-l-red'
      case 'priority': return 'border-l-amber'
      default: return 'border-l-muted-foreground'
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: 0.3 }}
      className="glass-card hud-panel rounded-xl p-5 md:p-6"
    >
      <h2 className="text-lg font-semibold text-foreground mb-4 flex items-center gap-2">
        <Bot className="w-5 h-5 text-gold" />
        Agent Link
      </h2>

      <form onSubmit={handleSubmit} className="space-y-4">
        {/* Agent Selection */}
        <div>
          <label className="text-xs text-muted-foreground uppercase tracking-wider mb-2 block">
            Target Agent
          </label>
          <div className="grid grid-cols-3 gap-2">
            {agents.map((agent) => (
              <button
                key={agent.id}
                type="button"
                onClick={() => setSelectedAgent(agent.id)}
                className={`p-3 rounded-lg border text-left transition-all ${
                  selectedAgent === agent.id
                    ? 'bg-gold/10 border-gold/40 text-gold'
                    : 'bg-muted/50 border-border text-muted-foreground hover:border-gold/20'
                }`}
              >
                <div className="flex items-center gap-2 mb-1">
                  {agent.icon}
                  <span className="text-sm font-medium">{agent.name}</span>
                </div>
                <span className="text-[10px] opacity-70 hidden md:block">{agent.description}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Priority Selection */}
        <div>
          <label className="text-xs text-muted-foreground uppercase tracking-wider mb-2 block">
            Priority
          </label>
          <div className="flex gap-2">
            {priorities.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => setSelectedPriority(p.id)}
                className={`px-4 py-2 rounded-lg border text-sm font-medium transition-all ${
                  selectedPriority === p.id
                    ? p.color === 'red' 
                      ? 'bg-red/10 border-red/40 text-red'
                      : p.color === 'amber'
                      ? 'bg-amber/10 border-amber/40 text-amber'
                      : 'bg-muted border-border text-foreground'
                    : 'bg-muted/50 border-border text-muted-foreground hover:border-gold/20'
                }`}
              >
                {p.name}
              </button>
            ))}
          </div>
        </div>

        {/* Message Input */}
        <div>
          <label className="text-xs text-muted-foreground uppercase tracking-wider mb-2 block">
            Message
          </label>
          <textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder="Enter your message to the agent..."
            rows={3}
            className="w-full bg-muted/50 border border-border rounded-lg px-4 py-3 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-gold/40 focus:ring-1 focus:ring-gold/20 resize-none"
          />
        </div>

        {/* Submit Button */}
        <motion.button
          type="submit"
          disabled={!message.trim() || isSending || !token}
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          className="w-full py-3 px-4 rounded-lg bg-gradient-to-r from-gold to-gold-dim text-background font-semibold flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed hover:shadow-lg hover:shadow-gold/20 transition-shadow"
        >
          <Send className="w-4 h-4" />
          {isSending ? 'Sending...' : 'Send To Agent'}
        </motion.button>
      </form>

      {/* Message Thread */}
      {messages.length > 0 && (
        <div className="mt-6 space-y-3">
          <h3 className="text-xs text-muted-foreground uppercase tracking-wider">
            Recent Messages
          </h3>
          <div className="space-y-2 max-h-60 overflow-y-auto pr-2">
            <AnimatePresence>
              {messages.slice(0, 5).map((msg) => (
                <motion.div
                  key={msg.id}
                  initial={{ opacity: 0, x: -10 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 10 }}
                  className={`p-3 rounded-lg bg-muted/30 border-l-2 ${getPriorityColor(msg.priority)}`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-medium text-gold capitalize">
                        → {msg.target_agent}
                      </span>
                      <span className="text-[10px] text-muted-foreground">
                        {formatTime(msg.created_at)}
                      </span>
                    </div>
                    {msg.acknowledged ? (
                      <CheckCircle2 className="w-3.5 h-3.5 text-green" />
                    ) : (
                      <Clock className="w-3.5 h-3.5 text-amber animate-pulse" />
                    )}
                  </div>
                  <p className="text-sm text-foreground">{msg.message}</p>
                  {msg.response && (
                    <div className="mt-2 pt-2 border-t border-border">
                      <p className="text-xs text-green">{msg.response}</p>
                    </div>
                  )}
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
        </div>
      )}
    </motion.div>
  )
}
