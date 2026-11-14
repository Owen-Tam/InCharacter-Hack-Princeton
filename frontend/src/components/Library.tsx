import { useRef, useState } from 'react'
import { Plus } from 'lucide-react'
import { api, type Book } from '../api'
import { splitTitle } from '../book'
import { allReadingStates } from '../store'
import { Cover } from './common'

function percent(book: Book, position: number) {
  return book.sentences ? Math.round((position / book.sentences) * 100) : 0
}

function statusLine(book: Book): string {
  if (book.status === 'ready') return 'Ready'
  if (book.status === 'failed') return 'Import paused'
  const learned = percent(book, book.indexed_through)
  return learned ? `Learning the cast · ${learned}%` : 'Learning the cast'
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
        <nav className="bar-actions">{addButton}</nav>
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
                <Cover title={recent.title} large />
              </button>
              <div className="continue-info">
                <button className="continue-title" onClick={() => onOpen(recent.id)}>{splitTitle(recent.title).title}</button>
                <span className="small muted">{splitTitle(recent.title).author ?? statusLine(recent)}</span>
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
            <span className="shelf-label">All books</span>
            <div className="covers">
              {books.map((b) => (
                <button key={b.id} className="cover-item" onClick={() => onOpen(b.id)}>
                  <Cover title={b.title} />
                  <span className="name">{splitTitle(b.title).title}</span>
                  <span className="meta tnum">
                    {reading[b.id] ? `${percent(b, reading[b.id].position)}% read` : b.status === 'ready' ? 'New' : statusLine(b)}
                  </span>
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
