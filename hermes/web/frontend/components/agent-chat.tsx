'use client'

import { useState, useRef, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Send, Bot, Zap, Shield, Radio, CheckCircle2, Check, Clock, Loader2,
} from 'lucide-react'
import useSWR, { mutate } from 'swr'
import { authHeaders } from '@/hooks/use-token'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Dock, DockIcon } from '@/components/ui/dock'

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

type MessageStatus = 'sending' | 'sent' | 'delivered' | 'acknowledged'

const AGENTS = [
  { id: 'perseus', name: 'Perseus', role: 'CEO / Scheduler', icon: Zap, color: '#58e0ff', avatar: '/assets/generated/agents/perseus.svg' },
  { id: 'titan', name: 'Titan', role: 'Revenue Engine', icon: Bot, color: '#39f3e2', avatar: '/assets/generated/agents/titan.svg' },
  { id: 'clawdbot', name: 'ClawdBot', role: 'Site Builder', icon: Shield, color: '#62f1b5', avatar: '/assets/generated/agents/clawdbot.svg' },
  { id: 'hermes', name: 'Hermes', role: 'Alerts / Comms', icon: Radio, color: '#ffb347', avatar: '/assets/generated/agents/hermes.svg' },
  { id: 'deerflow_research', name: 'DeerFlow', role: 'Continuous R&D', icon: Bot, color: '#b794ff', avatar: '/assets/generated/agents/hermes.svg' },
]

const PRIORITIES = [
  { id: 'urgent', name: 'Urgent', color: 'red' },
  { id: 'priority', name: 'Priority', color: 'amber' },
  { id: 'routine', name: 'Routine', color: 'muted' },
]

function AgentAvatar({ agentId, size = 'sm' }: { agentId: string; size?: 'sm' | 'md' }) {
  const agent = AGENTS.find(a => a.id === agentId)
  if (!agent) return null
  const dim = size === 'md' ? 'w-9 h-9' : 'w-7 h-7'

  return (
    <img
      src={agent.avatar}
      alt={agent.name}
      className={`${dim} rounded-full shrink-0`}
    />
  )
}

function StatusIndicator({ status }: { status: MessageStatus }) {
  switch (status) {
    case 'sending':
      return <Loader2 className="w-3 h-3 text-muted-foreground animate-spin" />
    case 'sent':
      return <Check className="w-3 h-3 text-muted-foreground" />
    case 'delivered':
      return (
        <span className="inline-flex -space-x-1">
          <Check className="w-3 h-3 text-muted-foreground" />
          <Check className="w-3 h-3 text-muted-foreground" />
        </span>
      )
    case 'acknowledged':
      return (
        <span className="inline-flex -space-x-1">
          <Check className="w-3 h-3 text-green" />
          <Check className="w-3 h-3 text-green" />
        </span>
      )
  }
}

function TypingIndicator({ agentId }: { agentId: string }) {
  const agent = AGENTS.find(a => a.id === agentId)
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -10 }}
      className="flex items-start gap-2"
    >
      <AgentAvatar agentId={agentId} />
      <div className="glass-card rounded-xl rounded-tl-none px-4 py-2.5">
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">{agent?.name} is thinking</span>
          <motion.span
            className="flex gap-0.5"
            initial="start"
            animate="end"
          >
            {[0, 1, 2].map(i => (
              <motion.span
                key={i}
                className="w-1.5 h-1.5 rounded-full bg-muted-foreground"
                animate={{ opacity: [0.3, 1, 0.3] }}
                transition={{ duration: 1, repeat: Infinity, delay: i * 0.2 }}
              />
            ))}
          </motion.span>
        </div>
      </div>
    </motion.div>
  )
}

