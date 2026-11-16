import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowLeft, Bookmark, BookOpen, Users } from 'lucide-react'
import { api, type Book, type ChatReply, type Character, type Turn } from '../api'
import { chapterLabel, inSentence, recentParagraphs, type BookModel } from '../book'
import { annotate, sourcesLabel } from '../reply'
import { BookmarkRule, SettingsMenu, TopBar } from './common'
import { SourcePanel, type SourceView } from './SourcePanel'
import { SpeechButton } from './SpeechButton'
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
  return messages.map((m) => (m.role === 'reader'
    ? { role: 'reader', text: m.text }
    : { role: 'character', text: m.reply.reply, evidence_ids: [...new Set(m.reply.statements.flatMap((s) => s.evidence_ids))] }))
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
  const [pageOpen, setPageOpen] = useState(true)
  const [source, setSource] = useState<SourceView | null>(null)
  const [others, setOthers] = useState<Character[]>([])
  const [compact, setCompact] = useState(false)
  const scroller = useRef<HTMLDivElement>(null)
  const convoRef = useRef<HTMLElement>(null)
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

  // The heading overlays the messages and folds as they scroll: --p runs 0 → 1 over the
  // height it gives up, so the blend tracks the scroll exactly. Written straight to the
  // style (no re-render per scroll event); only the half-way flip goes through state,
  // to switch which version of the heading takes focus and clicks.
  const headFull = useRef<HTMLDivElement>(null)
  const fold = useRef(1) // px of scroll over which the heading folds
  function onScroll() {
    const el = scroller.current
    const convo = convoRef.current
    if (!el || !convo) return
    const p = Math.min(1, Math.max(0, el.scrollTop / fold.current))
    convo.style.setProperty('--p', p.toFixed(3))
    setCompact(p > 0.5)
  }
  useLayoutEffect(() => {
    const full = headFull.current
    const convo = convoRef.current
    if (!full || !convo) return
    const measure = () => {
      const height = full.offsetHeight // layout size: unaffected by the fold's transform
      const compactHeight = parseFloat(getComputedStyle(convo).getPropertyValue('--head-compact')) || 46
      convo.style.setProperty('--head-full', `${height}px`)
      fold.current = Math.max(1, height - compactHeight)
      onScroll()
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(full)
    return () => observer.disconnect()
  }, [])

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
  const paragraphs = pageOpen ? recentParagraphs(model, position, 6) : []
  const sourceMsg = source ? conversation.messages[source.msg] : null
  const activeNote = source && 'note' in source ? source.note : null
  // A source panel takes the page's place, so the toggle acts on what is on screen:
  // with a source open, "Show the page" swaps it for the page.
  const pageShown = pageOpen && !source
  function togglePage() {
    if (pageShown) { setPageOpen(false); return }
    setSource(null)
    setPageOpen(true)
  }
  // Real lines depend only on the bookmark, not the question, so any reply's lines
  // are the conversation's; use the latest.
  const lastReplyAt = conversation.messages.map((m) => m.role).lastIndexOf('character')

  return (
    <div className="app">
      <TopBar title={book.title} chapter={chapter} onBrand={onHome}>
        <button className="bar-link" onClick={togglePage} aria-pressed={pageShown}>
          <BookOpen size={14} strokeWidth={1.6} />
          <span className="label">{pageShown ? 'Hide the page' : 'Show the page'}</span>
        </button>
        <button className="bar-link" onClick={onChangeCharacter}>
          <Users size={14} strokeWidth={1.6} />
          <span className="label">Change character</span>
        </button>
        <SettingsMenu />
        <button className="bar-link strong" onClick={onBack}>
          <ArrowLeft size={14} strokeWidth={1.6} />
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
        ) : pageOpen && (
          <aside className="side wide">
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
        <main className="convo" ref={convoRef}>
          <div className="convo-head">
            <div className="head-full" ref={headFull} inert={compact}>
              <span className="kicker">Talk to</span>
              <span className="name">{name}</span>
              {lastReplyAt >= 0 && (
                <button className="link-quiet voice-link" onClick={() => setSource({ msg: lastReplyAt, voice: true })}>
                  How {nameInText} speaks
                </button>
              )}
            </div>
            <div className="head-compact" inert={!compact} aria-hidden={!compact}>
              <span className="kicker">Talk to</span>
              <span className="name">{name}</span>
              {lastReplyAt >= 0 && (
                <button className="link-quiet voice-link" onClick={() => setSource({ msg: lastReplyAt, voice: true })}>
                  How {nameInText} speaks
                </button>
              )}
            </div>
          </div>
          <div className="messages" ref={scroller} onScroll={onScroll} aria-live="polite">
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
                <div key={i} className={`msg-char${source && 'note' in source && source.msg !== i ? ' dimmed' : ''}`}>
                  <span className="kicker">{nameInText}</span>
                  <ReplyText
                    reply={m.reply}
                    animate={!!m.fresh}
                    activeNote={source?.msg === i ? activeNote : null}
                    onNote={(note) => setSource({ msg: i, note })}
                    onDone={() => settle(i)}
                  />
                  <div className="msg-foot" style={{ opacity: m.fresh ? 0 : 1 }}>
                    {character.has_audio_voice && <SpeechButton key={`${conversation.key}:${i}`} bookId={book.id} characterId={character.id} position={position} text={m.reply.reply} />}
                    {sourcesLabel(m.reply, model) && (
                      <button className="link-quiet" onClick={() => setSource({ msg: i, note: 0 })}>{sourcesLabel(m.reply, model)}</button>
                    )}
                    <button className="link-accent" onClick={() => onCompare(m.question, m.reply)}>
                      <Bookmark size={12} strokeWidth={1.6} />
                      Ask at another point in the book
                    </button>
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
