import { useEffect, useState } from 'react'
import { ArrowRight, BookOpen } from 'lucide-react'
import { api, type Book, type Character } from '../api'
import { chapterLabel, recentParagraphs, splitTitle, spreadForPosition, type BookModel } from '../book'
import { BookmarkRule, SettingsMenu, TopBar } from './common'

const TITLES = /^(mr|mrs|miss|ms|dr|doctor|old)\.?$/i

/** "in Chapter IV", or "on page 12" in books without chapters. */
function whereIs(model: BookModel, position: number): string {
  const chapter = chapterLabel(model, position)
  return chapter ? `in ${chapter}` : `on page ${spreadForPosition(model, position).firstPage}`
}

/** "also the narrator · met in Chapter I · last heard in Chapter IV". The "also"
 * names leave out ones the shown name already covers ("Nick" under "Nick Carraway"). */
export function describe(c: Character, model: BookModel): string {
  const shown = new Set(c.name.toLowerCase().split(/\s+/))
  const others = c.names.filter((n) => {
    const words = n.toLowerCase().split(/\s+/).filter((w) => !TITLES.test(w))
    return !words.every((w) => shown.has(w))
  })
  const parts = others.length ? [`also ${others.slice(0, 2).join(', ')}`] : []
  const met = whereIs(model, c.first_seen)
  const heard = c.last_heard !== null ? whereIs(model, c.last_heard) : null
  if (heard === met) parts.push(`met and last heard ${met}`)
  else {
    parts.push(`met ${met}`)
    parts.push(heard ? `last heard ${heard}` : 'not heard speaking yet')
  }
  return parts.join(' · ')
}

/** Most present first: lines spoken and facts known, by the bookmark. */
export function byPresence(cast: Character[]): Character[] {
  return [...cast].sort((a, b) => b.lines * 2 + b.facts - (a.lines * 2 + a.facts))
}

export function Cast({ book, model, position, page, onPick, onBack, onHome }: {
  book: Book
  model: BookModel
  position: number // effective bookmark (never past indexed_through)
  page: number
  onPick: (c: Character) => void
  onBack: () => void
  onHome: () => void
}) {
  const [cast, setCast] = useState<Character[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const chapter = chapterLabel(model, position)

  useEffect(() => {
    let live = true
    setCast(null)
    setError(null)
    if (position < 1) {
      setCast([])
      return
    }
    api.characters(book.id, position).then((c) => live && setCast(byPresence(c)), (e) => live && setError(e.message))
    return () => { live = false }
  }, [book.id, position])

  // As much of the page as the conversation screen shows beside the chat.
  const paragraphs = recentParagraphs(model, position, 6)

  return (
    <div className="app">
      <TopBar title={book.title} chapter={chapter} onBrand={onHome}>
        <SettingsMenu />
        <button className="bar-link" onClick={onBack}>
          <BookOpen size={14} strokeWidth={1.6} />
          <span className="label">Back to page {page}</span>
        </button>
      </TopBar>
      <div className="split">
        <aside className="side">
          <div className="left-off">
            <span className="small muted">Where you left off{chapter ? ` · ${chapter}` : ''}</span>
            <div className="left-off-text">{paragraphs.map((p) => <p key={p.passage.id}>{p.text}</p>)}</div>
            <BookmarkRule />
          </div>
          <div className="side-note">
            The main characters appear here once you have met them in the book. Each knows only what they could know by your bookmark.
          </div>
        </aside>
        <main className="cast">
          <span className="kicker">{splitTitle(book.title).title}{chapter ? ` · ${chapter}` : ''}</span>
          <h2>Who will you speak with?</h2>
          <div className="cast-list">
            {error && <div className="empty">{error}</div>}
            {!cast && !error && <div className="empty">Gathering everyone you've met…</div>}
            {cast && cast.length === 0 && (
              <div className="empty">
                {book.indexed_through === 0
                  ? 'The importer is still learning who is in this book. Try again in a minute.'
                  : "You haven't met any of the main characters yet. Read on a little."}
              </div>
            )}
            {cast?.map((c) => (
              <button key={c.id} className="cast-row" onClick={() => onPick(c)}>
                <span className="who">{c.name}</span>
                <span className="desc" title={describe(c, model)}>{describe(c, model)}</span>
                <ArrowRight className="go" size={16} strokeWidth={1.6} />
              </button>
            ))}
          </div>
        </main>
      </div>
    </div>
  )
}