export function AgentChat({ token }: { token: string | null }) {
  const [selectedAgent, setSelectedAgent] = useState('titan')
  const [selectedPriority, setSelectedPriority] = useState('routine')
  const [message, setMessage] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [pendingAgent, setPendingAgent] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const fetcher = (url: string) => fetch(url, { headers: token ? authHeaders(token) : {} }).then(res => res.json())
  const { data: messages = [] } = useSWR<Message[]>(
    token ? '/api/operator-chat' : null,
    fetcher,
    { refreshInterval: 5000 }
  )

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages])

  // Clear typing indicator when ack arrives
  useEffect(() => {
    if (pendingAgent && messages.some(m => m.target_agent === pendingAgent && m.acknowledged)) {
      setPendingAgent(null)
    }
  }, [messages, pendingAgent])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!token || !message.trim() || isSending) return

    setIsSending(true)
    setPendingAgent(selectedAgent)
    try {
      const formData = new FormData()
      formData.append('target_agent', selectedAgent)
      formData.append('priority', selectedPriority)
      formData.append('message', message)

      await fetch('/api/operator-chat', {
        method: 'POST',
        headers: authHeaders(token),
        body: formData,
      })

      setMessage('')
      mutate('/api/operator-chat')
    } finally {
      setIsSending(false)
    }
  }

  // Auto-resize textarea
  const handleTextareaChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setMessage(e.target.value)
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 120)}px`
    }
  }

  const formatTime = (dateString: string) => {
    const date = new Date(dateString)
    return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
  }

  const getStatus = (msg: Message): MessageStatus => {
    if (msg.response) return 'acknowledged'
    if (msg.acknowledged) return 'delivered'
    return 'sent'
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: 0.3 }}
      className="glass-card-elevated hud-panel rounded-xl p-5 md:p-6 flex flex-col"
      style={{ minHeight: 420 }}
    >
      <h2 className="text-lg font-semibold text-foreground mb-4 flex items-center gap-2">
        <Bot className="w-5 h-5 text-gold" />
        Agent Link
      </h2>

      {/* Agent selector — macOS-style Dock with magnification */}
      <div className="flex justify-center mb-4">
        <Dock
          magnification={52}
          distance={100}
          direction="bottom"
          className="border-gold/20 bg-background/60"
        >
          {AGENTS.map((agent) => (
            <DockIcon
              key={agent.id}
              className={`transition-all ${
                selectedAgent === agent.id
                  ? 'ring-2 ring-gold/50 bg-gold/10'
                  : 'hover:bg-white/5'
              }`}
            >
              <button
                type="button"
                onClick={() => setSelectedAgent(agent.id)}
                className="w-full h-full flex items-center justify-center"
                title={`${agent.name} — ${agent.role}`}
              >
                <img
                  src={agent.avatar}
                  alt={agent.name}
                  className="w-full h-full rounded-full object-cover"
                />
              </button>
            </DockIcon>
          ))}
        </Dock>
      </div>

      {/* Message thread — chat bubbles */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto space-y-3 mb-3 min-h-[140px] max-h-[260px] pr-1">
        <AnimatePresence mode="popLayout">
          {messages.slice(0, 8).map((msg) => {
            const agent = AGENTS.find(a => a.id === msg.target_agent)
            return (
              <div key={msg.id}>
                {/* Operator message — right side */}
                <motion.div
                  initial={{ opacity: 0, x: 20 }}
                  animate={{ opacity: 1, x: 0 }}
                  className="flex justify-end gap-2 mb-2"
                >
                  <div className="max-w-[80%]">
                    <div className="bg-gold/12 border border-gold/20 rounded-xl rounded-tr-none px-3 py-2">
                      <p className="text-sm text-foreground">{msg.message}</p>
                    </div>
                    <div className="flex items-center justify-end gap-1.5 mt-0.5 px-1">
                      <span className="text-[10px] text-muted-foreground">{formatTime(msg.created_at)}</span>
                      <span className="text-[10px] text-muted-foreground">→ {msg.target_agent}</span>
                      <StatusIndicator status={getStatus(msg)} />
                    </div>
                  </div>
                </motion.div>

                {/* Agent response — left side */}
                {msg.response && (
                  <motion.div
                    initial={{ opacity: 0, x: -20 }}
                    animate={{ opacity: 1, x: 0 }}
                    className="flex items-start gap-2"
                  >
                    <AgentAvatar agentId={msg.target_agent} />
                    <div className="max-w-[80%]">
                      <div className="glass-card rounded-xl rounded-tl-none px-3 py-2">
                        <p className="text-sm text-foreground">{msg.response}</p>
                      </div>
                      <span className="text-[10px] text-muted-foreground px-1">
                        {msg.response_at ? formatTime(msg.response_at) : ''}
                      </span>
                    </div>
                  </motion.div>
                )}
              </div>
            )
          })}

          {/* Typing indicator */}
          {pendingAgent && isSending && (
            <TypingIndicator agentId={pendingAgent} />
          )}
        </AnimatePresence>

        {messages.length === 0 && !isSending && (
          <div className="text-center py-8 text-muted-foreground text-xs">
            Send a message to any agent to get started
          </div>
        )}
      </div>

      {/* Input area */}
      <form onSubmit={handleSubmit} className="flex gap-2 items-end">
        {/* Priority dot */}
        <button
          type="button"
          onClick={() => {
            const idx = PRIORITIES.findIndex(p => p.id === selectedPriority)
            setSelectedPriority(PRIORITIES[(idx + 1) % PRIORITIES.length].id)
          }}
          className="shrink-0 mb-2"
          title={`Priority: ${selectedPriority}`}
        >
          <div className={`w-3 h-3 rounded-full ${
            selectedPriority === 'urgent' ? 'bg-red' : selectedPriority === 'priority' ? 'bg-amber' : 'bg-muted-foreground/50'
          }`} />
        </button>

        <textarea
          ref={textareaRef}
          value={message}
          onChange={handleTextareaChange}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              handleSubmit(e)
            }
          }}
          placeholder={`Message ${AGENTS.find(a => a.id === selectedAgent)?.name}...`}
          rows={1}
          className="flex-1 bg-muted/50 border border-border rounded-xl px-4 py-2.5 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-gold/40 focus:ring-1 focus:ring-gold/20 resize-none overflow-hidden"
          style={{ minHeight: 40 }}
        />

        <motion.button
          type="submit"
          disabled={!message.trim() || isSending || !token}
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
          className="shrink-0 w-10 h-10 rounded-xl bg-gradient-to-br from-gold to-gold-dim text-background flex items-center justify-center disabled:opacity-40 disabled:cursor-not-allowed hover:shadow-lg hover:shadow-gold/20 transition-shadow mb-0.5"
        >
          <Send className="w-4 h-4" />
        </motion.button>
      </form>
    </motion.div>
  )
}
