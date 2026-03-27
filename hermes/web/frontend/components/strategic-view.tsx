'use client'

import { useState, useRef, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Brain, Loader2, Search, Sparkles, User } from 'lucide-react'
import { authHeaders } from '@/hooks/use-token'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useWarRoom } from '@/contexts/war-room-context'
import { TypingAnimation } from '@/components/ui/typing-animation'

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: Date
  isStreaming?: boolean
}

const SUGGESTED_QUESTIONS = [
  "What's the biggest pipeline bottleneck right now?",
  "Which industry has the highest conversion rate?",
  "Why did close rate change this week?",
  "What should I focus on today?",
]

export function StrategicView({ token }: { token: string | null }) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  // Get pipeline context for dynamic suggestions
  let pipelineContext: Record<string, number> = {}
  try {
    const ctx = useWarRoom()
    pipelineContext = ctx.pipeline
  } catch {
    // Outside provider
  }

  // Auto-scroll on new messages
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages])

  const askQuestion = async (question: string) => {
    if (!token || !question.trim() || isStreaming) return

    const userMsg: ChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: question.trim(),
      timestamp: new Date(),
    }

    const assistantId = `assistant-${Date.now()}`
    const assistantMsg: ChatMessage = {
      id: assistantId,
      role: 'assistant',
      content: '',
      timestamp: new Date(),
      isStreaming: true,
    }

    setMessages(prev => [...prev, userMsg, assistantMsg])
    setInput('')
    setIsStreaming(true)

    try {
      // Try streaming endpoint first
      const res = await fetch('/api/insights/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? authHeaders(token) : {}) },
        body: JSON.stringify({ question: question.trim() }),
      })

      if (res.ok && res.body) {
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let accumulated = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          const chunk = decoder.decode(value, { stream: true })
          const lines = chunk.split('\n')

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const data = JSON.parse(line.slice(6))
                if (data.token) {
                  accumulated += data.token
                  setMessages(prev =>
                    prev.map(m => m.id === assistantId ? { ...m, content: accumulated } : m)
                  )
                }
                if (data.done) break
                if (data.error) {
                  accumulated += `\n\nError: ${data.error}`
                  setMessages(prev =>
                    prev.map(m => m.id === assistantId ? { ...m, content: accumulated, isStreaming: false } : m)
                  )
                }
              } catch {
                // Skip malformed SSE lines
              }
            }
          }
        }

        // Mark as done
        setMessages(prev =>
          prev.map(m => m.id === assistantId ? { ...m, isStreaming: false } : m)
        )
      } else {
        // Fallback to non-streaming /api/insights
        const fallbackRes = await fetch('/api/insights', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...(token ? authHeaders(token) : {}) },
          body: JSON.stringify({ question: question.trim() }),
        })

        if (fallbackRes.ok) {
          const data = await fallbackRes.json()
          let fullResponse = data.answer || 'No response.'
          if (data.recommended_actions?.length) {
            fullResponse += '\n\n**Recommended actions:**\n' +
              data.recommended_actions.map((a: string) => `• ${a}`).join('\n')
          }
          setMessages(prev =>
            prev.map(m => m.id === assistantId ? { ...m, content: fullResponse, isStreaming: false } : m)
          )
        } else {
          setMessages(prev =>
            prev.map(m => m.id === assistantId ? { ...m, content: 'Strategic view unavailable.', isStreaming: false } : m)
          )
        }
      }
    } catch {
      setMessages(prev =>
        prev.map(m => m.id === assistantId ? { ...m, content: 'Connection error.', isStreaming: false } : m)
      )
    } finally {
      setIsStreaming(false)
    }
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    askQuestion(input)
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: 0.35 }}
      className="glass-card-elevated hud-panel rounded-xl p-5 md:p-6 flex flex-col"
      style={{ minHeight: 380 }}
    >
      <h2 className="text-lg font-semibold text-foreground mb-2 flex items-center gap-2">
        <Brain className="w-5 h-5 text-gold" />
        <TypingAnimation
          text="God's-Eye View"
          duration={60}
          className="text-lg font-semibold leading-none tracking-normal drop-shadow-none"
        />
      </h2>
      <p className="text-xs text-muted-foreground mb-4">
        Ask strategic questions grounded in real pipeline data
      </p>

      {/* Chat messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto space-y-3 mb-4 min-h-[120px] max-h-[300px] pr-1">
        {messages.length === 0 ? (
          <div className="text-center py-6">
            <Sparkles className="w-8 h-8 text-gold/30 mx-auto mb-3" />
            <p className="text-xs text-muted-foreground mb-4">Ask a question to get started</p>

            {/* Suggested questions */}
            <div className="flex flex-wrap gap-2 justify-center">
              {SUGGESTED_QUESTIONS.map((q) => (
                <button
                  key={q}
                  onClick={() => askQuestion(q)}
                  disabled={isStreaming}
                  className="px-3 py-1.5 rounded-full border border-border bg-muted/30 text-xs text-muted-foreground hover:text-foreground hover:border-gold/30 transition-colors disabled:opacity-50"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <AnimatePresence mode="popLayout">
            {messages.map((msg) => (
              <motion.div
                key={msg.id}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                className={`flex gap-2 ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
              >
                {msg.role === 'assistant' && (
                  <div className="w-7 h-7 rounded-full bg-gold/10 border border-gold/20 flex items-center justify-center shrink-0">
                    <Brain className="w-3.5 h-3.5 text-gold" />
                  </div>
                )}

                <div className={`max-w-[85%] rounded-xl px-3.5 py-2.5 ${
                  msg.role === 'user'
                    ? 'bg-gold/12 border border-gold/20 rounded-tr-none'
                    : 'glass-card rounded-tl-none'
                }`}>
                  <p className="text-sm text-foreground whitespace-pre-wrap leading-relaxed">
                    {msg.content}
                    {msg.isStreaming && (
                      <span className="inline-block w-1.5 h-4 bg-gold/60 ml-0.5 animate-pulse" />
                    )}
                  </p>
                </div>

                {msg.role === 'user' && (
                  <div className="w-7 h-7 rounded-full bg-muted border border-border flex items-center justify-center shrink-0">
                    <User className="w-3.5 h-3.5 text-muted-foreground" />
                  </div>
                )}
              </motion.div>
            ))}
          </AnimatePresence>
        )}
      </div>

      {/* Suggested questions (show again after conversation) */}
      {messages.length > 0 && !isStreaming && (
        <div className="flex gap-2 mb-3 overflow-x-auto pb-1">
          {SUGGESTED_QUESTIONS.slice(0, 3).map((q) => (
            <button
              key={q}
              onClick={() => askQuestion(q)}
              className="shrink-0 px-2.5 py-1 rounded-full border border-border bg-muted/20 text-[10px] text-muted-foreground hover:text-foreground hover:border-gold/30 transition-colors"
            >
              {q.length > 35 ? q.slice(0, 35) + '...' : q}
            </button>
          ))}
        </div>
      )}

      {/* Input */}
      <form onSubmit={handleSubmit} className="flex gap-2 items-end">
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              handleSubmit(e)
            }
          }}
          placeholder="Ask a strategic question..."
          rows={1}
          className="flex-1 bg-muted/50 border border-border rounded-xl px-4 py-2.5 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-gold/40 focus:ring-1 focus:ring-gold/20 resize-none overflow-hidden"
          style={{ minHeight: 40 }}
        />
        <motion.button
          type="submit"
          disabled={!input.trim() || isStreaming || !token}
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
          className="shrink-0 w-10 h-10 rounded-xl bg-gradient-to-br from-gold to-gold-dim text-background flex items-center justify-center disabled:opacity-40 disabled:cursor-not-allowed hover:shadow-lg hover:shadow-gold/20 transition-shadow"
        >
          {isStreaming ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
        </motion.button>
      </form>
    </motion.div>
  )
}
