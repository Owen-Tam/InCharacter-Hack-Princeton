import { useEffect, useRef, useState, type FormEvent } from 'react'
import { AlertTriangle, Bookmark, BookOpen, Users } from 'lucide-react'
import { api, type Book, type ChatReply, type Character, type Turn } from '../api'
import { chapterLabel, inSentence, recentParagraphs, type BookModel } from '../book'
import { annotate, sourcesLabel } from '../reply'
import { BookmarkRule, TopBar } from './common'
import { SourcePanel, type SourceView } from './SourcePanel'
import { byPresence } from './Cast'

export type Message =
  | { role: 'reader'; text: string }
  | { role: 'character'; question: string; reply: ChatReply; fresh?: boolean }

export interface Conversation {
  key: string
  messages: Message[]
  pending: boolean
  error: string | null
}

export const conversationKey = (bookId: number, characterId: number, position: number) => `${bookId}:${characterId}:${position}`

export function historyOf(messages: Message[]): Turn[] {
  return messages.map((m) => (m.role === 'reader' ? { role: 'reader', text: m.text } : { role: 'character', text: m.reply.reply }))
}

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

function ReplyText({ reply, animate, activeNote, onNote, onDone }: {
  reply: ChatReply
  animate: boolean
  activeNote: number | null
  onNote: (note: number) => void
  onDone: () => void
}) {
  const sentences = annotate(reply)
  const [shown, setShown] = useState(animate ? 0 : sentences.length)
  useEffect(() => {
    if (!animate) return
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    if (reduce) { setShown(sentences.length); onDone(); return }
    let n = 0
    const t = window.setInterval(() => {
      n++
      setShown(n)
      if (n >= sentences.length) { window.clearInterval(t); onDone() }
    }, 650)
    return () => window.clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [animate])
  return (
    <p className="reply">
      {sentences.map((s, i) => (
        <span key={i} className={`sent${activeNote !== null && s.notes.includes(activeNote) ? ' hl' : ''}`} style={{ opacity: i < shown ? 1 : 0 }}>
          {s.text.trimEnd()}
          {s.notes.map((n) => (
            <button key={n} className={`note-ref${activeNote === n ? ' on' : ''}`} onClick={() => onNote(n)} aria-label={`Note ${n + 1}`}>
              {n + 1}
            </button>
          ))}
          {s.text.slice(s.text.trimEnd().length)}
        </span>
      ))}
    </p>
  )
}

export function ConversationScreen({ book, model, position, page, character, conversation, onUpdate, onBack, onChangeCharacter, onCompare, onReadAt, onHome }: {
  book: Book
  model: BookModel
  position: number
  page: number
  character: Character
  conversation: Conversation
  onUpdate: (c: Conversation) => void
  onBack: () => void
  onChangeCharacter: () => void
  onCompare: (question: string, reply: ChatReply) => void
  onReadAt: (passageId: number) => void
  onHome: () => void
}) {
  const [draft, setDraft] = useState('')
  const [pageOpen, setPageOpen] = useState(false)
  const [source, setSource] = useState<SourceView | null>(null)
  const [others, setOthers] = useState<Character[]>([])
  const scroller = useRef<HTMLDivElement>(null)
  const latest = useRef(conversation)
  latest.current = conversation
  const chapter = chapterLabel(model, position)
  const name = character.name
  // The API sends the fullest name known at the bookmark; use it as is ("the narrator" mid-sentence).
  const nameInText = inSentence(name)

  useEffect(() => {
    let live = true
    api.characters(book.id, position).then((cast) => live && setOthers(byPresence(cast.filter((c) => c.id !== character.id))), () => {})
    return () => { live = false }
  }, [book.id, position, character.id])

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' })
  }, [conversation.messages.length, conversation.pending])

  async function ask(question: string) {
    question = question.trim()
    if (!question || conversation.pending) return
    const history = historyOf(conversation.messages)
    const messages: Message[] = [...conversation.messages, { role: 'reader', text: question }]
    onUpdate({ ...conversation, messages, pending: true, error: null })
    setDraft('')
    try {
      const reply = await api.chat({ book_id: book.id, character_id: character.id, position, question, history })
      onUpdate({ ...latest.current, messages: [...messages, { role: 'character', question, reply, fresh: true }], pending: false })
    } catch (e) {
      // Put the question back so the reader can retry it.
      onUpdate({ ...latest.current, messages: conversation.messages, pending: false, error: (e as Error).message })
      setDraft(question)
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault()
    ask(draft)
  }

  function settle(index: number) {
    const m = latest.current.messages[index]
    if (m?.role !== 'character' || !m.fresh) return
    const messages = [...latest.current.messages]
    messages[index] = { ...m, fresh: false }
    onUpdate({ ...latest.current, messages })
  }

  const suggestions = [
    ...others.slice(0, 2).map((o) => `What do you make of ${inSentence(o.name)}?`),
    'What is on your mind right now?',
  ]
  const paragraphs = recentParagraphs(model, position, pageOpen ? 6 : 2)
  const sourceMsg = source ? conversation.messages[source.msg] : null
  const activeNote = source && 'note' in source ? source.note : null

  return (
    <div className="app">
      <TopBar title={book.title} chapter={chapter} onBrand={onHome}>
        <button className="bar-link" onClick={onChangeCharacter}>
          <Users size={14} strokeWidth={1.6} />
          <span className="label">Change character</span>
        </button>
        <button className="bar-link" onClick={onBack}>
          <BookOpen size={14} strokeWidth={1.6} />
          <span className="label">Back to page {page}</span>
        </button>
      </TopBar>
      <div className="split">
        {source && sourceMsg?.role === 'character' ? (
          <SourcePanel
            view={source}
            reply={sourceMsg.reply}
            model={model}
            position={position}
            characterName={name}
            onNote={(note) => setSource({ msg: source.msg, note })}
            onClose={() => setSource(null)}
            onReadAt={onReadAt}
          />
        ) : (
          <aside className={`side${pageOpen ? ' wide' : ''}`}>
            <div>
              <button className="btn" onClick={() => setPageOpen((o) => !o)}>
                <BookOpen size={14} strokeWidth={1.6} />
                {pageOpen ? 'Hide the page' : 'Show the page'}
              </button>
            </div>
            <div className="left-off">
              <span className="small muted">Where you left off{chapter ? ` · ${chapter}` : ''}</span>
              <div className="left-off-text">{paragraphs.map((p) => <p key={p.passage.id}>{p.text}</p>)}</div>
              <BookmarkRule />
            </div>
            {others.length > 0 && (
              <div className="side-foot">
                <span className="small muted">Others you have met</span>
                <span className="names">{others.slice(0, 3).map((o) => o.name).join(' · ')}</span>
              </div>
            )}
          </aside>
        )}
        <main className="convo">
          <div className="convo-head">
            <span className="kicker">A conversation with</span>
            <span className="name">{name}</span>
          </div>
          <div className="messages" ref={scroller} aria-live="polite">
            {conversation.messages.length === 0 && !conversation.pending && (
              <div className="empty">
                {capitalize(nameInText)} knows the story only as far as your bookmark{chapter ? `, in ${chapter}` : ''}. Ask anything.
              </div>
            )}
            {conversation.messages.map((m, i) =>
              m.role === 'reader' ? (
                <div key={i} className="msg-you">
                  <span className="kicker muted">You</span>
                  <div className="bubble">{m.text}</div>
                </div>
              ) : (
                <div key={i} className={`msg-char${source && source.msg !== i ? ' dimmed' : ''}`}>
                  <span className="kicker">{nameInText}</span>
                  <ReplyText
                    reply={m.reply}
                    animate={!!m.fresh}
                    activeNote={source?.msg === i ? activeNote : null}
                    onNote={(note) => setSource({ msg: i, note })}
                    onDone={() => settle(i)}
                  />
                  <div className="msg-foot" style={{ opacity: m.fresh ? 0 : 1 }}>
                    <span className="imagined">Imagined reply</span>
                    {sourcesLabel(m.reply, model) && (
                      <button className="link-quiet" onClick={() => setSource({ msg: i, note: 0 })}>{sourcesLabel(m.reply, model)}</button>
                    )}
                    {m.reply.voice_lines.length > 0 && (
                      <button className="link-quiet" onClick={() => setSource({ msg: i, voice: true })}>Real lines</button>
                    )}
                    <button className="link-accent" onClick={() => onCompare(m.question, m.reply)}>
                      <Bookmark size={12} strokeWidth={1.6} />
                      Ask at another point in the book
                    </button>
                    {m.reply.unsupported_statements.length > 0 && (
                      <span className="unsupported" title={m.reply.unsupported_statements.join('\n')}>
                        <AlertTriangle size={12} strokeWidth={1.6} />
                        Some of this isn't backed by the book
                      </span>
                    )}
                  </div>
                </div>
              ),
            )}
            {conversation.pending && <div className="msg-char"><span className="kicker">{nameInText}</span><span className="thinking">{capitalize(nameInText)} is thinking…</span></div>}
            {conversation.error && (
              <div className="msg-error" role="alert">
                <span>{conversation.error}</span>
                <button className="link-quiet" onClick={() => ask(draft)}>Try again</button>
              </div>
            )}
          </div>
          <form className="composer" onSubmit={submit}>
            {conversation.messages.length === 0 && (
              <div className="suggestions">
                {suggestions.map((s) => <button type="button" key={s} onClick={() => ask(s)} disabled={conversation.pending}>{s}</button>)}
              </div>
            )}
            <div className="ask">
              <label className="sr-only" htmlFor="ask">Ask {nameInText}</label>
              <input id="ask" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={`Ask ${nameInText} something…`} autoComplete="off" autoFocus />
              <button type="submit" disabled={!draft.trim() || conversation.pending}>Enter</button>
            </div>
          </form>
        </main>
      </div>
    </div>
  )
}
