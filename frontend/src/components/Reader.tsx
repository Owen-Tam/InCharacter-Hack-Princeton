import { useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, MessageCircle } from 'lucide-react'
import type { Book } from '../api'
import { chapterLabel, pagesLeftInChapter, spreadForPosition, type BookModel, type Page } from '../book'
import { TopBar } from './common'

function PageView({ page, side, number, book }: { page: Page | null; side: 'left' | 'right'; number: number; book: BookModel }) {
  const headings = new Set(book.chapters.map((c) => c.paragraphPos))
  return (
    <div className={`page ${side}`}>
      <div className="page-body">
        {page?.fragments.map((f) => {
          const isHeading = headings.has(f.passage.paragraph_pos)
          return (
            <p key={`${f.passage.id}:${f.lo}`} className={[isHeading ? 'chapter-heading' : f.continued ? '' : 'indent', f.runsOn ? 'runs-on' : ''].join(' ').trim() || undefined}>
              {f.text}
            </p>
          )
        })}
      </div>
      {page && <span className="page-num tnum">{number}</span>}
    </div>
  )
}

export function Reader({ book, model, spreadIndex, onSpread, onTalk, onHome }: {
  book: Book
  model: BookModel
  spreadIndex: number
  onSpread: (index: number) => void
  onTalk: () => void
  onHome: () => void
}) {
  const [fade, setFade] = useState(1)
  // While the progress bar is being dragged: the spread under the thumb, not yet opened.
  const [scrub, setScrub] = useState<number | null>(null)
  const spread = model.spreads[spreadIndex]
  const total = model.pages.length

  function flip(delta: number) {
    const next = spreadIndex + delta
    if (next < 0 || next >= model.spreads.length) return
    setFade(0)
    window.setTimeout(() => {
      onSpread(next)
      setFade(1)
    }, 180)
  }

  function commitScrub() {
    if (scrub === null) return
    if (scrub !== spreadIndex) onSpread(scrub)
    setScrub(null)
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === 'INPUT') return
      if (e.key === 'ArrowRight') flip(1)
      if (e.key === 'ArrowLeft') flip(-1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const last = Math.min(spread.firstPage + 1, total)
  const pagesLabel = last > spread.firstPage ? `Pages ${spread.firstPage}–${last} of ${total}` : `Page ${spread.firstPage} of ${total}`
  const left = pagesLeftInChapter(model, spread)
  const leftLabel = left === 0 ? 'Last pages of the chapter' : `${left} ${left === 1 ? 'page' : 'pages'} left in the chapter`
  const ahead = spread.position > book.indexed_through
  const learnedPage = model.spreads.find((s) => s.position >= book.indexed_through)?.firstPage ?? 1

  return (
    <div className="app">
      <TopBar title={book.title} chapter={chapterLabel(model, spread.position)} onBrand={onHome}>
        <button className="btn" onClick={onTalk}>
          <MessageCircle size={14} strokeWidth={1.6} />
          <span className="label">Talk to a character</span>
        </button>
      </TopBar>
      <main className="reader">
        <div className="stage">
          <button className="round-btn" onClick={() => flip(-1)} disabled={spreadIndex === 0} aria-label="Previous pages">
            <ChevronLeft size={18} strokeWidth={1.6} />
          </button>
          <div className="spread" style={{ opacity: fade, transition: 'opacity 180ms ease' }}>
            <PageView page={spread.pages[0]} side="left" number={spread.firstPage} book={model} />
            <div className="spine" />
            <PageView page={spread.pages[1]} side="right" number={spread.firstPage + 1} book={model} />
            <div className="ribbon" aria-hidden />
          </div>
          <button className="round-btn" onClick={() => flip(1)} disabled={spreadIndex >= model.spreads.length - 1} aria-label="Next pages">
            <ChevronRight size={18} strokeWidth={1.6} />
          </button>
        </div>
        <div className="reader-foot">
          <div className="scrubber">
            <div className="track"><div style={{ width: `${((scrub !== null ? model.spreads[scrub].firstPage + 1 : last) / total) * 100}%` }} /></div>
            {model.chapters.map((c) => (
              <div key={c.paragraphPos} className="scrub-tick" style={{ left: `${(spreadForPosition(model, c.position).firstPage / total) * 100}%` }} />
            ))}
            <input
              type="range" min={0} max={model.spreads.length - 1} value={scrub ?? spreadIndex}
              aria-label="Move through the book"
              aria-valuetext={pagesLabel}
              onChange={(e) => setScrub(Number(e.target.value))}
              onPointerUp={commitScrub}
              onKeyUp={commitScrub}
              onBlur={commitScrub}
            />
          </div>
          <div className="reader-foot-row tnum">
            <span>{pagesLabel}</span>
            {scrub !== null ? (
              <span>
                {[chapterLabel(model, model.spreads[scrub].position), `page ${model.spreads[scrub].firstPage}`].filter(Boolean).join(', ')}
              </span>
            ) : ahead ? (
              <span className="warn">Characters have only learned the story up to page {learnedPage} so far</span>
            ) : (
              <span>{leftLabel}</span>
            )}
          </div>
        </div>
      </main>
    </div>
  )
}
