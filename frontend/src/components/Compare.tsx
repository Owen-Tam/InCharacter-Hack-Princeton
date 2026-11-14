import { useState } from 'react'
import { BookOpen, ChevronLeft } from 'lucide-react'
import { api, type Book, type ChatReply, type Character } from '../api'
import { chapterLabel, inSentence, shortChapter, spreadForPosition, type BookModel } from '../book'
import { sourcesLabel, splitSentences } from '../reply'
import { TopBar } from './common'

export interface CompareSeed {
  question: string
  reply: ChatReply
  position: number
}

function momentLabel(model: BookModel, position: number) {
  const spread = spreadForPosition(model, position)
  const chapter = chapterLabel(model, position)
  return `Bookmark · ${chapter ? `${chapter}, ` : ''}page ${spread.firstPage}`
}

function Moment({ label, name, reply, model, later, newSince }: {
  label: string
  name: string
  reply: ChatReply
  model: BookModel
  later?: boolean
  newSince?: number // show citations that became available after this position
}) {
  const fresh = newSince === undefined ? [] : reply.citations.filter((c) => c.available_from > newSince)
  return (
    <div className={`moment${later ? ' later' : ''}`}>
      <span className="small" style={{ color: later ? 'var(--color-accent-400)' : 'var(--color-neutral-500)' }}>{label}</span>
      <span className={`kicker${later ? '' : ' muted'}`}>{name}</span>
      <p className="reply">{splitSentences(reply.reply).join('')}</p>
      {sourcesLabel(reply, model) && <span className="tiny muted">{sourcesLabel(reply, model)}</span>}
      {newSince !== undefined && (
        <div className="new-since">
          {fresh.length ? (
            <>
              <span className="small muted">
                New since the earlier bookmark{shortChapter(model, fresh[0].available_from) ? ` · ${shortChapter(model, fresh[0].available_from)}` : ''}
              </span>
              {fresh.map((c) => (
                <span key={c.evidence_id} className="q" title={c.claim}>{c.sources[0]?.quote || c.claim}</span>
              ))}
            </>
          ) : (
            <span className="small muted">Nothing they cited is new since the earlier bookmark.</span>
          )}
        </div>
      )}
    </div>
  )
}

export function Compare({ book, model, character, seed, page, onBackToChat, onBack, onReadFrom, onContinueAt, onHome }: {
  book: Book
  model: BookModel
  character: Character
  seed: CompareSeed
  page: number
  onBackToChat: () => void
  onBack: () => void
  onReadFrom: (position: number) => void
  onContinueAt: (position: number, question: string, reply: ChatReply) => void
  onHome: () => void
}) {
  const max = Math.max(1, book.indexed_through)
  // Default to the end of the next chapter (or as far as the characters know).
  const nextChapters = model.chapters.filter((c) => c.position > seed.position)
  const defaultOther = Math.min(max, spreadForPosition(model, nextChapters[1] ? nextChapters[1].position - 1 : max).position)
  const [other, setOther] = useState(defaultOther === seed.position ? Math.max(1, seed.position - 1) : defaultOther)
  const [result, setResult] = useState<{ position: number; reply: ChatReply } | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function askAgain() {
    setPending(true)
    setError(null)
    try {
      const reply = await api.chat({ book_id: book.id, character_id: character.id, position: other, question: seed.question, history: [] })
      setResult({ position: other, reply })
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setPending(false)
    }
  }

  const first = { position: seed.position, reply: seed.reply }
  const [early, late] = result && result.position < first.position ? [result, first] : [first, result]
  const total = model.totalSentences || max
  const pct = (p: number) => `${(p / total) * 100}%`
  const lo = Math.min(seed.position, other)
  const hi = Math.max(seed.position, other)
  const firstCh = model.chapters[0]?.label.replace(/^chapter\s+/i, '')
  const lastCh = model.chapters[model.chapters.length - 1]?.label.replace(/^chapter\s+/i, '')
  const earlierChapter = early ? chapterLabel(model, early.position) : null
  const notMetYet = other < character.first_seen

  return (
    <div className="app">
      <TopBar title={book.title} chapter={chapterLabel(model, seed.position)} onBrand={onHome}>
        <button className="bar-link" onClick={onBackToChat}>
          <ChevronLeft size={15} strokeWidth={1.6} />
          <span className="label">Back to conversation</span>
        </button>
        <button className="bar-link" onClick={onBack}>
          <BookOpen size={14} strokeWidth={1.6} />
          <span className="label">Back to page {page}</span>
        </button>
      </TopBar>
      <main className="compare">
        <div className="compare-head">
          <span className="small" style={{ color: 'var(--color-accent-400)' }}>
            {result ? `You asked ${inSentence(character.name)} the same question twice` : `Ask ${inSentence(character.name)} the same question at another bookmark`}
          </span>
          <span className="q">“{seed.question}”</span>
        </div>
        <div className="compare-grid">
          {early && <Moment label={momentLabel(model, early.position)} name={character.name} reply={early.reply} model={model} />}
          {late ? (
            <Moment label={momentLabel(model, late.position)} name={character.name} reply={late.reply} model={model} later newSince={early.position} />
          ) : (
            <div className="moment later">
              <span className="small" style={{ color: 'var(--color-accent-400)' }}>{momentLabel(model, other)}</span>
              <p className="muted" style={{ margin: 0 }}>
                Move the bookmark on the timeline below, then ask again. {character.name.replace(/^the\s/, 'The ')} will know only what they could know by then.
              </p>
              {notMetYet && (
                <span className="notice">You haven't met {inSentence(character.name)} by this bookmark yet. Move it later.</span>
              )}
              <div>
                <button className="btn" onClick={askAgain} disabled={pending || other === seed.position || notMetYet}>
                  {pending ? `${character.name.replace(/^the\s/, 'The ')} is thinking…` : 'Ask again at this bookmark'}
                </button>
              </div>
              {error && <span className="notice error">{error}</span>}
            </div>
          )}
        </div>
        <div className="timeline">
          <span className="ends tnum">{firstCh ?? '1'}</span>
          <div className="tl">
            <div className="line" />
            {model.chapters.map((c) => <div key={c.paragraphPos} className="tick" style={{ left: pct(c.position) }} />)}
            <div className="span" style={{ left: pct(lo), width: `calc(${pct(hi)} - ${pct(lo)})` }} />
            <div className="knob" style={{ left: pct(seed.position) }} />
            <div className="knob active" style={{ left: pct(other) }} />
            <input
              type="range" min={1} max={max} value={other}
              aria-label="Second bookmark"
              onChange={(e) => {
                // Snap to the end of a spread, as the reader's own bookmark does. Moving
                // back lands on the previous spread, or a step left would snap straight back.
                const value = Number(e.target.value)
                const spread = spreadForPosition(model, value)
                let next = spread.position
                if (value < other && next >= other && spread.index > 0) next = model.spreads[spread.index - 1].position
                setOther(Math.min(max, next))
                setResult(null)
              }}
            />
          </div>
          <span className="ends tnum">{lastCh ?? ''}</span>
          {result ? (
            <>
              <button className="btn quiet" onClick={() => onContinueAt(result.position, seed.question, result.reply)}>Keep talking at the new bookmark</button>
              {early && <button className="btn quiet" onClick={() => onReadFrom(early.position)}>Rewind and read from {earlierChapter ?? 'there'}</button>}
            </>
          ) : null}
        </div>
      </main>
    </div>
  )
}
