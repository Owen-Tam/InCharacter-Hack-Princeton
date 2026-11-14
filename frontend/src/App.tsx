import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type Book, type ChatReply, type Character, type Passage } from './api'
import { buildBook, pageMetrics, type PageMetrics, spreadForPassage, spreadForPosition, type BookModel } from './book'
import { go, useRoute } from './router'
import { readingState, saveReadingState } from './store'
import { Library } from './components/Library'
import { Reader } from './components/Reader'
import { Cast } from './components/Cast'
import { ConversationScreen, conversationKey, type Conversation } from './components/Conversation'
import { Compare, type CompareSeed } from './components/Compare'
import { TopBar } from './components/common'

const texts = new Map<number, Promise<Passage[]>>()

function loadPassages(bookId: number): Promise<Passage[]> {
  let p = texts.get(bookId)
  if (!p) {
    p = api.passages(bookId)
    p.catch(() => texts.delete(bookId))
    texts.set(bookId, p)
  }
  return p
}

/** Where a book opens the first time: the end of the spread with its first chapter heading. */
function firstBookmark(model: BookModel): number {
  const start = model.chapters[0]
  return start ? spreadForPosition(model, start.position).position : model.spreads[0]?.position ?? 1
}

/** Page geometry for the current window, recomputed on resize and once the body font loads. */
// The body font, loaded once before any page is laid out: measuring the fallback
// font first would lay the book out twice and move every page number.
async function loadBodyFont() {
  if (!document.fonts) return
  // An empty result means the @font-face isn't registered yet: wait for the stylesheet.
  if ((await document.fonts.load('16px Lora')).length === 0) {
    await document.fonts.ready
    await document.fonts.load('16px Lora')
  }
}
const bodyFont: Promise<unknown> = Promise.race([
  loadBodyFont(),
  new Promise((resolve) => window.setTimeout(resolve, 2500)), // offline: use the fallback
]).catch(() => undefined)

/** Page geometry for the current window: null until the body font is ready. */
function useMetrics() {
  const measure = () => pageMetrics(window.innerWidth, window.innerHeight)
  const [metrics, setMetrics] = useState<PageMetrics | null>(null)
  useEffect(() => {
    let t = 0
    let live = true
    const update = () => setMetrics((m) => {
      const next = measure()
      return m && next.charsPerLine === m.charsPerLine && next.lines === m.lines ? m : next
    })
    const onResize = () => { window.clearTimeout(t); t = window.setTimeout(update, 150) }
    bodyFont.then(() => live && update())
    window.addEventListener('resize', onResize)
    return () => { live = false; window.removeEventListener('resize', onResize); window.clearTimeout(t) }
  }, [])
  return metrics
}

