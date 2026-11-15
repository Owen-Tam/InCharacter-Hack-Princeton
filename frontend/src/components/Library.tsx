import { useEffect, useRef, useState } from 'react'
import { ChevronDown, Plus } from 'lucide-react'
import { api, type Book } from '../api'
import { authorOf, splitTitle } from '../book'
import { SAMPLE_BOOKS } from '../samples'
import { allReadingStates } from '../store'
import { Cover, SettingsMenu } from './common'

function percent(book: Book, position: number) {
  return book.sentences ? Math.round((position / book.sentences) * 100) : 0
}

function statusLine(book: Book): string {
  if (book.status === 'ready') return 'Ready'
  if (book.status === 'failed') return 'Import paused'
  const learned = percent(book, book.indexed_through)
  return learned ? `Learning the cast · ${learned}%` : 'Learning the cast'
}

type SortBy = 'opened' | 'added'
const SORT_KEY = 'incharacter.librarySort.v1'

function savedSort(): SortBy {
  try {
    return localStorage.getItem(SORT_KEY) === 'added' ? 'added' : 'opened'
  } catch {
    return 'opened'
  }
}

const SORT_LABELS: Record<SortBy, string> = { opened: 'Recently opened', added: 'Date added' }
const SORTS = Object.keys(SORT_LABELS) as SortBy[]

/** "Sort by Recently opened ⌄": a small menu in the app's own style. Closes on a
 * choice, Escape or a click elsewhere; arrow keys move between options. */
