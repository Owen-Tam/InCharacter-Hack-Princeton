import { ChevronLeft, ChevronRight, X } from 'lucide-react'
import type { ChatReply, CitationSource } from '../api'
import { chapterLabel, lastSentence, spreadForPassage, type BookModel } from '../book'
import { BASIS } from '../reply'
import { BookmarkRule, Highlighted } from './common'

export type SourceView = { msg: number; note: number } | { msg: number; voice: true }

/** Group a citation's ranges by paragraph, in reading order. */
function byParagraph(sources: CitationSource[]) {
  const groups = new Map<number, { source: CitationSource; ranges: [number, number][] }>()
  for (const s of sources) {
    const g = groups.get(s.passage_id) ?? { source: s, ranges: [] }
    g.ranges.push([s.lo, s.hi])
    if (s.text.length > g.source.text.length) g.source = { ...s }
    groups.set(s.passage_id, g)
  }
  return [...groups.values()].sort((a, b) => a.source.paragraph_pos - b.source.paragraph_pos)
}

export function SourcePanel({ view, reply, model, position, characterName, onNote, onClose, onReadAt }: {
  view: SourceView
  reply: ChatReply
  model: BookModel
  position: number
  characterName: string
  onNote: (note: number) => void
  onClose: () => void
  onReadAt: (passageId: number) => void
}) {
  if ('voice' in view) {
    return (
      <aside className="source" aria-label="Real lines">
        <div className="source-top">
          <span className="label">Real lines · how {characterName} speaks</span>
          <button className="icon-btn" onClick={onClose} aria-label="Close"><X size={16} strokeWidth={1.6} /></button>
        </div>
        <span className="claim">Lines from the book that shaped this voice</span>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {reply.voice_lines.length === 0 && <p className="muted">They haven't spoken yet by your bookmark, so the voice is inferred.</p>}
          {reply.voice_lines.map((line) => (
            <p key={line.utterance_id} className="voice-line">“{line.text}”</p>
          ))}
        </div>
        <BookmarkRule label="Lines end at your bookmark" />
        <div className="source-actions">
          <button className="btn quiet" onClick={onClose}>Back to conversation</button>
        </div>
      </aside>
    )
  }

  const citation = reply.citations[view.note]
  if (!citation) return null
  const groups = byParagraph(citation.sources)
  const first = groups[0]?.source
  const firstPassage = first && model.byId.get(first.passage_id)
  // The paragraph before the cited one, for context, if the reader has finished it.
  const prevPassage = firstPassage && model.passages[model.passages.indexOf(firstPassage) - 1]
  const context = prevPassage && lastSentence(prevPassage) <= position && !groups.some((g) => g.source.passage_id === prevPassage.id)
    ? prevPassage.text : null
  const truncated = groups.some((g) => g.source.truncated)
  const chapter = firstPassage ? chapterLabel(model, firstPassage.first_sentence) : null
  const page = first ? spreadForPassage(model, first.passage_id).firstPage : null
  const count = reply.citations.length

  return (
    <aside className="source" aria-label={`Note ${view.note + 1}`}>
      <div className="source-top">
        <span className="label">Note {view.note + 1} · From the book{chapter ? ` · ${chapter}` : ''}</span>
        {count > 1 && (
          <>
            <button className="icon-btn" onClick={() => onNote(view.note - 1)} disabled={view.note === 0} aria-label="Previous note">
              <ChevronLeft size={16} strokeWidth={1.6} />
            </button>
            <span className="tiny muted tnum">{view.note + 1}/{count}</span>
            <button className="icon-btn" onClick={() => onNote(view.note + 1)} disabled={view.note >= count - 1} aria-label="Next note">
              <ChevronRight size={16} strokeWidth={1.6} />
            </button>
          </>
        )}
        <button className="icon-btn" onClick={onClose} aria-label="Close"><X size={16} strokeWidth={1.6} /></button>
      </div>
      <span className="claim">{citation.claim}</span>
      <span className="basis">{BASIS[citation.basis] ?? citation.basis}</span>
      <div className="source-text">
        {context && <p className="context">{context}</p>}
        {groups.map((g) => (
          <p key={g.source.passage_id}>
            <Highlighted text={g.source.text} ranges={g.ranges} />
          </p>
        ))}
      </div>
      <BookmarkRule label={truncated ? 'Text ends at your bookmark' : undefined} />
      <div className="source-actions">
        {first && page !== null && (
          <button className="btn" onClick={() => onReadAt(first.passage_id)}>Read it on page {page}</button>
        )}
        <button className="btn quiet" onClick={onClose}>Back to conversation</button>
      </div>
    </aside>
  )
}