export default function App() {
  const route = useRoute()
  const [books, setBooks] = useState<Book[] | null>(null)
  const [booksError, setBooksError] = useState<string | null>(null)
  const [text, setText] = useState<{ id: number; passages: Passage[] } | null>(null)
  const [modelError, setModelError] = useState<string | null>(null)
  // The bookmark per book: the last sentence finished (the end of the open spread).
  const [bookmarks, setBookmarks] = useState<Record<number, number>>({})
  const metrics = useMetrics()
  const [characters, setCharacters] = useState<Record<string, Character>>({})
  const [conversations, setConversations] = useState<Record<string, Conversation>>({})
  const [seed, setSeed] = useState<CompareSeed | null>(null)

  const bookId = route.screen === 'library' ? null : route.bookId
  const book = books?.find((b) => b.id === bookId) ?? null

  const refreshBooks = useCallback(() => {
    api.books().then((b) => { setBooks(b); setBooksError(null) }, (e) => setBooksError(e.message))
  }, [])

  useEffect(refreshBooks, [refreshBooks])

  // While any import is running, keep the shelf and the bookmark limits current.
  const importing = books?.some((b) => b.status !== 'ready')
  useEffect(() => {
    if (!importing) return
    const t = window.setInterval(refreshBooks, 10000)
    return () => window.clearInterval(t)
  }, [importing, refreshBooks])

  useEffect(() => {
    if (bookId === null || text?.id === bookId) return
    let live = true
    setModelError(null)
    loadPassages(bookId).then(
      (passages) => live && setText({ id: bookId, passages }),
      (e) => live && setModelError(e.message),
    )
    return () => { live = false }
  }, [bookId, text?.id])

  const current = useMemo(
    () => (text && text.id === bookId && metrics ? buildBook(text.passages, metrics) : null),
    [text, bookId, metrics],
  )
  const bookmark = bookId !== null && current
    ? bookmarks[bookId] ?? readingState(bookId)?.position ?? firstBookmark(current)
    : null
  const spread = current && bookmark !== null ? spreadForPosition(current, bookmark) : null
  // Chat can only use what the importer has finished.
  const position = spread && book ? Math.min(spread.position, book.indexed_through) : 0
  const page = spread?.firstPage ?? 1

  const setSpread = useCallback((index: number) => {
    if (bookId === null || !current) return
    const sp = current.spreads[index]
    setBookmarks((b) => ({ ...b, [bookId]: sp.position }))
    saveReadingState(bookId, { position: sp.position, page: sp.firstPage })
  }, [bookId, current])

  // Record the visit so the library can offer "continue reading".
  useEffect(() => {
    if (bookId !== null && spread) saveReadingState(bookId, { position: spread.position, page: spread.firstPage })
  }, [bookId, spread])

  const home = () => go({ screen: 'library' })

  if (route.screen === 'library') {
    return <Library books={books} error={booksError} onOpen={(id) => go({ screen: 'read', bookId: id })} onUploaded={refreshBooks} />
  }

  const loadingMessage = booksError ?? modelError ?? (books && !book ? 'That book is not on the shelf.' : null)
  if (!book || !current || !spread) {
    return (
      <div className="app">
        <TopBar onBrand={home} />
        <div className="center-msg">
          <span>{loadingMessage ?? 'Opening the book…'}</span>
          {loadingMessage && <button className="btn" onClick={home}>Back to the library</button>}
        </div>
      </div>
    )
  }

  const backToReader = () => go({ screen: 'read', bookId: book.id })
  const readAtPassage = (passageId: number) => {
    setSpread(spreadForPassage(current, passageId).index)
    backToReader()
  }

  if (route.screen === 'read') {
    return <Reader book={book} model={current} spreadIndex={spread.index} onSpread={setSpread}
      onTalk={() => go({ screen: 'cast', bookId: book.id })} onHome={home} />
  }

  if (route.screen === 'cast') {
    return <Cast book={book} model={current} position={position} page={page} onHome={home} onBack={backToReader}
      onPick={(c) => {
        setCharacters((m) => ({ ...m, [`${book.id}:${c.id}`]: c }))
        go({ screen: 'chat', bookId: book.id, characterId: c.id })
      }} />
  }

  const character = characters[`${book.id}:${route.characterId}`]
  if (!character) {
    return <CharacterLoader bookId={book.id} characterId={route.characterId} position={position}
      onLoaded={(c) => setCharacters((m) => ({ ...m, [`${book.id}:${c.id}`]: c }))} onHome={home} />
  }

  if (route.screen === 'compare') {
    if (!seed) {
      go({ screen: 'chat', bookId: book.id, characterId: character.id })
      return null
    }
    return <Compare book={book} model={current} character={character} seed={seed} page={page} onHome={home}
      onBack={backToReader}
      onBackToChat={() => go({ screen: 'chat', bookId: book.id, characterId: character.id })}
      onReadFrom={(p) => { setSpread(spreadForPosition(current, p).index); backToReader() }}
      onContinueAt={(p, question, reply) => {
        setSpread(spreadForPosition(current, p).index)
        const key = conversationKey(book.id, character.id, p)
        setConversations((all) => ({
          ...all,
          [key]: { key, pending: false, error: null, messages: [{ role: 'reader', text: question }, { role: 'character', question, reply }] },
        }))
        go({ screen: 'chat', bookId: book.id, characterId: character.id })
      }} />
  }

  // A conversation belongs to one character at one bookmark; moving either starts afresh.
  const key = conversationKey(book.id, character.id, position)
  const conversation = conversations[key] ?? { key, messages: [], pending: false, error: null }
  return <ConversationScreen book={book} model={current} position={position} page={page} character={character}
    conversation={conversation}
    onUpdate={(c) => setConversations((all) => ({ ...all, [c.key]: c }))}
    onBack={backToReader}
    onChangeCharacter={() => go({ screen: 'cast', bookId: book.id })}
    onCompare={(question: string, reply: ChatReply) => {
      setSeed({ question, reply, position })
      go({ screen: 'compare', bookId: book.id, characterId: character.id })
    }}
    onReadAt={readAtPassage}
    onHome={home} />
}

function CharacterLoader({ bookId, characterId, position, onLoaded, onHome }: {
  bookId: number
  characterId: number
  position: number
  onLoaded: (c: Character) => void
  onHome: () => void
}) {
  const [missing, setMissing] = useState<string | null>(null)
  useEffect(() => {
    api.characters(bookId, position).then((cast) => {
      const c = cast.find((x) => x.id === characterId)
      if (c) onLoaded(c)
      else setMissing("You haven't met this character by your bookmark.")
    }, (e) => setMissing(e.message))
  }, [bookId, characterId, position, onLoaded])
  return (
    <div className="app">
      <TopBar onBrand={onHome} />
      <div className="center-msg">
        <span>{missing ?? 'Finding them…'}</span>
        {missing && <button className="btn" onClick={() => go({ screen: 'cast', bookId })}>Choose someone you've met</button>}
      </div>
    </div>
  )
}