function SortMenu({ value, onChange }: { value: SortBy; onChange: (next: SortBy) => void }) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const items = useRef<(HTMLButtonElement | null)[]>([])

  useEffect(() => {
    if (!open) return
    items.current[SORTS.indexOf(value)]?.focus()
    const onDown = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('pointerdown', onDown)
    return () => document.removeEventListener('pointerdown', onDown)
  }, [open, value])

  function choose(next: SortBy) {
    onChange(next)
    setOpen(false)
    trigger.current?.focus()
  }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === 'Escape') { setOpen(false); trigger.current?.focus(); return }
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
    e.preventDefault()
    if (!open) { setOpen(true); return }
    const at = items.current.indexOf(document.activeElement as HTMLButtonElement)
    const next = (at + (e.key === 'ArrowDown' ? 1 : -1) + SORTS.length) % SORTS.length
    items.current[next]?.focus()
  }

  return (
    <div className="sorter" ref={root} onKeyDown={onKey}>
      <span className="sorter-label" id="sort-label">Sort by</span>
      <button ref={trigger} className="sorter-button" aria-haspopup="listbox" aria-expanded={open}
        aria-labelledby="sort-label sort-value" onClick={() => setOpen((o) => !o)}>
        <span id="sort-value">{SORT_LABELS[value]}</span>
        <ChevronDown size={13} strokeWidth={1.6} className={open ? 'flip' : undefined} aria-hidden />
      </button>
      {open && (
        <div className="sorter-menu" role="listbox" aria-labelledby="sort-label">
          {SORTS.map((key, i) => (
            <button key={key} ref={(el) => { items.current[i] = el }} role="option" aria-selected={key === value}
              className={key === value ? 'on' : undefined} onClick={() => choose(key)}>
              {SORT_LABELS[key]}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function Library({ books, error, onOpen, onUploaded }: {
  books: Book[] | null
  error: string | null
  onOpen: (bookId: number) => void
  onUploaded: (book: Book) => void
}) {
  const fileInput = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null)
  const reading = allReadingStates()
  const [sortBy, setSortBy] = useState<SortBy>(savedSort)
  function chooseSort(next: SortBy) {
    setSortBy(next)
    try { localStorage.setItem(SORT_KEY, next) } catch { /* storage unavailable */ }
  }
  // Ids count up as books are imported, so they give the order added (newest first).
  // Books never opened here follow the opened ones, newest added first.
  const sorted = books && [...books].sort((a, b) => {
    if (sortBy === 'opened') {
      const diff = (reading[b.id]?.openedAt ?? 0) - (reading[a.id]?.openedAt ?? 0)
      if (diff) return diff
    }
    return b.id - a.id
  })

  const recent = books
    ?.filter((b) => reading[b.id])
    .sort((a, b) => reading[b.id].openedAt - reading[a.id].openedAt)[0]

  async function upload(file: File) {
    setUploading(true)
    setNotice({ text: `Reading ${file.name}…` })
    try {
      const book = await api.upload(file)
      setNotice({ text: `Added “${splitTitle(book.title).title}”. You can read it now; characters arrive as the import learns the story.` })
      onUploaded(book)
    } catch (e) {
      setNotice({ text: (e as Error).message, error: true })
    } finally {
      setUploading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  const addButton = (
    <button className="bar-link accent" onClick={() => fileInput.current?.click()} disabled={uploading}>
      <Plus size={14} strokeWidth={1.6} />
      <span className="label">Add EPUB</span>
    </button>
  )

  return (
    <div className="app">
      <header className="bar plain">
        <span className="brand">InCharacter</span>
        <nav className="bar-actions"><SettingsMenu />{addButton}</nav>
      </header>
      <input ref={fileInput} type="file" accept=".epub,application/epub+zip" hidden
        onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
      <main className="library">
        <h1>Library</h1>
        {notice && <div className={`notice${notice.error ? ' error' : ''}`} role="status">{notice.text}</div>}
        {error && <div className="notice error">{error}</div>}
        {!books && !error && <div className="muted">Opening the shelf…</div>}

        {recent && (
          <section style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            <span className="shelf-label">Continue reading</span>
            <div className="continue">
              <button className="cover-item" style={{ width: 150 }} onClick={() => onOpen(recent.id)} aria-label={`Open ${recent.title}`}>
                <Cover title={recent.title} author={recent.author} large />
              </button>
              <div className="continue-info">
                <button className="continue-title" onClick={() => onOpen(recent.id)}>{splitTitle(recent.title).title}</button>
                <span className="small muted">{authorOf(recent) ?? statusLine(recent)}</span>
                <div className="progress-row">
                  <div className="track"><div style={{ width: `${percent(recent, reading[recent.id].position)}%` }} /></div>
                  <span className="tiny muted tnum">{percent(recent, reading[recent.id].position)}%</span>
                </div>
                <button className="continue-link" onClick={() => onOpen(recent.id)}>
                  Continue on page {reading[recent.id].page} →
                </button>
              </div>
            </div>
          </section>
        )}

        {books && (
          <section className="all-books" style={recent ? undefined : { borderTop: 0, paddingTop: 0 }}>
            <div className="shelf-head">
              <span className="shelf-label">All books</span>
              <SortMenu value={sortBy} onChange={chooseSort} />
            </div>
            <div className="covers">
              {sorted!.map((b) => (
                <button key={b.id} className="cover-item" onClick={() => onOpen(b.id)}>
                  <Cover title={b.title} author={b.author} />
                  <span className="name">{splitTitle(b.title).title}</span>
                  <span className="meta tnum">
                    {reading[b.id] ? `${percent(b, reading[b.id].position)}% read` : b.status === 'ready' ? 'New' : statusLine(b)}
                  </span>
                </button>
              ))}
              {SAMPLE_BOOKS.map((b) => (
                <button key={b.key} className="cover-item sample"
                  onClick={() => setNotice({ text: `“${b.title}” is a sample on the shelf. Add its EPUB to read it and meet the characters.` })}>
                  <Cover title={b.title} author={b.author} />
                  <span className="name">{b.title}</span>
                  <span className="meta">Sample</span>
                </button>
              ))}
              <button className="add-epub" onClick={() => fileInput.current?.click()} disabled={uploading}>
                <Plus size={18} strokeWidth={1.4} />
                <span>{uploading ? 'Adding…' : 'Add EPUB'}</span>
              </button>
            </div>
          </section>
        )}
      </main>
    </div>
  )
}
